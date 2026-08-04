# Notification Service

A backend service for sending notifications to users across Email, SMS, and Push channels, with priority queueing, automatic retries, delivery tracking, idempotency, and per-user rate limiting.

## Overview

Clients POST a notification request (a user, one or more channels, a priority, and either a stored template or raw content). The service persists the request, resolves which channels the user actually accepts (respecting their opt-outs), and fans it out to a Redis-backed priority queue. A separate worker process picks jobs up — critical/high priority first — sends through the appropriate (mocked) channel provider, and tracks per-channel delivery status with automatic exponential-backoff retries on failure.

Third-party providers (email/SMS/push) are mocked per the assignment scope — the focus is the service architecture around them (queueing, retries, tracking, idempotency), not real provider integration.

## Tech Stack & Rationale

| Component | Choice | Why |
|---|---|---|
| Web framework | **FastAPI** | Async-ready, built-in request validation via Pydantic, and free OpenAPI/Swagger docs generation — directly satisfies the "API documentation" requirement with near-zero extra work. |
| Database | **PostgreSQL** (via SQLAlchemy 2.0 + Alembic) | Relational fits the schema well (notifications → per-channel delivery → attempt audit log, all with real foreign keys). Alembic gives real, reviewable migrations rather than just `create_all()`. |
| Queue | **Redis + RQ** | RQ gives real priority queues (named queues consumed in priority order) and a built-in delayed-job scheduler (used for backoff retries) with much less operational complexity than Celery. See DESIGN.md for the full trade-off discussion. |
| Testing | **pytest** + SQLite (in-memory) + fakeredis | Fast, hermetic test suite that needs no external services to run. Real Postgres/Redis are used in local dev and docker-compose; see "Test Strategy" below for why this split is safe here. |
| Logging | **python-json-logger** | Structured JSON logs (notification_id, channel, status, etc. as queryable fields) rather than free-text — the "Observability" requirement. |

## Setup Instructions

### Option A — Docker Compose (recommended)

```bash
docker-compose up --build
```

This starts Postgres, Redis, runs migrations automatically (via a one-shot `migrate` service), then starts the API (port 8000) and the worker.

> **Note on verification:** this Dockerfile/compose setup was built and reviewed carefully, but the sandbox used to build this submission has no Docker daemon available to run it end-to-end. **Please verify `docker-compose up --build` works on your machine before considering it validated.** Everything else in this repo (the API, worker, retries, tests) *was* run and verified live against real PostgreSQL and Redis instances during development — see DESIGN.md for details.

Once running:
- API: http://localhost:8000
- Interactive API docs (Swagger): http://localhost:8000/docs
- Health check: http://localhost:8000/health

### Option B — Local development (no Docker)

Requires Python 3.12+, a running PostgreSQL instance, and a running Redis instance.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # edit DATABASE_URL / REDIS_URL if needed

alembic upgrade head

# Terminal 1: API
uvicorn app.main:app --reload --port 8000

# Terminal 2: worker (--with-scheduler is required for backoff retries)
rq worker --with-scheduler critical high normal low
```

> You may see harmless `UserWarning: The parameter -S/--serializer is used more than once` output when starting the worker — this is a cosmetic issue in `rq`'s own CLI argument parser (rq 2.0.0 + click 8.1), not a functional problem; the worker runs correctly despite it.

## Running Tests

```bash
pytest                                        # run the full suite
pytest --cov=app --cov-report=term-missing    # with coverage (93% at last check)
```

40 tests: unit tests for template rendering, retry/backoff math, the rate limiter, and preference resolution; integration tests hitting the full API (including the actual retry-and-recover and retry-exhaustion paths through the worker, not just the queue-creation side).

### Test strategy note
Automated tests run against an in-memory SQLite DB and `fakeredis` rather than real Postgres/Redis, so the suite is fast and needs no external services (`pytest` just works, anywhere). This is safe here because the models avoid Postgres-only types (no JSONB, no native ENUM — see DESIGN.md). The real Postgres+Redis+RQ stack was exercised extensively by hand during development (creating notifications, checking delivery status transitions, forcing simulated provider failures to watch retries fire and back off correctly) and is what `docker-compose` runs.

## API Documentation

Full interactive docs (OpenAPI/Swagger) are auto-generated at `/docs` when the service is running. Summary:

| Method | Path | Description |
|---|---|---|
| POST | `/notifications` | Create a notification. Body: `user_id`, optional `channels`, `priority`, `template_name` OR `subject`+`body`, `variables`, optional `idempotency_key`. |
| GET | `/notifications/{id}` | Get a notification's status, including per-channel delivery state. |
| GET | `/users/{user_id}/notifications?page=&page_size=` | Paginated notification history for a user. |
| POST | `/users/{user_id}/preferences` | Set channel opt-in/opt-out. Body: `{"preferences": [{"channel": "sms", "enabled": false}]}`. |
| GET | `/users/{user_id}/preferences` | Get effective preferences (unset channels default to enabled). |
| GET | `/notifications/analytics/stats` | *(bonus)* Sent/failed counts grouped by channel and status. |
| GET | `/health` | Liveness check. |

### Example: send a templated, high-priority notification

```bash
curl -X POST http://localhost:8000/notifications \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user1",
    "channels": ["email", "sms", "push"],
    "priority": "high",
    "body": "Hello {{name}}, your order {{order_id}} has shipped!",
    "subject": "Order Update",
    "variables": {"name": "Priya", "order_id": "ORD-4521"},
    "idempotency_key": "order-4521-shipped"
  }'
```

## Assumptions Made

- **Authentication/authorization** is out of scope, assumed to be handled by an API gateway upstream (per the assignment's example assumptions).
- **User existence** isn't validated — `user_id` is treated as an opaque string; user data is assumed to live in a separate service.
- **Preference default is opt-in**: if a user has never set a preference for a channel, they're treated as opted *in* to it. Requiring an explicit opt-in row before a brand-new user can receive anything would silently drop notifications for everyone who hasn't visited a settings page. Opt-*out* is always explicit.
- **Templates are stored in the database** (not in-memory) so they survive restarts. No template-management endpoints were required by the spec, so templates are currently seeded directly (see `app/models/template.py`); adding CRUD endpoints would be a natural follow-up.
- **Mock providers "deliver" synchronously**: a successful mock send is recorded as `DELIVERED` immediately, since there's no real provider webhook to simulate an async `SENT → DELIVERED` confirmation step. A production integration would stay at `SENT` until a provider delivery webhook fires.
- **A notification with a pending retry reports `processing`, not `failed`**, at the top level — only a channel that has *exhausted* its retries (or delivered) is treated as terminal for the purposes of the aggregate status. (This was actually a real bug caught and fixed during development — see DESIGN.md.)
- **Idempotency key scope is global** (not scoped per-user) since the spec didn't specify; a client-supplied key is assumed to already be unique enough (e.g. a UUID or a domain-specific key like `order-4521-shipped`).
