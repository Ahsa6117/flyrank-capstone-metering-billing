# Architecture

Three paths, deliberately small: **one metering path, one read path, one payment-sync path.**

---

## System overview

```
                    ┌──────────────────────────────────────────────────────────┐
                    │                      CLIENT                              │
                    │   Authorization: Bearer <tenant api key>                 │
                    │   Idempotency-Key: <uuid v4>                             │
                    └───────────┬──────────────────────────────────────────────┘
                                │
╔═══════════════════════════════▼══════════════════════════════════════════════╗
║  HTTP LAYER   app/api/          Pydantic validation · auth · error mapping    ║
║               bad input ─────────────────────────► 4xx  (never a 500)        ║
╚═══════════════════════════════╤══════════════════════════════════════════════╝
                                │ domain calls only — no SQL, no status codes below here
╔═══════════════════════════════▼══════════════════════════════════════════════╗
║  SERVICE LAYER   app/services/                                               ║
║                                                                              ║
║    MeterService · QuotaService · CostService · StripeSyncService              ║
║    raise QuotaExceeded / SubscriptionNotActive / IdempotencyConflict          ║
╚═══════════════════════════════╤══════════════════════════════════════════════╝
                                │ every call carries tenant_id
╔═══════════════════════════════▼══════════════════════════════════════════════╗
║  DATA LAYER   app/repositories/ → app/models/ → SQLite / Postgres            ║
║               UNIQUE(tenant_id, idempotency_key)   ← the no-double-count      ║
║               UNIQUE(event_id)                     ← the no-replay guarantee  ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## Path 1 — the metering path (`POST /v1/generate`)

```
Client → Billable API request  (Idempotency-Key required)
   │
   ├─ authenticate tenant by API-key hash ───────────────► 401 unknown key
   ├─ validate body (Pydantic) ──────────────────────────► 422 bad input
   │
   └─ MeterService.record(tenant, type, qty, idempotencyKey)
         │
         ├─ duplicate key, same body?  → return the ORIGINAL stored status+body
         │                                (no new event, idempotent_replay: true)
         ├─ duplicate key, other body? → 409 Conflict
         │
         ├─ BUMP tenants.metering_lock  ── serialises this tenant until COMMIT
         │      (a second request for the same tenant waits here, so it reads
         │       usage only after the first has committed. Without this, two
         │       requests at the boundary both saw "one call left": 1006/1000.)
         │
         ├─ subscription in good standing? ──────────────► 402 Payment Required
         │                                                  (checked BEFORE quota)
         ├─ Quota Check:  used + requested <= limit ?
         │        no  ───────────────────────────────────► 429 + Retry-After
         │        yes
         ├─ CostService.price()  → integer micro-cents
         │
         └─ ONE TRANSACTION:
                INSERT usage_event
                INSERT idempotency_key (UNIQUE tenant_id+key)
                   └─ IntegrityError → concurrent twin won:
                      roll back, return the winner's stored response
                COMMIT   → both rows, or neither
```

## Path 2 — the read path (`GET /v1/usage`)

```
GET /v1/usage ──► rollup(usage_events WHERE tenant_id = ? AND occurred_at IN this UTC month)
                      │
                      └─► { api_calls:  { used, limit, remaining },
                            tokens:     { used, limit, remaining,
                                          breakdown: { input, cached_input,
                                                       output, reasoning } },
                            cost:       { micro_cents, usd },
                            plan, subscription_status, period_start, reset_at }
```

## Path 3 — the payment-sync path

```
Stripe Checkout (TEST MODE) ──► customer pays with 4242 4242 4242 4242
                                        │
                                        ▼
Stripe ── signed webhook ──► POST /webhooks/stripe
      Stripe-Signature: t=…,v1=…
                 │
                 ├─ read RAW body (bytes, before any parsing)
                 ├─ verify HMAC-SHA256 over "{t}.{raw_body}", tolerance 300 s
                 │       forged / stale ─────────────► 400   (nothing written)
                 │
                 ├─ deduplicate on event.id (UNIQUE)
                 │       already processed ──────────► 200 duplicate_ignored (no change)
                 │
                 ├─ handle: checkout.session.completed
                 │          customer.subscription.updated
                 │          customer.subscription.deleted
                 │          (anything else → 200 no-op, so Stripe stops retrying)
                 │
                 └─ update tenant plan / subscription status ──► 200 fast
```

**Payment truth lives at Stripe. This database only mirrors it, and only through verified events.**

---

## Background job

```
APScheduler (in-process) ──► jobs/rollup_and_alerts.py
       │
       ├─ recompute month-to-date rollups   (keeps GET /usage off the event scan)
       ├─ emit usage alerts at 80% / 100%   (once per tenant/threshold/month)
       │
       └─ on exception: retry ×3 with exponential backoff
                        still failing → write job_failure alert + log ERROR
```

Manually triggerable at `POST /internal/jobs/run` so it can be demonstrated on demand.

---

## Request lifecycle of a single billed call

```
t0  POST /v1/generate  Idempotency-Key: K   ─┐
t1  auth ok, body valid                      │
t2  no prior row for (tenant, K)             │  one
t3  subscription active                      │  transaction
t4  quota: 412 + 1 <= 1000  → allowed        │
t5  cost = 341_500 micro-cents (integers)    │
t6  INSERT usage_event + idempotency_key    ─┘
t7  200 { usage_event_id, cost, idempotent_replay: false }

t8  NETWORK RETRY, same key K
t9  row found, fingerprint matches
t10 200 — the SAME stored body, idempotent_replay: true, usage_events count still 1
```
