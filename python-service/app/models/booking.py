from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean, ForeignKey,
    Enum, Text, Index, UniqueConstraint, CheckConstraint, Numeric
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
import enum

from app.core.database import Base


class BookingStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    RESCHEDULED = "rescheduled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class ConversationState(str, enum.Enum):
    GREETING = "greeting"
    SERVICE_SELECTION = "service_selection"
    DATE_SELECTION = "date_selection"
    TIME_SELECTION = "time_selection"
    STAFF_SELECTION = "staff_selection"
    CONFIRMATION = "confirmation"
    COMPLETED = "completed"
    RESCHEDULING = "rescheduling"
    CANCELLING = "cancelling"
    ESCALATED = "escalated"


class Tenant(Base):
    """One row per salon in multi-tenant mode."""
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    calcom_api_key = Column(String(500), nullable=False)
    calcom_base_url = Column(String(500), nullable=False)
    whatsapp_phone_number_id = Column(String(100))
    timezone = Column(String(100), default="America/New_York")
    settings = Column(JSONB, default={})
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    customers = relationship("Customer", back_populates="tenant")
    staff = relationship("Staff", back_populates="tenant")
    services = relationship("Service", back_populates="tenant")
    appointments = relationship("Appointment", back_populates="tenant")


class Customer(Base):
    __tablename__ = "customers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    phone_number = Column(String(20), nullable=False)
    name = Column(String(255))
    email = Column(String(255))
    language_preference = Column(String(10), default="en")
    notes = Column(Text)
    gdpr_consent = Column(Boolean, default=False)
    gdpr_consent_date = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "phone_number", name="uq_customer_tenant_phone"),
        Index("ix_customer_phone", "phone_number"),
        Index("ix_customer_tenant", "tenant_id"),
    )

    tenant = relationship("Tenant", back_populates="customers")
    appointments = relationship("Appointment", back_populates="customer")
    conversations = relationship("Conversation", back_populates="customer")


class Staff(Base):
    __tablename__ = "staff"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    calcom_user_id = Column(Integer)  # Cal.com user ID
    name = Column(String(255), nullable=False)
    email = Column(String(255))
    phone = Column(String(20))
    specializations = Column(JSONB, default=[])  # list of service IDs
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_staff_tenant", "tenant_id"),
    )

    tenant = relationship("Tenant", back_populates="staff")
    appointments = relationship("Appointment", back_populates="staff")
    availability = relationship("StaffAvailability", back_populates="staff")
    holidays = relationship("StaffHoliday", back_populates="staff")


class Service(Base):
    __tablename__ = "services"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    calcom_event_type_id = Column(Integer)  # Cal.com event type ID
    name = Column(String(255), nullable=False)
    description = Column(Text)
    duration_minutes = Column(Integer, nullable=False)
    buffer_before_minutes = Column(Integer, default=0)
    buffer_after_minutes = Column(Integer, default=15)
    price = Column(Numeric(10, 2))
    max_concurrent_bookings = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)

    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="ck_service_duration_positive"),
        Index("ix_service_tenant", "tenant_id"),
    )

    tenant = relationship("Tenant", back_populates="services")
    appointments = relationship("Appointment", back_populates="service")


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    service_id = Column(UUID(as_uuid=True), ForeignKey("services.id"), nullable=False)
    staff_id = Column(UUID(as_uuid=True), ForeignKey("staff.id"), nullable=False)

    # Cal.com references
    calcom_booking_id = Column(Integer, unique=True)
    calcom_booking_uid = Column(String(100), unique=True)

    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    timezone = Column(String(100), nullable=False)
    status = Column(Enum(BookingStatus), default=BookingStatus.PENDING, nullable=False)

    # Optimistic concurrency control
    version = Column(Integer, default=1, nullable=False)

    notes = Column(Text)
    cancellation_reason = Column(Text)
    reminder_sent = Column(Boolean, default=False)
    confirmation_sent = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        Index("ix_appointment_staff_time", "staff_id", "start_time", "end_time"),
        Index("ix_appointment_customer", "customer_id"),
        Index("ix_appointment_tenant_status", "tenant_id", "status"),
        Index("ix_appointment_start_time", "start_time"),
        # Prevent overlapping bookings for same staff at DB level
        # Use application-level lock + this index together
    )

    tenant = relationship("Tenant", back_populates="appointments")
    customer = relationship("Customer", back_populates="appointments")
    service = relationship("Service", back_populates="appointments")
    staff = relationship("Staff", back_populates="appointments")


class StaffAvailability(Base):
    """Weekly recurring availability windows per staff member."""
    __tablename__ = "staff_availability"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    staff_id = Column(UUID(as_uuid=True), ForeignKey("staff.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)  # 0=Monday, 6=Sunday
    start_time = Column(String(5), nullable=False)  # "09:00"
    end_time = Column(String(5), nullable=False)    # "18:00"
    is_active = Column(Boolean, default=True)

    __table_args__ = (
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_availability_dow"),
        Index("ix_availability_staff", "staff_id"),
    )

    staff = relationship("Staff", back_populates="availability")


class StaffHoliday(Base):
    """One-off days off or vacation blocks."""
    __tablename__ = "staff_holidays"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    staff_id = Column(UUID(as_uuid=True), ForeignKey("staff.id"), nullable=False)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    reason = Column(String(255))

    staff = relationship("Staff", back_populates="holidays")


class Conversation(Base):
    """Tracks WhatsApp conversation state per customer."""
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    state = Column(Enum(ConversationState), default=ConversationState.GREETING)
    context = Column(JSONB, default={})  # accumulated slot data
    messages = Column(JSONB, default=[])  # last N messages for LLM context
    pending_appointment_id = Column(UUID(as_uuid=True), ForeignKey("appointments.id"), nullable=True)
    last_activity_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_conversation_customer", "customer_id"),
        Index("ix_conversation_last_activity", "last_activity_at"),
    )

    customer = relationship("Customer", back_populates="conversations")


class AuditLog(Base):
    """Immutable audit trail for GDPR and compliance."""
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True))
    actor_type = Column(String(50))  # "customer" | "staff" | "system" | "ai"
    actor_id = Column(String(100))
    action = Column(String(100), nullable=False)
    resource_type = Column(String(100))
    resource_id = Column(String(100))
    payload = Column(JSONB)
    ip_address = Column(String(45))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_audit_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_resource", "resource_type", "resource_id"),
    )
