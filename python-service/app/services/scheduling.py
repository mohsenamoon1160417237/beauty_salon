"""
Core scheduling engine — standalone mode (no Cal.com dependency).

Concurrency strategy:
  1. Acquire Redis slot lock (30s TTL, 5 retries with exponential backoff)
  2. Re-check DB for conflicts inside the lock (eliminates TOCTOU race)
  3. Write to DB atomically inside a transaction
  4. Release lock

This gives us: no double bookings, sub-second conflict detection, horizontal scale.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select, and_, or_, func, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import DistributedLock, slot_lock_key
from app.models.booking import (
    Appointment, BookingStatus, Customer, Service, Staff, StaffHoliday
)
from app.schemas.booking import (
    AvailabilityRequest, AvailabilityResponse, TimeSlot,
    BookingRequest, BookingResponse, RescheduleRequest, CancellationRequest,
    ConflictCheckRequest, ConflictCheckResponse,
)

logger = logging.getLogger(__name__)


class SchedulingService:

    def __init__(self, db: AsyncSession):
        self.db = db

    # ─────────────────────────── Availability ────────────────────────────

    async def get_availability(self, req: AvailabilityRequest) -> AvailabilityResponse:
        service = await self._get_service(req.service_id)
        if not service:
            raise ValueError(f"Service {req.service_id} not found")

        if req.staff_id:
            staff_list = [await self._get_staff(req.staff_id)]
        else:
            staff_list = await self._get_active_staff(req.tenant_id)

        tz = ZoneInfo(req.timezone)
        available_slots: List[TimeSlot] = []

        # Generate slots every 30 minutes across the requested date range
        current = req.date_from.astimezone(tz)
        end_range = req.date_to.astimezone(tz)

        while current < end_range:
            slot_end = current + timedelta(minutes=service.duration_minutes)

            for staff in staff_list:
                if not staff or not staff.is_active:
                    continue
                if await self._is_on_holiday(staff.id, current, slot_end):
                    continue
                conflict = await self._has_booking_conflict(staff.id, current, slot_end)
                available_slots.append(TimeSlot(
                    start=current,
                    end=slot_end,
                    staff_id=staff.id,
                    staff_name=staff.name,
                    available=not conflict,
                ))

            current += timedelta(minutes=30)

        return AvailabilityResponse(
            slots=[s for s in available_slots if s.available],
            timezone=req.timezone,
            service_duration_minutes=service.duration_minutes,
        )

    # ─────────────────────────── Booking ─────────────────────────────────

    async def book_appointment(self, req: BookingRequest) -> BookingResponse:
        service = await self._get_service(req.service_id)
        staff = await self._get_staff(req.staff_id)
        if not service or not staff:
            raise ValueError("Service or staff not found")

        end_time = req.start_time + timedelta(minutes=service.duration_minutes)
        lock_key = slot_lock_key(str(req.staff_id), req.start_time.isoformat())

        async with DistributedLock(lock_key):
            if await self._has_booking_conflict(req.staff_id, req.start_time, end_time):
                raise ValueError(
                    f"Time slot {req.start_time} is no longer available for {staff.name}"
                )

            min_advance = timedelta(hours=2)
            if req.start_time - datetime.now(req.start_time.tzinfo) < min_advance:
                raise ValueError("Appointments must be booked at least 2 hours in advance")

            customer = await self._upsert_customer(
                tenant_id=req.tenant_id,
                phone=req.customer_phone,
                name=req.customer_name,
                email=req.customer_email,
                language=req.language,
            )

            booking_ref = str(uuid.uuid4())[:8].upper()

            appointment = Appointment(
                tenant_id=req.tenant_id,
                customer_id=customer.id,
                service_id=req.service_id,
                staff_id=req.staff_id,
                calcom_booking_uid=booking_ref,
                start_time=req.start_time,
                end_time=end_time,
                timezone=req.timezone,
                status=BookingStatus.CONFIRMED,
            )
            self.db.add(appointment)
            await self.db.commit()
            await self.db.refresh(appointment)

        return BookingResponse(
            appointment_id=appointment.id,
            calcom_booking_uid=booking_ref,
            customer_name=req.customer_name,
            service_name=service.name,
            staff_name=staff.name,
            start_time=req.start_time,
            end_time=end_time,
            timezone=req.timezone,
            status="confirmed",
            confirmation_message=(
                f"✅ Your {service.name} appointment with {staff.name} is confirmed!\n"
                f"📅 {req.start_time.strftime('%A, %B %d at %I:%M %p')} ({req.timezone})\n"
                f"⏱ Duration: {service.duration_minutes} minutes\n"
                f"Ref: {booking_ref}"
            ),
        )

    # ─────────────────────────── Reschedule ──────────────────────────────

    async def reschedule_appointment(self, req: RescheduleRequest) -> BookingResponse:
        appointment = await self._get_appointment(req.appointment_id)
        if not appointment:
            raise ValueError("Appointment not found")
        if appointment.status == BookingStatus.CANCELLED:
            raise ValueError("Cannot reschedule a cancelled appointment")

        service = await self._get_service(appointment.service_id)
        staff = await self._get_staff(appointment.staff_id)
        new_end = req.new_start_time + timedelta(minutes=service.duration_minutes)
        lock_key = slot_lock_key(str(appointment.staff_id), req.new_start_time.isoformat())

        async with DistributedLock(lock_key):
            if await self._has_booking_conflict(
                appointment.staff_id, req.new_start_time, new_end, exclude_id=appointment.id
            ):
                raise ValueError("New time slot is not available")

            appointment.start_time = req.new_start_time
            appointment.end_time = new_end
            appointment.status = BookingStatus.RESCHEDULED
            appointment.version += 1
            await self.db.commit()

        customer = await self.db.get(Customer, appointment.customer_id)
        return BookingResponse(
            appointment_id=appointment.id,
            calcom_booking_uid=appointment.calcom_booking_uid,
            customer_name=customer.name,
            service_name=service.name,
            staff_name=staff.name,
            start_time=req.new_start_time,
            end_time=new_end,
            timezone=req.timezone,
            status="rescheduled",
            confirmation_message=(
                f"✅ Your appointment has been rescheduled!\n"
                f"📅 New time: {req.new_start_time.strftime('%A, %B %d at %I:%M %p')} ({req.timezone})\n"
                f"Ref: {appointment.calcom_booking_uid}"
            ),
        )

    # ─────────────────────────── Cancellation ────────────────────────────

    async def cancel_appointment(self, req: CancellationRequest) -> dict:
        appointment = await self._get_appointment(req.appointment_id)
        if not appointment:
            raise ValueError("Appointment not found")

        customer = await self.db.get(Customer, appointment.customer_id)
        if customer.phone_number != req.customer_phone:
            raise PermissionError("Phone number does not match appointment record")

        if appointment.status == BookingStatus.CANCELLED:
            return {"status": "already_cancelled"}

        appointment.status = BookingStatus.CANCELLED
        appointment.cancellation_reason = req.reason
        await self.db.commit()

        return {
            "status": "cancelled",
            "message": "Your appointment has been cancelled. We hope to see you again soon! 💙",
        }

    # ─────────────────────────── Conflict check ──────────────────────────

    async def check_conflict(self, req: ConflictCheckRequest) -> ConflictCheckResponse:
        has_conflict = await self._has_booking_conflict(
            req.staff_id, req.start_time, req.end_time, req.exclude_appointment_id
        )
        return ConflictCheckResponse(
            has_conflict=has_conflict,
            conflicting_appointments=[],
        )

    # ─────────────────────────── Private helpers ─────────────────────────

    async def _has_booking_conflict(
        self,
        staff_id: UUID,
        start: datetime,
        end: datetime,
        exclude_id: Optional[UUID] = None,
    ) -> bool:
        from sqlalchemy import text, literal_column
        q = select(Appointment).where(
            and_(
                Appointment.staff_id == staff_id,
                Appointment.status.cast(String).in_(["confirmed", "pending"]),
                or_(
                    and_(Appointment.start_time < end, Appointment.end_time > start),
                ),
            )
        )
        if exclude_id:
            q = q.where(Appointment.id != exclude_id)
        result = await self.db.execute(q)
        return result.scalars().first() is not None

    async def _is_on_holiday(self, staff_id: UUID, start: datetime, end: datetime) -> bool:
        q = select(StaffHoliday).where(
            and_(
                StaffHoliday.staff_id == staff_id,
                StaffHoliday.start_date < end,
                StaffHoliday.end_date > start,
            )
        )
        result = await self.db.execute(q)
        return result.scalars().first() is not None

    async def _get_service(self, service_id: UUID) -> Optional[Service]:
        return await self.db.get(Service, service_id)

    async def _get_staff(self, staff_id: UUID) -> Optional[Staff]:
        return await self.db.get(Staff, staff_id)

    async def _get_appointment(self, appointment_id: UUID) -> Optional[Appointment]:
        return await self.db.get(Appointment, appointment_id)

    async def _get_active_staff(self, tenant_id: UUID) -> List[Staff]:
        result = await self.db.execute(
            select(Staff).where(
                and_(Staff.tenant_id == tenant_id, Staff.is_active == True)
            )
        )
        return result.scalars().all()

    async def _upsert_customer(
        self, tenant_id: UUID, phone: str, name: str,
        email: Optional[str], language: str
    ) -> Customer:
        result = await self.db.execute(
            select(Customer).where(
                and_(Customer.tenant_id == tenant_id, Customer.phone_number == phone)
            )
        )
        customer = result.scalars().first()
        if customer:
            customer.name = name or customer.name
            customer.email = email or customer.email
        else:
            customer = Customer(
                tenant_id=tenant_id,
                phone_number=phone,
                name=name,
                email=email,
                language_preference=language,
                gdpr_consent=True,
                gdpr_consent_date=datetime.utcnow(),
            )
            self.db.add(customer)
        await self.db.flush()
        return customer
