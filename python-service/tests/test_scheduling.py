"""
Unit tests for the scheduling engine.
Focus: conflict detection, lock behavior, booking flow.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.scheduling import SchedulingService
from app.schemas.booking import BookingRequest, ConflictCheckRequest
from app.models.booking import BookingStatus, Appointment


TENANT_ID = uuid4()
STAFF_ID = uuid4()
SERVICE_ID = uuid4()
CUSTOMER_PHONE = "+12125551234"


def make_dt(offset_hours: int = 3) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=offset_hours)


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.fixture
def mock_calcom():
    client = AsyncMock()
    client.create_booking = AsyncMock(return_value=MagicMock(
        id=42,
        uid="cal_uid_abc123",
        start_time=make_dt(3),
        end_time=make_dt(4),
        status="accepted",
    ))
    return client


@pytest.fixture
def scheduling_service(mock_db, mock_calcom):
    svc = SchedulingService(
        db=mock_db,
        tenant_calcom_url="http://calcom:3000",
        tenant_api_key="test-key",
    )
    svc.calcom = mock_calcom
    return svc


class TestConflictDetection:

    @pytest.mark.asyncio
    async def test_no_conflict_when_no_existing_bookings(self, scheduling_service, mock_db):
        mock_db.execute.return_value.scalars.return_value.first.return_value = None

        req = ConflictCheckRequest(
            staff_id=STAFF_ID,
            start_time=make_dt(3),
            end_time=make_dt(4),
        )
        result = await scheduling_service.check_conflict(req)
        assert result.has_conflict is False

    @pytest.mark.asyncio
    async def test_conflict_detected_with_overlapping_booking(self, scheduling_service, mock_db):
        existing = MagicMock(spec=Appointment)
        existing.status = BookingStatus.CONFIRMED
        mock_db.execute.return_value.scalars.return_value.first.return_value = existing

        req = ConflictCheckRequest(
            staff_id=STAFF_ID,
            start_time=make_dt(3),
            end_time=make_dt(4),
        )
        result = await scheduling_service.check_conflict(req)
        assert result.has_conflict is True


class TestBooking:

    @pytest.mark.asyncio
    async def test_booking_succeeds_when_slot_is_free(self, scheduling_service, mock_db, mock_calcom):
        # No conflict
        mock_db.execute.return_value.scalars.return_value.first.return_value = None

        # Service mock
        service_mock = MagicMock()
        service_mock.duration_minutes = 60
        service_mock.calcom_event_type_id = 1
        service_mock.name = "Haircut"
        mock_db.get.side_effect = lambda model, id: service_mock if model.__name__ == "Service" else MagicMock(name="Maria")

        req = BookingRequest(
            tenant_id=TENANT_ID,
            customer_phone=CUSTOMER_PHONE,
            customer_name="Jane Doe",
            customer_email="jane@example.com",
            service_id=SERVICE_ID,
            staff_id=STAFF_ID,
            start_time=make_dt(3),
            timezone="America/New_York",
        )

        with patch("app.services.scheduling.DistributedLock") as mock_lock:
            mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await scheduling_service.book_appointment(req)

        assert result.status == "confirmed"
        mock_calcom.create_booking.assert_called_once()

    @pytest.mark.asyncio
    async def test_booking_fails_if_slot_taken_under_lock(self, scheduling_service, mock_db):
        # First call (pre-lock): free; second call (under lock): conflict
        existing = MagicMock(spec=Appointment)
        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            m = MagicMock()
            m.scalars.return_value.first.return_value = existing if call_count > 1 else None
            return m

        mock_db.execute.side_effect = side_effect

        service_mock = MagicMock()
        service_mock.duration_minutes = 60
        service_mock.calcom_event_type_id = 1
        service_mock.name = "Haircut"
        mock_db.get.return_value = service_mock

        req = BookingRequest(
            tenant_id=TENANT_ID,
            customer_phone=CUSTOMER_PHONE,
            customer_name="Jane Doe",
            service_id=SERVICE_ID,
            staff_id=STAFF_ID,
            start_time=make_dt(3),
            timezone="America/New_York",
        )

        with patch("app.services.scheduling.DistributedLock") as mock_lock:
            mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(ValueError, match="no longer available"):
                await scheduling_service.book_appointment(req)

    @pytest.mark.asyncio
    async def test_booking_requires_min_advance_hours(self, scheduling_service, mock_db):
        mock_db.execute.return_value.scalars.return_value.first.return_value = None
        service_mock = MagicMock()
        service_mock.duration_minutes = 30
        service_mock.calcom_event_type_id = 1
        service_mock.name = "Haircut"
        mock_db.get.return_value = service_mock

        req = BookingRequest(
            tenant_id=TENANT_ID,
            customer_phone=CUSTOMER_PHONE,
            customer_name="Jane",
            service_id=SERVICE_ID,
            staff_id=STAFF_ID,
            start_time=make_dt(0.5),  # only 30 minutes ahead
            timezone="America/New_York",
        )

        with patch("app.services.scheduling.DistributedLock") as mock_lock:
            mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(ValueError, match="2 hours in advance"):
                await scheduling_service.book_appointment(req)
