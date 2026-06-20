from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime
from uuid import UUID
import re


class AvailabilityRequest(BaseModel):
    tenant_id: UUID
    service_id: UUID
    staff_id: Optional[UUID] = None
    date_from: datetime
    date_to: datetime
    timezone: str = "UTC"

    @field_validator("date_to")
    @classmethod
    def date_to_after_from(cls, v, info):
        if "date_from" in info.data and v <= info.data["date_from"]:
            raise ValueError("date_to must be after date_from")
        return v


class TimeSlot(BaseModel):
    start: datetime
    end: datetime
    staff_id: UUID
    staff_name: str
    available: bool


class AvailabilityResponse(BaseModel):
    slots: List[TimeSlot]
    timezone: str
    service_duration_minutes: int


class BookingRequest(BaseModel):
    tenant_id: UUID
    customer_phone: str = Field(..., pattern=r"^\+[1-9]\d{1,14}$")
    customer_name: str = Field(..., min_length=1, max_length=255)
    customer_email: Optional[str] = None
    service_id: UUID
    staff_id: UUID
    start_time: datetime
    timezone: str = "UTC"
    notes: Optional[str] = None
    language: str = "en"


class BookingResponse(BaseModel):
    appointment_id: UUID
    calcom_booking_uid: Optional[str]
    customer_name: str
    service_name: str
    staff_name: str
    start_time: datetime
    end_time: datetime
    timezone: str
    status: str
    confirmation_message: str


class RescheduleRequest(BaseModel):
    appointment_id: UUID
    new_start_time: datetime
    timezone: str = "UTC"
    reason: Optional[str] = None


class CancellationRequest(BaseModel):
    appointment_id: UUID
    reason: Optional[str] = None
    customer_phone: str  # for authorization check


class ServiceInfo(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    duration_minutes: int
    price: Optional[float]
    buffer_after_minutes: int


class StaffInfo(BaseModel):
    id: UUID
    name: str
    specializations: List[str]


class ConflictCheckRequest(BaseModel):
    staff_id: UUID
    start_time: datetime
    end_time: datetime
    exclude_appointment_id: Optional[UUID] = None  # for rescheduling


class ConflictCheckResponse(BaseModel):
    has_conflict: bool
    conflicting_appointments: List[dict]
