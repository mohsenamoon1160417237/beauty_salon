from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.database import get_db
from app.schemas.booking import (
    AvailabilityRequest, AvailabilityResponse,
    BookingRequest, BookingResponse,
    RescheduleRequest, CancellationRequest,
    ConflictCheckRequest, ConflictCheckResponse,
    ServiceInfo, StaffInfo,
)
from app.services.scheduling import SchedulingService
from app.core.config import settings
from app.models.booking import Service, Staff
from sqlalchemy import select, and_
from typing import List
from uuid import UUID

router = APIRouter(prefix="/api/v1", tags=["scheduling"])


def get_scheduling_service(db: AsyncSession = Depends(get_db)) -> SchedulingService:
    # In multi-tenant mode this would resolve per-tenant credentials
    return SchedulingService(
        db=db,
        tenant_calcom_url=settings.CALCOM_BASE_URL,
        tenant_api_key=settings.CALCOM_API_KEY,
    )


@router.get("/availability", response_model=AvailabilityResponse)
async def get_availability(
    tenant_id: UUID,
    service_id: UUID,
    date_from: str,
    date_to: str,
    timezone: str = "UTC",
    staff_id: UUID = None,
    svc: SchedulingService = Depends(get_scheduling_service),
):
    """
    Returns available time slots for a given service and optional staff member.
    Used by the AI agent to present options to the customer.
    """
    from datetime import datetime
    try:
        req = AvailabilityRequest(
            tenant_id=tenant_id,
            service_id=service_id,
            staff_id=staff_id,
            date_from=datetime.fromisoformat(date_from),
            date_to=datetime.fromisoformat(date_to),
            timezone=timezone,
        )
        return await svc.get_availability(req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/book", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def book_appointment(
    req: BookingRequest,
    svc: SchedulingService = Depends(get_scheduling_service),
):
    """
    Atomically books an appointment.
    Acquires distributed lock → checks conflict → creates in Cal.com → mirrors to DB.
    Returns 409 if the slot was taken between availability check and booking.
    """
    try:
        return await svc.book_appointment(req)
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Booking system is busy. Please try again in a moment.",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/reschedule", response_model=BookingResponse)
async def reschedule_appointment(
    req: RescheduleRequest,
    svc: SchedulingService = Depends(get_scheduling_service),
):
    try:
        return await svc.reschedule_appointment(req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post("/cancel")
async def cancel_appointment(
    req: CancellationRequest,
    svc: SchedulingService = Depends(get_scheduling_service),
):
    try:
        return await svc.cancel_appointment(req)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/check-conflict", response_model=ConflictCheckResponse)
async def check_conflict(
    req: ConflictCheckRequest,
    svc: SchedulingService = Depends(get_scheduling_service),
):
    """Pre-flight conflict check before presenting a slot to the customer."""
    return await svc.check_conflict(req)


@router.get("/services", response_model=List[ServiceInfo])
async def list_services(
    tenant_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Service).where(
            and_(Service.tenant_id == tenant_id, Service.is_active == True)
        )
    )
    services = result.scalars().all()
    return [
        ServiceInfo(
            id=s.id,
            name=s.name,
            description=s.description,
            duration_minutes=s.duration_minutes,
            price=float(s.price) if s.price else None,
            buffer_after_minutes=s.buffer_after_minutes,
        )
        for s in services
    ]


@router.get("/staff", response_model=List[StaffInfo])
async def list_staff(
    tenant_id: UUID,
    service_id: UUID = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Staff).where(
        and_(Staff.tenant_id == tenant_id, Staff.is_active == True)
    )
    result = await db.execute(q)
    staff_list = result.scalars().all()
    return [
        StaffInfo(
            id=s.id,
            name=s.name,
            specializations=s.specializations or [],
        )
        for s in staff_list
    ]
