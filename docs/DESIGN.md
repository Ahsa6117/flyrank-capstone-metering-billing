# Design document — Usage Metering & Billing Engine

**Phase 1 deliverable.** Problem · data model · API surface · layer sketch · one explicit non-goal.
Every decision below cites the rule it comes from in `REFERENCES.md` (e.g. `I1`, `M2`, `T3`).

---

## 1 · Problem

A multi-tenant SaaS must answer three questions, correctly, every time:

1. **How much has this customer used?** — metering
2. **Have they hit their limit?** — quota enforcement
3. **What does it cost?** — money math

The two failure modes that matter are *double-charging* (a retried request billed twice) and
*giving away unlimited access* (a quota check that lets one extra request through). Both are
correctness bugs, not scale bugs, so the design optimises for **provable correctness under retries**
rather than throughput.

---

## 2 · Non-goal (explicit, as required by the brief)

> **This service does not move real money, and it is not the source of truth for payment state.**

Payment truth lives at Stripe. Our database is a **mirror**, updated only through
signature-verified webhook events (`W2`). We never mark a tenant "paid" from a client request, a
redirect, or a Checkout success URL — only a verified `checkout.session.completed` or
`customer.subscription.*` event can change plan state. Consequently the app runs in **Stripe test
mode only** and a `sk_live_` key makes it refuse to boot (`S1`).

Also out of core scope, per brief Section 7: invoicing, proration, and overage billing.

---

## 3 · Plans and quotas

Quotas reset on the **1st of each month at 00:00 UTC**. The billing period is the calendar month in
UTC; all timestamps are stored UTC-aware.

| Plan | API calls / month | AI tokens / month | Source |
|---|---|---|---|
| `free` | **1,000** | **100,000** | fixed by the brief |
| `pro` | **50,000** | **5,000,000** | our choice, documented in `README.md` |

Both usage types are metered per tenant. The token limit counts **all four token categories summed**
(a token consumed is a token consumed); *pricing* keeps them separate (`T5`). This distinction is
deliberate and is the single most easily-confused point in the system, so it is asserted by a test.

---

## 4 · Pricing constants (pinned, `core/pricing.py`)

Money is stored in **integer micro-cents**: `1 cent = 1_000_000 micro-cents` (`M2`). Cents alone are
too coarse — a single token costs a small fraction of a cent, and rounding per event would leak
money. **No float ever touches a money value** (`M1`).

| Item | Rate | Micro-cents |
|---|---|---|
| API call | $0.00200 per call | `2_000_000` µ¢ / call |
| Fresh input tokens | $0.75 / 1M tokens | `75_000_000` µ¢ / 1M |
| **Cached** input tokens | $0.075 / 1M tokens | `7_500_000` µ¢ / 1M |
| Output tokens | $3.75 / 1M tokens | `375_000_000` µ¢ / 1M |
| **Reasoning** tokens | *the output rate* | `375_000_000` µ¢ / 1M |

Shape taken from the Gemini pricing reference (`T1`–`T4`). The three rules that the calculator must
encode, and which the brief calls out as the hard part:

1. **Cached input is cheaper** — 10× cheaper here, priced with its own constant, and **not also**
   billed as fresh input. The two counts are disjoint.
2. **Reasoning tokens are billed at the output rate** — `PRICE_REASONING is PRICE_OUTPUT`, literally
   the same constant, so they can never drift apart (`T3`).
3. **Categories cannot simply be added together** — there is no code path that sums token counts
   before applying a price.

Formula, all integer arithmetic, with the single division deferred to the end (`M3`):

```
cost_micro_cents =
    (input  * P_IN      ) // 1_000_000
  + (cached * P_CACHED  ) // 1_000_000
  + ((output + reasoning) * P_OUT) // 1_000_000
  + (api_calls * P_CALL)
```

Rounding is **floor** — never rounded up against the customer — and stated in the README (`M4`).

---

## 5 · Data model

Six tables. SQLite by default; the same SQLAlchemy models run on Postgres via `DATABASE_URL`.
Schema is applied as **numbered migration files**, not `create_all` (`S4`).

```
plans                          tenants                       subscriptions
─────────────────────          ─────────────────────         ──────────────────────────────
code            PK  TEXT       id            PK  TEXT        id                 PK  TEXT
name                TEXT       name              TEXT        tenant_id          FK  → tenants.id
quota_api_calls     INT        api_key_hash   U  TEXT        plan_code          FK  → plans.code
quota_tokens        INT        plan_code     FK → plans      stripe_customer_id     TEXT
                               created_at        TS          stripe_subscription_id U TEXT
                                                             status                 TEXT
                                                             current_period_end     TS
usage_events                          idempotency_keys                 processed_webhook_events
──────────────────────────────        ─────────────────────────        ────────────────────────
id                 PK  TEXT           id              PK  TEXT         event_id      PK  TEXT
tenant_id          FK  → tenants      tenant_id       FK  → tenants    event_type        TEXT
event_type             TEXT           idempotency_key     TEXT         processed_at      TS
api_calls              INT            request_fingerprint TEXT
input_tokens           INT            status_code         INT
cached_input_tokens    INT            response_body       TEXT
output_tokens          INT            usage_event_id  FK  → usage_events
reasoning_tokens       INT            created_at          TS
cost_micro_cents       INT
idempotency_key        TEXT           UNIQUE (tenant_id, idempotency_key)   ← the guarantee
occurred_at            TS
```

**Indexes**
- `UNIQUE (tenant_id, idempotency_key)` on `idempotency_keys` — the database constraint that makes
  double-counting impossible even under concurrency (`I5`). Not application logic. This is the heart
  of the capstone.
- `INDEX (tenant_id, occurred_at)` on `usage_events` — every rollup is "this tenant, this month".
- `UNIQUE (event_id)` on `processed_webhook_events` — webhook replay protection (`W4`).
- `UNIQUE (stripe_subscription_id)`, `UNIQUE (api_key_hash)`.

**Tenant isolation** (`S4`, R10): every query in the repository layer takes `tenant_id` as a
required argument. There is no repository method that can read usage without one. Tenants are
identified by an API key presented as `Authorization: Bearer <key>`; only a **hash** of the key is
stored, never the key itself (`S6`).

**Money columns are `BIGINT`/`INTEGER` only.** No `REAL`, no `NUMERIC`, no float (`M1`).

---

## 6 · API surface

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/generate` | The billable action. Simulates an AI call, meters it, enforces quota, prices it. |
| `GET` | `/v1/usage` | Monthly rollup: `used`, `limit`, `remaining`, `cost` per usage type. |
| `POST` | `/v1/billing/checkout` | Creates a Stripe test-mode Checkout Session for the Pro plan. |
| `POST` | `/webhooks/stripe` | Signature-verified, deduplicated subscription sync. **No auth header** — the signature *is* the auth. |
| `GET` | `/health` | Liveness. |

### `POST /v1/generate`

```http
POST /v1/generate
Authorization: Bearer <tenant api key>
Idempotency-Key: 3f9a1c7e-... (client-generated UUIDv4, ≤255 chars, required)
Content-Type: application/json

{ "prompt": "...", "simulated_tokens": { "input": 1200, "cached_input": 800,
                                          "output": 500, "reasoning": 300 } }
```

Success `200`:
```json
{ "usage_event_id": "ue_...", "billed": { "api_calls": 1, "input_tokens": 1200,
    "cached_input_tokens": 800, "output_tokens": 500, "reasoning_tokens": 300 },
  "cost": { "micro_cents": 341500, "cents": 0, "usd": "0.003415" },
  "idempotent_replay": false }
```

The `Idempotency-Key` header is **required** on this route and rejected with `400` if absent —
stricter than Stripe, because here every call is billable (`I3`, `I4`).

### Error contract

| Status | When | Body |
|---|---|---|
| `400` | Missing/oversized `Idempotency-Key`, malformed JSON | `{ "error": { "code": "...", "message": "..." } }` |
| `401` | Missing or unknown API key | `{ "error": { "code": "unauthorized", ... } }` |
| `409` | Same key replayed with a **different** body (`I2`) | includes both fingerprints |
| `422` | Validation failure (negative tokens, wrong types) — Pydantic at the boundary (`S2`) | field-level detail |
| `429` | Plan healthy, **allowance exhausted** (`Q1`) | `used`, `limit`, `requested`, `reset_at`; **`Retry-After` header in seconds** (`Q2`) |
| `402` | **Subscription not in good standing** (`P2`) | `code`, `message`, `upgrade_url` |

**No path returns `500` for bad input** (`S2`). Unhandled exceptions are caught by a global handler
that logs with the secret-redacting formatter and returns a generic `500` with no internals.

### 402 vs 429 — the documented convention

402 is a *reserved, non-standard* code with **no standard use convention** (`P1`), so ours is
written down explicitly:

- **`402` is about the plan.** Subscription status is `past_due`, `unpaid`, `canceled` or
  `incomplete_expired`. Money must change before any billable call succeeds.
- **`429` is about the allowance.** The plan is in good standing; this month's quota is spent. It
  will succeed again after `reset_at`, which is why `Retry-After` is meaningful.
- **402 is checked first** (`P3`) — telling an unpaid customer "you're out of quota" would be a lie.

### The boundary rule (graded by PROBE 2)

> A request is allowed while **`used + requested <= limit`**. The request that would push the total
> **past** the limit is rejected in full — we never partially meter.

Worked example on Free (1,000 calls): at `used = 999`, one more call gives `1000 <= 1000` →
**allowed**. At `used = 1000`, the next call gives `1001 > 1000` → **`429`**. The limit is
inclusive; a tenant gets exactly the 1,000 calls they paid for, no more and no fewer.

Quota is checked **before** the action, and the check plus the write happen in **one transaction**,
so two concurrent requests at the boundary cannot both observe `used = 999`.

---

## 7 · Idempotency strategy — the heart of the capstone

**Guarantee: the same `(tenant_id, Idempotency-Key)` produces exactly one `usage_event`, ever.**

The order of operations on `POST /v1/generate`, inside a single transaction:

```
1  authenticate tenant (API key hash)          → 401 if unknown
2  validate body with Pydantic                 → 422 (never 500)
3  look up (tenant_id, idempotency_key)
      hit + same fingerprint   → return the STORED status + body, no new event   (I1)
      hit + different fingerprint → 409 Conflict                                 (I2)
4  check subscription standing                 → 402
5  check quota: used + requested <= limit      → 429 + Retry-After
6  INSERT usage_event
7  INSERT idempotency_key row  (tenant_id, key, fingerprint, status, response)
      IntegrityError on the UNIQUE index → a concurrent twin won the race:
      roll back, re-read the winner's stored response, return it                 (I5)
8  COMMIT  → both rows land, or neither does
```

Why this is correct, not merely careful:

- The **unique index is the arbiter**, not a `SELECT` before an `INSERT`. A check-then-insert has a
  race window between the two statements; a unique constraint has none. Under concurrent retries one
  transaction commits and the other gets `IntegrityError`, and the loser returns the winner's
  response rather than creating a second event.
- The usage event and its idempotency record are inserted in the **same transaction**, so there is
  no state in which a request was billed but not recorded as billed, or vice versa.
- We store the **status code and body** of the first attempt, so a replay is byte-identical —
  including a replayed failure (`I1`).
- The **fingerprint** (SHA-256 of canonical JSON) means a client cannot reuse a key with a different
  payload and silently get the old answer (`I2`).

Keys are client-generated UUIDv4s, ≤255 chars, and must not contain personal data — documented in
the README (`I3`). Records are retained rather than pruned at 24 h because they double as the
billing audit trail (`I6`).

---

## 8 · Layer sketch (`S1` — data / logic / HTTP separated)

```
app/
├─ api/            HTTP only. FastAPI routers, Pydantic schemas, error handlers,
│                  auth dependency. Knows about status codes; knows no SQL.
├─ services/       Business logic. MeterService, QuotaService, CostService,
│                  StripeSyncService. Raises domain errors (QuotaExceeded,
│                  SubscriptionNotActive, IdempotencyConflict). Knows no HTTP.
├─ repositories/   Data access. Every method takes tenant_id. The only place
│                  SQLAlchemy queries are written.
├─ models/         SQLAlchemy ORM models — the schema.
├─ core/           pricing.py (pinned constants), money.py (integer math),
│                  config.py (env, sk_live_ guard), logging.py (secret redaction),
│                  periods.py (UTC month windows).
├─ jobs/           Background job: monthly rollup + 80%/100% usage alerts,
│                  with retries and a failure alert (S3).
└─ migrations/     Numbered SQL migrations (S4).
```

Rule enforced by review and by import direction: `api → services → repositories → models`.
Nothing points back up. A domain error carries no status code; `api/errors.py` is the single place
that maps `QuotaExceeded → 429` and `SubscriptionNotActive → 402`.

---

## 9 · Background job (`S3`)

A scheduler thread (APScheduler, in-process — no broker needed, no cost) runs
`jobs/rollup_and_alerts.py`:

- **Rollup**: recompute each tenant's month-to-date usage and cost into a summary table, so
  `GET /usage` is cheap and month-end reporting does not scan every event on the request path.
- **Usage alerts**: at 80% and 100% of either quota, record an alert (once per tenant per threshold
  per month — idempotent by construction, same discipline as metering).
- **Retries**: three attempts with exponential backoff. After the final failure the job writes a
  `job_failure` alert record and logs at `ERROR` — the required *failure alert*, not a silent pass.
- It is triggerable manually (`POST /internal/jobs/run`, key-protected) so an evaluator can prove it
  works without waiting for a schedule.

---

## 10 · Security and secret hygiene (`S6`, brief G2)

- `.env` was in `.gitignore` **before commit #1**; `.env.example` ships placeholders only.
- Stripe keys and `whsec_` are read from the environment, never written to a doc, a log, a test
  fixture, or `EVIDENCE.md`. Evidence transcripts are redacted (`sk_test_***`).
- A logging filter redacts anything matching `sk_(test|live)_\w+`, `whsec_\w+`, `rk_\w+` and
  `Bearer \w+` before a record is emitted, so a key cannot leak through an exception trace.
- Tenant API keys are stored as SHA-256 hashes; the plaintext exists only at seed time, shown once.
- Startup refuses `sk_live_` (`S1`).
- `/webhooks/stripe` is exempt from API-key auth by design — the Stripe signature is the
  authentication, and the raw body is read before any parsing (`W1`).

---

## 11 · Testing strategy

`pytest`, one command, deterministic — the clock is injected so month boundaries are testable.
Tests are written to the five acceptance probes, plus the traps:

- same key twice → one event, identical response · same key + different body → `409`
- concurrent identical requests (threads) → exactly one event
- boundary at `used = limit - 1`, `= limit`, `= limit + 1` → allow, allow, `429`
- `past_due` subscription → `402` even with quota remaining (402 wins over 429)
- forged signature → `400` **and the DB is unchanged** · same `event.id` twice → processed once
- pricing: cached input cheaper than fresh · reasoning billed at the output rate ·
  categories never summed · every cost function returns `int`
