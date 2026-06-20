"""
Cal.com webhook receiver.
Cal.com calls this when bookings are created, rescheduled, or cancelled directly
in the Cal.com UI (staff-initiated changes). We sync those back to our DB.
"""

import hmac
import hashlib
import logging
from fastapi import APIRouter, Request, HTTPException, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.database import get_db
from app.core.config import settings
from app.models.booking import Appointment, BookingStatus

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


async def verify_calcom_signature(
    request: Request,
    x_cal_signature_256: str = Header(None),
) -> bytes:
    body = await request.body()
    if not x_cal_signature_256:
        raise HTTPException(status_code=401, detail="Missing signature")

    expected = "sha256=" + hmac.new(
        settings.CALCOM_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, x_cal_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")

    return body


@router.post("/calcom")
async def calcom_webhook(
    request: Request,
    body: bytes = Depends(verify_calcom_signature),
    db: AsyncSession = Depends(get_db),
):
    import json
    payload = json.loads(body)
    trigger = payload.get("triggerEvent")
    booking = payload.get("payload", {})
    uid = booking.get("uid")

    if not uid:
        return {"status": "ignored"}

    result = await db.execute(
        select(Appointment).where(Appointment.calcom_booking_uid == uid)
    )
    appt = result.scalars().first()

    if not appt:
        logger.warning("Cal.com webhook for unknown booking uid=%s trigger=%s", uid, trigger)
        return {"status": "not_found"}

    status_map = {
        "BOOKING_CANCELLED": BookingStatus.CANCELLED,
        "BOOKING_RESCHEDULED": BookingStatus.RESCHEDULED,
        "BOOKING_CONFIRMED": BookingStatus.CONFIRMED,
    }

    if trigger in status_map:
        appt.status = status_map[trigger]
        await db.commit()
        logger.info("Synced Cal.com event %s for booking %s", trigger, uid)

    return {"status": "ok"}
