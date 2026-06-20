"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-01-01 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Run the raw SQL schema file
    op.execute("""
    CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
    """)

    op.execute("""
    CREATE EXTENSION IF NOT EXISTS "btree_gist";
    """)

    op.execute("""
    DO $$ BEGIN
        CREATE TYPE booking_status AS ENUM (
            'pending', 'confirmed', 'cancelled', 'rescheduled', 'completed', 'no_show'
        );
    EXCEPTION WHEN duplicate_object THEN null;
    END $$;
    """)

    op.execute("""
    DO $$ BEGIN
        CREATE TYPE conversation_state AS ENUM (
            'greeting', 'service_selection', 'date_selection', 'time_selection',
            'staff_selection', 'confirmation', 'completed', 'rescheduling', 'cancelling', 'escalated'
        );
    EXCEPTION WHEN duplicate_object THEN null;
    END $$;
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS tenants (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        name VARCHAR(255) NOT NULL,
        slug VARCHAR(100) NOT NULL UNIQUE,
        calcom_api_key TEXT NOT NULL,
        calcom_base_url TEXT NOT NULL,
        whatsapp_phone_number_id VARCHAR(100),
        timezone VARCHAR(100) NOT NULL DEFAULT 'America/New_York',
        settings JSONB NOT NULL DEFAULT '{}',
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        phone_number VARCHAR(20) NOT NULL,
        name VARCHAR(255),
        email VARCHAR(255),
        language_preference VARCHAR(10) NOT NULL DEFAULT 'en',
        notes TEXT,
        gdpr_consent BOOLEAN NOT NULL DEFAULT FALSE,
        gdpr_consent_date TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ,
        CONSTRAINT uq_customer_tenant_phone UNIQUE (tenant_id, phone_number)
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS staff (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        calcom_user_id INTEGER,
        name VARCHAR(255) NOT NULL,
        email VARCHAR(255),
        phone VARCHAR(20),
        specializations JSONB NOT NULL DEFAULT '[]',
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS services (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        calcom_event_type_id INTEGER,
        name VARCHAR(255) NOT NULL,
        description TEXT,
        duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
        buffer_before_minutes INTEGER NOT NULL DEFAULT 0,
        buffer_after_minutes INTEGER NOT NULL DEFAULT 15,
        price NUMERIC(10,2),
        max_concurrent_bookings INTEGER NOT NULL DEFAULT 1,
        is_active BOOLEAN NOT NULL DEFAULT TRUE
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS appointments (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        customer_id UUID NOT NULL REFERENCES customers(id),
        service_id UUID NOT NULL REFERENCES services(id),
        staff_id UUID NOT NULL REFERENCES staff(id),
        calcom_booking_id INTEGER UNIQUE,
        calcom_booking_uid VARCHAR(100) UNIQUE,
        start_time TIMESTAMPTZ NOT NULL,
        end_time TIMESTAMPTZ NOT NULL,
        timezone VARCHAR(100) NOT NULL,
        status booking_status NOT NULL DEFAULT 'pending',
        version INTEGER NOT NULL DEFAULT 1,
        notes TEXT,
        cancellation_reason TEXT,
        reminder_sent BOOLEAN NOT NULL DEFAULT FALSE,
        confirmation_sent BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ,
        CONSTRAINT ck_appointment_times CHECK (end_time > start_time)
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS staff_availability (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        staff_id UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
        day_of_week SMALLINT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
        start_time VARCHAR(5) NOT NULL,
        end_time VARCHAR(5) NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT TRUE
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS staff_holidays (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        staff_id UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
        start_date TIMESTAMPTZ NOT NULL,
        end_date TIMESTAMPTZ NOT NULL,
        reason VARCHAR(255),
        CONSTRAINT ck_holiday_dates CHECK (end_date > start_date)
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS conversations (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        customer_id UUID NOT NULL REFERENCES customers(id),
        tenant_id UUID NOT NULL REFERENCES tenants(id),
        state conversation_state NOT NULL DEFAULT 'greeting',
        context JSONB NOT NULL DEFAULT '{}',
        messages JSONB NOT NULL DEFAULT '[]',
        pending_appointment_id UUID REFERENCES appointments(id),
        last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        tenant_id UUID,
        actor_type VARCHAR(50),
        actor_id VARCHAR(100),
        action VARCHAR(100) NOT NULL,
        resource_type VARCHAR(100),
        resource_id VARCHAR(100),
        payload JSONB,
        ip_address VARCHAR(45),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    # Indexes
    op.execute("CREATE INDEX IF NOT EXISTS ix_customer_phone ON customers (phone_number);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_customer_tenant ON customers (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_staff_tenant ON staff (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_service_tenant ON services (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_appointment_staff_time ON appointments (staff_id, start_time, end_time);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_appointment_customer ON appointments (customer_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_appointment_tenant_status ON appointments (tenant_id, status);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_appointment_start_time ON appointments (start_time);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_conversation_customer ON conversations (customer_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_tenant_created ON audit_logs (tenant_id, created_at);")

    # Exclusion constraint to prevent overlapping bookings
    op.execute("""
    DO $$ BEGIN
        ALTER TABLE appointments
            ADD CONSTRAINT no_overlapping_bookings
            EXCLUDE USING gist (
                staff_id WITH =,
                tstzrange(start_time, end_time, '[)') WITH &&
            )
            WHERE (status IN ('confirmed', 'pending'));
    EXCEPTION WHEN duplicate_object THEN null;
    END $$;
    """)

    # Seed default tenant
    op.execute("""
    INSERT INTO tenants (id, name, slug, calcom_api_key, calcom_base_url, timezone)
    VALUES (
        '00000000-0000-0000-0000-000000000001',
        'Glamour Beauty Salon',
        'glamour',
        'placeholder-api-key',
        'http://calcom:3000',
        'America/New_York'
    ) ON CONFLICT DO NOTHING;
    """)

    # Seed services
    op.execute("""
    INSERT INTO services (tenant_id, name, description, duration_minutes, buffer_after_minutes, price)
    VALUES
        ('00000000-0000-0000-0000-000000000001', 'Haircut', 'Professional haircut and styling', 30, 15, 45.00),
        ('00000000-0000-0000-0000-000000000001', 'Hair Coloring', 'Full color treatment', 120, 15, 120.00),
        ('00000000-0000-0000-0000-000000000001', 'Hair Styling', 'Blow dry and style', 60, 10, 65.00),
        ('00000000-0000-0000-0000-000000000001', 'Nail Services', 'Manicure and pedicure', 45, 10, 50.00),
        ('00000000-0000-0000-0000-000000000001', 'Facial Treatment', 'Deep cleansing facial', 60, 15, 80.00),
        ('00000000-0000-0000-0000-000000000001', 'Eyebrow Services', 'Threading and shaping', 30, 5, 25.00),
        ('00000000-0000-0000-0000-000000000001', 'Makeup', 'Full makeup application', 60, 10, 90.00)
    ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_logs CASCADE;")
    op.execute("DROP TABLE IF EXISTS conversations CASCADE;")
    op.execute("DROP TABLE IF EXISTS staff_holidays CASCADE;")
    op.execute("DROP TABLE IF EXISTS staff_availability CASCADE;")
    op.execute("DROP TABLE IF EXISTS appointments CASCADE;")
    op.execute("DROP TABLE IF EXISTS services CASCADE;")
    op.execute("DROP TABLE IF EXISTS staff CASCADE;")
    op.execute("DROP TABLE IF EXISTS customers CASCADE;")
    op.execute("DROP TABLE IF EXISTS tenants CASCADE;")
    op.execute("DROP TYPE IF EXISTS booking_status;")
    op.execute("DROP TYPE IF EXISTS conversation_state;")
