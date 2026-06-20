"""
Cal.com (Cal DIY) API v2 integration layer.

All Cal.com interactions go through this class.
The scheduling service calls this; nothing else does.
"""

import httpx
import hmac
import hashlib
from datetime import datetime
from typing import Optional, List
from dataclasses import dataclass
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class CalcomSlot:
    time: datetime
    attendees: int


@dataclass
class CalcomBooking:
    id: int
    uid: str
    title: str
    start_time: datetime
    end_time: datetime
    status: str
    attendee_email: Optional[str]
    attendee_name: Optional[str]


class CalcomClient:
    """
    Thin wrapper around Cal.com API v2.
    Uses per-tenant API keys in multi-tenant mode.
    """

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "cal-api-version": "2024-08-13",
                },
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def get_available_slots(
        self,
        event_type_id: int,
        start_time: str,
        end_time: str,
        timezone: str = "UTC",
    ) -> List[CalcomSlot]:
        """Fetch available slots for an event type within a date range."""
        client = self._get_client()
        resp = await client.get(
            f"/v2/slots/available",
            params={
                "eventTypeId": event_type_id,
                "startTime": start_time,
                "endTime": end_time,
                "timeZone": timezone,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        slots = []
        for date_str, slot_list in data.get("data", {}).get("slots", {}).items():
            for slot in slot_list:
                slots.append(
                    CalcomSlot(
                        time=datetime.fromisoformat(slot["time"].replace("Z", "+00:00")),
                        attendees=slot.get("attendees", 0),
                    )
                )
        return slots

    async def create_booking(
        self,
        event_type_id: int,
        start: str,
        attendee_name: str,
        attendee_email: str,
        attendee_phone: str,
        timezone: str,
        notes: Optional[str] = None,
        language: str = "en",
    ) -> CalcomBooking:
        """Create a booking in Cal.com."""
        client = self._get_client()
        payload = {
            "eventTypeId": event_type_id,
            "start": start,
            "attendee": {
                "name": attendee_name,
                "email": attendee_email,
                "timeZone": timezone,
                "phoneNumber": attendee_phone,
                "language": language,
            },
            "bookingFieldsResponses": {
                "notes": notes or "",
            },
        }
        resp = await client.post("/v2/bookings", json=payload)
        resp.raise_for_status()
        data = resp.json()["data"]
        return self._parse_booking(data)

    async def reschedule_booking(
        self,
        booking_uid: str,
        new_start: str,
        reason: Optional[str] = None,
    ) -> CalcomBooking:
        """Reschedule an existing booking."""
        client = self._get_client()
        resp = await client.post(
            f"/v2/bookings/{booking_uid}/reschedule",
            json={
                "start": new_start,
                "reschedulingReason": reason or "Customer request",
            },
        )
        resp.raise_for_status()
        return self._parse_booking(resp.json()["data"])

    async def cancel_booking(
        self,
        booking_uid: str,
        reason: Optional[str] = None,
    ) -> bool:
        """Cancel a booking. Returns True on success."""
        client = self._get_client()
        resp = await client.delete(
            f"/v2/bookings/{booking_uid}/cancel",
            json={"cancellationReason": reason or "Customer request"},
        )
        resp.raise_for_status()
        return resp.json().get("status") == "success"

    async def get_booking(self, booking_uid: str) -> Optional[CalcomBooking]:
        client = self._get_client()
        try:
            resp = await client.get(f"/v2/bookings/{booking_uid}")
            resp.raise_for_status()
            return self._parse_booking(resp.json()["data"])
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_event_types(self) -> List[dict]:
        client = self._get_client()
        resp = await client.get("/v2/event-types")
        resp.raise_for_status()
        return resp.json().get("data", {}).get("eventTypeGroups", [])

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @staticmethod
    def _parse_booking(data: dict) -> CalcomBooking:
        attendee = (data.get("attendees") or [{}])[0]
        return CalcomBooking(
            id=data["id"],
            uid=data["uid"],
            title=data.get("title", ""),
            start_time=datetime.fromisoformat(data["start"].replace("Z", "+00:00")),
            end_time=datetime.fromisoformat(data["end"].replace("Z", "+00:00")),
            status=data.get("status", ""),
            attendee_email=attendee.get("email"),
            attendee_name=attendee.get("name"),
        )

    @staticmethod
    def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
        """Verify Cal.com webhook signature (HMAC-SHA256)."""
        expected = hmac.new(
            secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(f"sha256={expected}", signature)


def get_calcom_client(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> CalcomClient:
    return CalcomClient(
        base_url=base_url or settings.CALCOM_BASE_URL,
        api_key=api_key or settings.CALCOM_API_KEY,
    )
