# Deployment Guide

## Prerequisites

- Docker ≥ 24 + Docker Compose v2
- Domain with DNS A-records pointing to your server
- Meta Business account with WhatsApp Business API access
- Cal.com API key (generated inside Cal DIY after first boot)

---

## 1. Server Setup

```bash
# Ubuntu 22.04 — recommended
apt update && apt install -y docker.io docker-compose-plugin certbot

# TLS certificates
certbot certonly --standalone \
  -d n8n.yourdomain.com \
  -d cal.yourdomain.com \
  --email you@yourdomain.com \
  --agree-tos
```

---

## 2. Clone and Configure

```bash
git clone https://github.com/your-org/salon-booking-agent
cd salon-booking-agent

cp .env.example .env
# Edit .env — fill in ALL required values
# Generate secrets:
openssl rand -base64 32   # POSTGRES_PASSWORD, N8N_ENCRYPTION_KEY, API_SECRET_KEY
openssl rand -base64 24   # CALCOM_ENCRYPTION_KEY (exactly 32 chars after trim)
```

---

## 3. Start the Stack

```bash
cd docker
docker compose up -d

# Wait for Cal.com to be healthy (~60s first boot)
docker compose logs -f calcom

# Run DB migrations
docker compose exec booking-api alembic upgrade head
```

---

## 4. Cal.com Initial Setup

1. Open https://cal.yourdomain.com
2. Create admin account
3. Go to Settings → Developer → API Keys → Create key
4. Paste key into `.env` as `CALCOM_API_KEY`
5. Create Event Types matching your services:
   - Haircut (30min), Hair Coloring (120min), etc.
6. Add staff members as Cal.com users
7. Configure webhook: Settings → Developer → Webhooks
   - URL: `https://your-booking-api/webhooks/calcom`
   - Events: `BOOKING_CREATED`, `BOOKING_CANCELLED`, `BOOKING_RESCHEDULED`
   - Secret: value from `CALCOM_WEBHOOK_SECRET`

---

## 5. Meta WhatsApp Business Setup

1. Go to [developers.facebook.com](https://developers.facebook.com)
2. Create a new App → Business type
3. Add WhatsApp product
4. Get phone number ID and access token → paste into `.env`
5. Set webhook:
   - URL: `https://n8n.yourdomain.com/webhook/whatsapp-incoming`
   - Verify token: value from `WHATSAPP_VERIFY_TOKEN`
   - Subscribe to: `messages`

---

## 6. n8n Workflow Import

```bash
# n8n auto-loads workflows from the mounted volume
# Or import manually:
docker compose exec n8n n8n import:workflow \
  --input=/home/node/.n8n/workflows/01-incoming-whatsapp.json
```

In n8n UI:
1. Go to Settings → Credentials
2. Add credentials:
   - **Anthropic API**: paste your API key
   - **Redis**: host=redis, password from `.env`
   - **Postgres**: use internal hostname `postgres`
   - **HTTP Header Auth**: Authorization = `Bearer <API_SECRET_KEY>`
3. Activate all 5 workflows

---

## 7. Seed Services into DB

```bash
docker compose exec booking-api python -c "
import asyncio
from app.core.database import get_db_context
# Schema seed already runs from 001_schema.sql on first boot
# To update service calcom_event_type_id values:
print('Update service rows with Cal.com event type IDs via psql or admin UI')
"

# Via psql:
docker compose exec postgres psql -U salon salon_booking -c "
UPDATE services SET calcom_event_type_id = 1 WHERE name = 'Haircut';
-- repeat for each service
"
```

---

## 8. Production Hardening Checklist

- [ ] All `.env` secrets are strong (32+ char random)
- [ ] Booking API not exposed publicly (nginx config enforces this)
- [ ] `N8N_HOST` restricted to VPN for UI access
- [ ] `POSTGRES_PASSWORD` rotated from example
- [ ] Sentry DSN configured for error alerting
- [ ] Redis password set
- [ ] TLS certificates auto-renew: `certbot renew --quiet` in cron
- [ ] Daily postgres backups: `pg_dump` → S3 or Backblaze B2
- [ ] Cal.com webhook secret matches `CALCOM_WEBHOOK_SECRET`

---

## Scaling

### 10 Salons (multi-tenant)

```yaml
# Add tenant rows to DB
# Each tenant gets its own Cal.com instance or uses the same
# with separate API keys stored in tenants table
# booking-api resolves credentials per tenant_id from DB

booking-api:
  deploy:
    replicas: 4
```

### 100 Salons (SaaS)

```
Architecture change:
  - One Cal.com per tenant (Docker Compose or K8s namespace)
  - PgBouncer in front of PostgreSQL
  - Redis Cluster (3 shards)
  - booking-api on Kubernetes (HPA on CPU)
  - n8n in queue mode with multiple worker replicas
  - Cloudflare for DDoS + rate limiting at edge
```

---

## Twilio Fallback

If Meta API is unavailable, switch to Twilio in n8n:
1. Activate the `01-incoming-whatsapp-twilio.json` workflow
2. Deactivate the Meta workflow
3. Update Twilio webhook URL to point to n8n

---

## Monitoring

```bash
# View logs
docker compose logs -f booking-api
docker compose logs -f n8n

# Check appointment conflicts (should always be 0)
docker compose exec postgres psql -U salon salon_booking -c "
SELECT COUNT(*) FROM appointments a1
JOIN appointments a2 ON (
  a1.staff_id = a2.staff_id
  AND a1.id != a2.id
  AND a1.start_time < a2.end_time
  AND a1.end_time > a2.start_time
  AND a1.status IN ('confirmed','pending')
  AND a2.status IN ('confirmed','pending')
);
"
```
