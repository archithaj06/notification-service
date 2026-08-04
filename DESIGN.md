# Design Document

## High-Level Architecture

```
                     ┌─────────────┐
   Client  ────POST──▶   FastAPI   │──────┐
                     │   (api)     │      │ 1. validate + rate-limit
                     └──────┬──────┘      │ 2. resolve template/channels
                            │             │ 3. persist Notification +
                     ┌──────▼──────┐      │    NotificationChannel rows
                     │ PostgreSQL  │◀─────┘ 4. commit
                     └──────▲──────┘
                            │                  ┌────────────────────┐
                            │  read/write       │  Redis              │
                            │◀──────────────────┤  - priority queues  │
                            │                   │  - rate-limit ZSETs │
                            │                   │  - RQ scheduler     │
                     ┌──────┴──────┐            └─────────┬──────────┘
                     │  RQ Worker  │◀── pop (critical → high → normal → low) ─┘
                     │  (worker)   │
                     └──────┬──────┘
                            │ send (mocked)
                     ┌──────▼──────┐
                     │ Email/SMS/  │
                     │ Push senders│  (mocked providers)
                     └─────────────┘
```

The API and worker are two separate processes (two containers in docker-compose) sharing the same Postgres database and Redis instance. The API never sends anything itself — it only validates, persists, and enqueues; all actual "sending" and retry logic lives in the worker. This separation is what makes the queue meaningful: a burst of 1000 requests/sec can be accepted and durably persisted by the API almost instantly, while the worker(s) drain the queue at whatever rate the (mocked, and in reality rate-limited) providers can sustain — and can be scaled independently.

## Database Schema

Three tables model the notification lifecycle, plus `user_preferences` and `templates`:

```
notifications                    notification_channels              delivery_attempts
──────────────                   ──────────────────────             ──────────────────
id (PK)                          id (PK)                             id (PK)
user_id            (indexed)     notification_id (FK) ─────┐        notification_channel_id (FK) ──┐
priority                         channel                   │        attempt_number                 │
status                           status            (indexed)        status                          │
template_id (FK, nullable)       attempt_count               ┌──────┘        error_message           │
raw_subject / raw_body           max_retries                 │               provider_response       │
variables (JSON)                 next_retry_at                │               created_at              │
idempotency_key (unique,         last_error                   │                                       │
  indexed)                       created_at / updated_at       └───────────────────────────────────────┘
created_at / updated_at
```

**Why split `Notification` from `NotificationChannel`?** A single logical request ("notify user X") can fan out to multiple channels (email *and* push). If those were tracked as one row with one status, a failure on SMS would either be hidden by a success on email, or would incorrectly fail the whole notification. Splitting them means each channel has its own independent status, attempt count, and retry schedule, and the parent `Notification.status` is *derived* by aggregating its children (`delivered` / `failed` / `partially_failed` / `processing`).

**Why a separate `delivery_attempts` audit table, distinct from `NotificationChannel`'s mutable status?** `NotificationChannel` holds current, mutable state (used for the retry scheduler and for `GET /notifications/:id`). `DeliveryAttempt` is an append-only log of every individual try, kept even after the channel's overall status changes — needed for real observability/debugging ("why did this notification take 3 minutes to deliver?") and reused directly for the bonus analytics endpoint.

**Enum storage:** all status/priority/channel enums use `values_callable` so the DB stores the lowercase string value (`"delivered"`) rather than SQLAlchemy's default of the Python enum *name* (`"DELIVERED"`) — this was caught and fixed during development after noticing raw SQL results didn't match the API's JSON casing. Enums are stored as plain `VARCHAR` (`native_enum=False`) rather than native Postgres `ENUM` types, trading a little storage efficiency for migration simplicity (adding a new status value later is just data, not a schema-altering `ALTER TYPE`) and portability (the same models run against SQLite in tests).

## How Failures and Retries Are Handled

1. A channel send failure is recorded as a `DeliveryAttempt(status=failure)`, and `NotificationChannel.attempt_count` is incremented.
2. If `attempt_count` hasn't exhausted `max_retries` (default 3), the channel is marked `FAILED` *with* `next_retry_at` set, and a retry job is scheduled via RQ's built-in scheduler using exponential backoff: `30s, 60s, 120s` (base × 2^retries_used).
3. **Important subtlety, caught during testing:** a channel that's `FAILED` but has a retry pending is *not* the same as permanently failed. The notification-level status aggregation (`_recompute_notification_status`) explicitly checks `next_retry_at is None` before treating a `FAILED` channel as terminal — otherwise `GET /notifications/:id` would incorrectly report `"failed"` for the entire notification during the (up to 120s) window before a retry that's about to succeed. This was a real bug found while writing integration tests for the retry path (not just the pure backoff-math unit tests) and is exactly the kind of thing that's easy to miss without exercising the full worker flow end-to-end.
4. Once retries are exhausted, the channel is permanently `FAILED` (`next_retry_at = None`), and the parent notification settles into `FAILED` (if *all* channels failed) or `PARTIALLY_FAILED` (if some channels succeeded and others didn't).
5. A malformed request — e.g. a template variable that's missing — is treated as a *permanent*, non-retryable failure: retrying won't fix bad input, so it's marked `FAILED` immediately without consuming retry attempts.

**No notification is ever lost:** the API commits the `Notification` + `NotificationChannel` rows to Postgres *before* enqueueing, so even if Redis or the worker were to crash immediately after, the record of "this needs to be sent" already durably exists. (A production hardening step here would be a periodic reconciliation job that re-enqueues any `PENDING` channel rows older than some threshold with no corresponding queue entry — `NotificationRepository.get_due_retries()` is written with exactly this kind of sweep in mind, though the primary retry path uses RQ's own scheduler rather than polling.)

## How the System Would Scale

- **API tier:** stateless — horizontally scale `uvicorn`/FastAPI instances behind a load balancer. The only shared state is Postgres and Redis.
- **Worker tier:** RQ workers are independent processes that all pop from the same named queues; running more `rq worker` processes (or containers) linearly increases throughput. Priority is preserved because every worker checks `critical` before `high` before `normal`/`low`.
- **Database:** the write path is small and indexed (`user_id`, `status`, `idempotency_key`, `notification_id`) — Postgres read replicas would handle `GET` traffic if it grew disproportionately to writes. Table growth (delivery_attempts especially) would eventually want partitioning by `created_at` or archival of old attempts.
- **Redis:** the priority queues and the rate-limiter's sliding-window ZSETs are both lightweight; Redis Cluster would be the next step if a single instance became a bottleneck, though at 1000 notifications/sec this is unlikely to be the constraint before Postgres write throughput is.
- **Rate limiting** is already implemented per-user via a Redis sorted-set sliding window (atomic via a Lua `EVAL`, so concurrent requests for the same user can't race past the limit) — this pattern scales horizontally with Redis itself, unlike an in-process counter which wouldn't work correctly across multiple API instances.

## Trade-offs

- **RQ over Celery.** Celery is the more "production-canonical" choice for many teams, but has meaningfully more operational surface (broker + optional result backend, worker pool config, task routing) for what this assignment needs. RQ's native support for multiple priority-ordered queues and a built-in delayed scheduler covers every functional requirement here with far less code and fewer moving parts to get wrong under a tight timeline. Celery would be the natural upgrade path if this service later needed complex routing, scheduled/periodic tasks beyond simple retries, or multi-broker support.
- **Generic `JSON` column type over Postgres `JSONB`.** `variables` and `provider_response` use SQLAlchemy's generic `JSON` type rather than `JSONB`, sacrificing JSONB's indexing/query operators in exchange for the same models working unmodified against SQLite in the automated test suite. Since the app never queries *into* these JSON blobs (only reads/writes them whole), this costs nothing functionally today.
- **Synchronous mock delivery confirmation.** Mock sends resolve to `DELIVERED` immediately rather than modeling a realistic `SENT → (async webhook) → DELIVERED` gap, since there's no real provider to send a webhook. The schema and status model already support that distinction (`SENT` is a defined status) — wiring up the bonus **Webhook Support** feature would mean adding a `POST /webhooks/delivery-status` endpoint that flips a channel from `SENT` to `DELIVERED`/`FAILED`, which fits the existing repository methods without schema changes.
- **Idempotency key is a single global unique column**, not scoped per-user or per-endpoint. Simpler, and sufficient for the stated requirement, but a multi-tenant system might want `UNIQUE(user_id, idempotency_key)` instead so two different users can't collide on the same key.
- **Test suite uses SQLite + fakeredis instead of spinning up real Postgres/Redis in CI.** This trades a small amount of environment fidelity for a suite that runs anywhere with zero setup. The real stack was still exercised thoroughly by hand (see README) — including deliberately forcing provider failures against the live Redis-backed worker to confirm the retry/backoff timing and status transitions actually work outside of mocks, not just inside the test doubles.
