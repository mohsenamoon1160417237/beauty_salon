# System Architecture

## Component Diagram

```
Customer (WhatsApp)
        │
        │ HTTPS (TLS 1.3)
        ▼
┌─────────────────────────────────────────────────────────────┐
│  NGINX (reverse proxy + rate limiting + security headers)    │
└───────────────────────────┬────────────────────┬────────────┘
                            │                    │
                    /webhook/*              cal.domain/*
                            │                    │
                    ┌───────▼──────┐    ┌────────▼────────┐
                    │     n8n      │    │   Cal DIY        │
                    │  (Workflow   │    │  (Cal.com self-  │
                    │  Orches-     │    │   hosted)        │
                    │  tration)    │    │                  │
                    │              │    │  - Event types   │
                    │  5 Workflows │    │  - Staff cals    │
                    │  + AI Agent  │    │  - Availability  │
                    │  (Claude)    │    │  - Email notifs  │
                    └──────┬───────┘    └────────▲─────────┘
                           │                     │
                    HTTP (internal network)       │
                           │                     │
                    ┌──────▼───────────────────┐ │
                    │   Python FastAPI          │ │
                    │   Scheduling Service      ├─┘
                    │                           │
                    │  • Conflict detection     │
                    │  • Redis distributed lock │
                    │  • Cal.com API wrapper    │
                    │  • Atomic booking ops     │
                    └──────┬──────────┬─────────┘
                           │          │
              ┌────────────▼───┐  ┌───▼────────────┐
              │  PostgreSQL 16  │  │  Redis 7        │
              │                 │  │                 │
              │  - appointments │  │  - Conv sessions│
              │  - customers    │  │  - Dist. locks  │
              │  - staff        │  │  - n8n queues   │
              │  - services     │  │  - Rate limits  │
              │  - audit_logs   │  │                 │
              └─────────────────┘  └─────────────────┘
```

## Component Responsibilities

| Component | Responsibility | Does NOT do |
|-----------|---------------|-------------|
| **n8n** | Message routing, AI agent orchestration, tool dispatch, reminder cron | Business logic, DB writes |
| **Claude (LLM)** | NLU, language detection, slot extraction, conversation state | Directly check DB, book appointments |
| **Python FastAPI** | Scheduling logic, conflict detection, locking, Cal.com calls, atomic writes | UI, conversation management |
| **Cal.com (Cal DIY)** | Source of truth for calendar, availability rules, email confirmations, ICS generation | WhatsApp communication |
| **PostgreSQL** | Persistent state, audit log, exclusion constraint backstop | Caching, sessions |
| **Redis** | Distributed locks (prevent double booking), conversation sessions (24h TTL), n8n job queue | Persistent data |
| **Nginx** | TLS termination, rate limiting, network isolation, security headers | Application logic |

## Conflict Prevention — Defense in Depth

```
Layer 1: Redis distributed lock (30s TTL, 5 retries)
         → Prevents concurrent requests racing for same slot

Layer 2: PostgreSQL re-check inside lock transaction
         → Catches any edge case that slipped through layer 1

Layer 3: PostgreSQL GiST exclusion constraint
         → DB-level absolute backstop; raises exception if overlap
            somehow got through layers 1+2

Layer 4: Cal.com own validation
         → Cal.com also validates slot availability before creating booking
```

## Concurrency Strategy Comparison

| Strategy | Pros | Cons | Chosen? |
|----------|------|------|---------|
| DB pessimistic lock (SELECT FOR UPDATE) | Simple | Doesn't work across connection pools (PgBouncer) | No |
| Optimistic concurrency (version field) | No blocking | Customer sees retries, UX cost | Partial (version field present for future) |
| **Redis distributed lock** | Cross-process, cross-replica, TTL-safe | Requires Redis | **YES — primary** |
| DB exclusion constraint | Absolute backstop | Only fires at INSERT, after the race | YES — backstop |

## Data Flow: Booking a New Appointment

```
1. Customer sends WhatsApp: "I want a haircut Saturday at 10am"
2. Meta Cloud API → n8n webhook
3. n8n: verify HMAC signature, extract phone + text
4. n8n: load conversation session from Redis
5. n8n: call Claude with full context + tool definitions
6. Claude: detect intent=BOOK, extract service=haircut, date=Saturday, time=10am
7. Claude: call check_availability tool
8. n8n: HTTP GET /api/v1/availability → Python service
9. Python: fetch slots from Cal.com, filter against DB conflicts + holidays
10. Python: return available slots as JSON
11. Claude: present 2-3 options to customer in WhatsApp message
12. Customer: "Yes, 10am with Maria"
13. Claude: call book_appointment tool (only after explicit confirmation)
14. n8n: HTTP POST /api/v1/book → Python service
15. Python: acquire Redis lock for staff_id+start_time
16. Python: re-check DB for conflicts (under lock)
17. Python: POST /v2/bookings → Cal.com API
18. Cal.com: creates booking, sends email confirmation, updates calendar
19. Python: mirror to local appointments table
20. Python: release Redis lock
21. Python: return BookingResponse with confirmation_message
22. n8n: update conversation session in Redis
23. n8n: send confirmation message to customer via WhatsApp
```

## Multi-Tenant SaaS Architecture (100+ Salons)

```
                    ┌─────────────────────────────────┐
                    │   Kubernetes Cluster             │
                    │                                  │
                    │  ┌─────────────────────────┐    │
                    │  │  booking-api (HPA)       │    │
                    │  │  min=2, max=20 replicas  │    │
                    │  └────────────┬────────────┘    │
                    │               │                  │
                    │  ┌────────────▼────────────┐    │
                    │  │  PgBouncer (pool=200)    │    │
                    │  └────────────┬────────────┘    │
                    │               │                  │
                    │  ┌────────────▼────────────┐    │
                    │  │  PostgreSQL              │    │
                    │  │  (Primary + 2 Replicas) │    │
                    │  └─────────────────────────┘    │
                    │                                  │
                    │  ┌─────────────────────────┐    │
                    │  │  Redis Cluster (3 shards)│    │
                    │  └─────────────────────────┘    │
                    │                                  │
                    │  Per-tenant Cal.com instances    │
                    │  in separate namespaces          │
                    └─────────────────────────────────┘

Tenant isolation:
- All DB tables have tenant_id column + RLS policies
- Cal.com API keys stored encrypted per tenant in tenants table
- WhatsApp phone numbers mapped to tenant_id at webhook entry
- Redis keys prefixed with tenant_id
```
