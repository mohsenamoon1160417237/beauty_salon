-- ============================================================
-- Salon Booking Agent — PostgreSQL Schema
-- ============================================================
-- Design decisions:
--   - UUID primary keys: avoid sequential ID guessing, works multi-region
--   - JSONB for flexible fields (settings, context, messages): avoids
--     over-normalizing fields that change per tenant
--   - Partial indexes on status/active columns: most queries filter on these
--   - Explicit CHECK constraints: enforce invariants at DB level as last resort
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "btree_gist"; -- needed for exclusion constraints

-- ─────────────────────────── Enums ──────────────────────────
CREATE TYPE booking_status AS ENUM (
  'pending', 'confirmed', 'cancelled', 'rescheduled', 'completed', 'no_show'
);

CREATE TYPE conversation_state AS ENUM (
  'greeting', 'service_selection', 'date_selection', 'time_selection',
  'staff_selection', 'confirmation', 'completed', 'rescheduling', 'cancelling', 'escalated'
);

-- ─────────────────────────── Tenants ────────────────────────
CREATE TABLE tenants (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name            VARCHAR(255) NOT NULL,
  slug            VARCHAR(100) NOT NULL UNIQUE,
  calcom_api_key  TEXT NOT NULL,
  calcom_base_url TEXT NOT NULL,
  whatsapp_phone_number_id VARCHAR(100),
  timezone        VARCHAR(100) NOT NULL DEFAULT 'America/New_York',
  settings        JSONB NOT NULL DEFAULT '{}',
  is_active       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ
);

CREATE INDEX ix_tenants_slug ON tenants (slug);
CREATE INDEX ix_tenants_active ON tenants (is_active) WHERE is_active = TRUE;

-- ─────────────────────────── Customers ──────────────────────
CREATE TABLE customers (
  id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  phone_number        VARCHAR(20) NOT NULL,
  name                VARCHAR(255),
  email               VARCHAR(255),
  language_preference VARCHAR(10) NOT NULL DEFAULT 'en',
  notes               TEXT,
  gdpr_consent        BOOLEAN NOT NULL DEFAULT FALSE,
  gdpr_consent_date   TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ,
  CONSTRAINT uq_customer_tenant_phone UNIQUE (tenant_id, phone_number)
);

CREATE INDEX ix_customer_phone ON customers (phone_number);
CREATE INDEX ix_customer_tenant ON customers (tenant_id);

-- ─────────────────────────── Staff ──────────────────────────
CREATE TABLE staff (
  id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  calcom_user_id   INTEGER,
  name             VARCHAR(255) NOT NULL,
  email            VARCHAR(255),
  phone            VARCHAR(20),
  specializations  JSONB NOT NULL DEFAULT '[]',  -- array of service IDs
  is_active        BOOLEAN NOT NULL DEFAULT TRUE,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_staff_tenant ON staff (tenant_id);
CREATE INDEX ix_staff_active ON staff (tenant_id, is_active) WHERE is_active = TRUE;

-- ─────────────────────────── Services ───────────────────────
CREATE TABLE services (
  id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  calcom_event_type_id    INTEGER,
  name                    VARCHAR(255) NOT NULL,
  description             TEXT,
  duration_minutes        INTEGER NOT NULL CHECK (duration_minutes > 0),
  buffer_before_minutes   INTEGER NOT NULL DEFAULT 0 CHECK (buffer_before_minutes >= 0),
  buffer_after_minutes    INTEGER NOT NULL DEFAULT 15 CHECK (buffer_after_minutes >= 0),
  price                   NUMERIC(10,2) CHECK (price >= 0),
  max_concurrent_bookings INTEGER NOT NULL DEFAULT 1 CHECK (max_concurrent_bookings > 0),
  is_active               BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX ix_service_tenant ON services (tenant_id);
CREATE INDEX ix_service_active ON services (tenant_id, is_active) WHERE is_active = TRUE;

-- ─────────────────────────── Appointments ───────────────────
CREATE TABLE appointments (
  id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  customer_id         UUID NOT NULL REFERENCES customers(id),
  service_id          UUID NOT NULL REFERENCES services(id),
  staff_id            UUID NOT NULL REFERENCES staff(id),
  calcom_booking_id   INTEGER UNIQUE,
  calcom_booking_uid  VARCHAR(100) UNIQUE,
  start_time          TIMESTAMPTZ NOT NULL,
  end_time            TIMESTAMPTZ NOT NULL,
  timezone            VARCHAR(100) NOT NULL,
  status              booking_status NOT NULL DEFAULT 'pending',
  version             INTEGER NOT NULL DEFAULT 1,   -- optimistic concurrency
  notes               TEXT,
  cancellation_reason TEXT,
  reminder_sent       BOOLEAN NOT NULL DEFAULT FALSE,
  confirmation_sent   BOOLEAN NOT NULL DEFAULT FALSE,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ,
  CONSTRAINT ck_appointment_times CHECK (end_time > start_time)
);

-- Core query indexes
CREATE INDEX ix_appointment_staff_time   ON appointments (staff_id, start_time, end_time);
CREATE INDEX ix_appointment_customer     ON appointments (customer_id);
CREATE INDEX ix_appointment_tenant_status ON appointments (tenant_id, status);
CREATE INDEX ix_appointment_start_time   ON appointments (start_time);

-- Partial index for active bookings only (most conflict queries filter here)
CREATE INDEX ix_appointment_active ON appointments (staff_id, start_time, end_time)
  WHERE status IN ('confirmed', 'pending');

-- Reminder query index
CREATE INDEX ix_appointment_reminders ON appointments (start_time, reminder_sent)
  WHERE status = 'confirmed' AND reminder_sent = FALSE;

-- ──────────── Exclusion constraint: prevent overlapping bookings ────────────
-- This is the DB-level backstop. The app-level Redis lock fires first.
-- GiST index required (btree_gist extension above).
ALTER TABLE appointments
  ADD CONSTRAINT no_overlapping_bookings
  EXCLUDE USING gist (
    staff_id WITH =,
    tstzrange(start_time, end_time, '[)') WITH &&
  )
  WHERE (status IN ('confirmed', 'pending'));

-- ─────────────────────────── Staff Availability ─────────────
CREATE TABLE staff_availability (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  staff_id     UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  day_of_week  SMALLINT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
  start_time   VARCHAR(5) NOT NULL,   -- "09:00"
  end_time     VARCHAR(5) NOT NULL,   -- "18:00"
  is_active    BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX ix_availability_staff ON staff_availability (staff_id);

-- ─────────────────────────── Staff Holidays ─────────────────
CREATE TABLE staff_holidays (
  id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  staff_id   UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  start_date TIMESTAMPTZ NOT NULL,
  end_date   TIMESTAMPTZ NOT NULL,
  reason     VARCHAR(255),
  CONSTRAINT ck_holiday_dates CHECK (end_date > start_date)
);

CREATE INDEX ix_holiday_staff ON staff_holidays (staff_id, start_date, end_date);

-- ─────────────────────────── Conversations ──────────────────
CREATE TABLE conversations (
  id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  customer_id           UUID NOT NULL REFERENCES customers(id),
  tenant_id             UUID NOT NULL REFERENCES tenants(id),
  state                 conversation_state NOT NULL DEFAULT 'greeting',
  context               JSONB NOT NULL DEFAULT '{}',  -- collected slot data
  messages              JSONB NOT NULL DEFAULT '[]',  -- last 20 messages for LLM
  pending_appointment_id UUID REFERENCES appointments(id),
  last_activity_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_conversation_customer      ON conversations (customer_id);
CREATE INDEX ix_conversation_last_activity ON conversations (last_activity_at);

-- ─────────────────────────── Audit Log ──────────────────────
CREATE TABLE audit_logs (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id     UUID,
  actor_type    VARCHAR(50),   -- 'customer' | 'staff' | 'system' | 'ai'
  actor_id      VARCHAR(100),
  action        VARCHAR(100) NOT NULL,
  resource_type VARCHAR(100),
  resource_id   VARCHAR(100),
  payload       JSONB,
  ip_address    VARCHAR(45),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Append-only: no UPDATEs or DELETEs on audit_logs
CREATE INDEX ix_audit_tenant_created ON audit_logs (tenant_id, created_at);
CREATE INDEX ix_audit_resource       ON audit_logs (resource_type, resource_id);

-- ─────────────────────────── Seed data ──────────────────────
INSERT INTO tenants (id, name, slug, calcom_api_key, calcom_base_url, timezone)
VALUES (
  '00000000-0000-0000-0000-000000000001',
  'Glamour Beauty Salon',
  'glamour',
  'placeholder-api-key',
  'http://calcom:3000',
  'America/New_York'
) ON CONFLICT DO NOTHING;

INSERT INTO services (tenant_id, name, description, duration_minutes, buffer_after_minutes, price)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'Haircut',          'Professional haircut and styling', 30, 15, 45.00),
  ('00000000-0000-0000-0000-000000000001', 'Hair Coloring',    'Full color treatment', 120, 15, 120.00),
  ('00000000-0000-0000-0000-000000000001', 'Hair Styling',     'Blow dry and style', 60, 10, 65.00),
  ('00000000-0000-0000-0000-000000000001', 'Nail Services',    'Manicure and pedicure', 45, 10, 50.00),
  ('00000000-0000-0000-0000-000000000001', 'Facial Treatment', 'Deep cleansing facial', 60, 15, 80.00),
  ('00000000-0000-0000-0000-000000000001', 'Eyebrow Services', 'Threading and shaping', 30, 5, 25.00),
  ('00000000-0000-0000-0000-000000000001', 'Makeup',           'Full makeup application', 60, 10, 90.00)
ON CONFLICT DO NOTHING;
