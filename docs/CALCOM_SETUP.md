# Cal.com (Cal DIY) Integration Guide

## Why Cal DIY as Backend

Cal DIY (self-hosted Cal.com) gives you:
- Full calendar management with conflict detection
- Automated email confirmations and reminders (fallback to WhatsApp)
- ICS calendar invites for customers
- Staff availability rules and working hours
- API v2 for programmatic booking
- No SaaS subscription cost

## Event Type Setup

Create one Cal.com Event Type per salon service:

| Service | Duration | Slug | Buffer After |
|---------|----------|------|-------------|
| Haircut | 30min | `haircut` | 15min |
| Hair Coloring | 120min | `hair-coloring` | 15min |
| Hair Styling | 60min | `hair-styling` | 10min |
| Nail Services | 45min | `nail-services` | 10min |
| Facial Treatment | 60min | `facial` | 15min |
| Eyebrow Services | 30min | `eyebrows` | 5min |
| Makeup | 60min | `makeup` | 10min |

### Via Cal.com API v2

```bash
# Create an event type
curl -X POST https://cal.yourdomain.com/api/v2/event-types \
  -H "Authorization: Bearer $CALCOM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Haircut",
    "slug": "haircut",
    "lengthInMinutes": 30,
    "afterEventBuffer": 15,
    "requiresConfirmation": false,
    "metadata": {}
  }'
```

## Availability API

```bash
# Get available slots for event type ID 1
curl "https://cal.yourdomain.com/api/v2/slots/available?\
eventTypeId=1&\
startTime=2025-01-15T00:00:00Z&\
endTime=2025-01-22T00:00:00Z&\
timeZone=America/New_York" \
  -H "Authorization: Bearer $CALCOM_API_KEY"
```

Response:
```json
{
  "status": "success",
  "data": {
    "slots": {
      "2025-01-15": [
        { "time": "2025-01-15T09:00:00-05:00" },
        { "time": "2025-01-15T09:30:00-05:00" }
      ]
    }
  }
}
```

## Booking API

```bash
# Create booking
curl -X POST https://cal.yourdomain.com/api/v2/bookings \
  -H "Authorization: Bearer $CALCOM_API_KEY" \
  -H "cal-api-version: 2024-08-13" \
  -H "Content-Type: application/json" \
  -d '{
    "eventTypeId": 1,
    "start": "2025-01-15T09:00:00-05:00",
    "attendee": {
      "name": "Sarah Johnson",
      "email": "sarah@example.com",
      "timeZone": "America/New_York",
      "phoneNumber": "+12125551234",
      "language": "en"
    }
  }'
```

## Webhook Events to Subscribe

| Event | Our Use |
|-------|---------|
| `BOOKING_CREATED` | Sync to local DB |
| `BOOKING_CANCELLED` | Update status + notify customer |
| `BOOKING_RESCHEDULED` | Update times + notify customer |
| `BOOKING_REMINDER` | Optional: use Cal.com reminders instead of ours |

## Staff Configuration

Each staff member needs:
1. A Cal.com account (created under your organization)
2. Availability schedule (e.g., Mon-Sat 9am-7pm)
3. Connected to event types they perform
4. Time blocked for breaks (lunch, etc.)

The `calcom_user_id` stored in our `staff` table maps to Cal.com's user ID,
allowing per-staff availability queries.
