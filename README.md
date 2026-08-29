# Usage Metering & Billing Engine

A backend service that answers the three questions every SaaS product must
answer: **how much has this customer used, what does it cost, and have they hit
their limit?**

Built for the FlyRank Backend Track capstone. The difficulty here is precision,
not size — a retried request that bills twice, or a quota check that lets one
extra call through, is real money. So the guarantees are enforced by database
constraints and integer arithmetic rather than by careful code.

| | |
|---|---|
| **Stack** | Python 3.11+ · FastAPI · SQLAlchemy · SQLite (Postgres-ready) · Stripe **test mode** |
| **Cost to run** | $0. No credit card, ever. No AI key — token counts are simulated. |
| **Tests** | 77, one command, deterministic |

---

## What it guarantees

1. **Exactly-once metering.** The same request retried any number of times records
   exactly one usage event. Enforced by a `UNIQUE (tenant_id, idempotency_key)`
   index — the database is the arbiter, not application logic, because a
   check-then-insert has a race window and a unique index does not.
2. **Honest boundaries.** A request is allowed while `used + requested <= limit`.
   The request that would cross the limit is rejected **in full** — never
   partially metered — with `429` or `402` and a message naming the exact numbers.
3. **Correct money math.** All money is integer **micro-cents**. No float ever
   touches a cost value. Cached input tokens are cheaper, reasoning tokens are
   billed at the output rate, and token categories are never summed before pricing.
4. **Payment truth lives at Stripe.** The database is a mirror, updated only
   through signature-verified, deduplicated webhook events.

---

## Architecture

```
                    ┌────────────────────────────────────────────────────┐
                    │  CLIENT                                            │
                    │  Authorization: Bearer <api key>                   │
                    │  Idempotency-Key: <uuid v4>                        │
                    └────────────────┬───────────────────────────────────┘
                                     │
╔════════════════════════════════════▼═══════════════════════════════════╗
║  HTTP     app/api/      Pydantic validation · auth · error mapping      ║
║           bad input ───────────────────────────► 4xx (never a 500)     ║
╚════════════════════════════════════╤═══════════════════════════════════╝
                                     │  domain calls only
╔════════════════════════════════════▼═══════════════════════════════════╗
║  LOGIC    app/services/  MeterService · QuotaService · CostService      ║
║                          StripeSyncService                             ║
║           raises QuotaExceeded / SubscriptionNotActive — no HTTP here   ║
╚════════════════════════════════════╤═══════════════════════════════════╝
                                     │  every call carries tenant_id
╔════════════════════════════════════▼═══════════════════════════════════╗
║  DATA     app/repositories/ → app/models/ → SQLite / Postgres           ║
║           UNIQUE(tenant_id, idempotency_key)  ← no double-count         ║
║           UNIQUE(event_id)                    ← no webhook replay       ║
╚════════════════════════════════════════════════════════════════════════╝
```

### The three paths

```
POST /v1/generate ──► duplicate key? ──yes──► return the ORIGINAL response, no new event
                          │no
                          ├─ subscription not in good standing ──► 402
                          ├─ used + requested > limit ───────────► 429 + Retry-After
                          └─ ONE TRANSACTION: usage_event + idempotency_key, or neither

GET /v1/usage ────► rollup(usage_events for this tenant, this UTC month)
                     └─► { used, limit, remaining, cost, breakdown }

Stripe Checkout (test mode) ──► signed webhook ──► POST /webhooks/stripe
                                    ├─ verify HMAC over raw body ──► forged: 400, nothing written
                                    ├─ deduplicate on event.id ────► replay: ignored
                                    └─ update tenant plan / status ► 200
```

Full diagrams: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Design rationale: [`docs/DESIGN.md`](docs/DESIGN.md).

---

## Run it

Requires Python 3.11 or newer. Nothing else — no Docker, no database server.

```bash
git clone https://github.com/Ahsa6117/flyrank-capstone-metering-billing.git
cd flyrank-capstone-metering-billing

python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # works as-is; Stripe keys optional (see below)
python seed.py                # applies migrations + creates demo tenants
python -m uvicorn app.main:app --port 8000
```

Open <http://localhost:8000/docs> for interactive API docs.

**Run the tests:**

```bash
python -m pytest
```

### Demo tenants (`python seed.py`)

These are local demo keys for a local SQLite database. They are not secrets.

| API key | Tenant | Plan | Subscription | Demonstrates |
|---|---|---|---|---|
| `demo_key_acme_free` | `tnt_acme` | Free | none | metering, quotas, the 429 boundary |
| `demo_key_globex_pro` | `tnt_globex` | Pro | active | the higher Pro limits |
| `demo_key_initech_pastdue` | `tnt_initech` | Pro | `past_due` | `402` winning over `429` |

### Try it in 30 seconds

```bash
# Meter a billable call
curl -X POST http://localhost:8000/v1/generate \
  -H "Authorization: Bearer demo_key_acme_free" \
  -H "Idempotency-Key: $(python -c 'import uuid;print(uuid.uuid4())')" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"hello","simulated_tokens":{"input":1200,"cached_input":800,"output":500,"reasoning":300}}'

# Send the SAME request again with the SAME key -> identical response, no second charge

# See the rollup
curl http://localhost:8000/v1/usage -H "Authorization: Bearer demo_key_acme_free"
```

---

## Plans and quotas

Quotas reset on the **1st of each month at 00:00 UTC**.

| Plan | API calls / month | AI tokens / month |
|---|---|---|
| **Free** | 1,000 | 100,000 |
| **Pro** | 50,000 | 5,000,000 |

The Free numbers are fixed by the brief. The Pro numbers are our choice, and are
seeded from `migrations/001_initial_schema.sql` — change them there.

The **token quota counts all four categories summed** (a token consumed is a token
consumed), while **pricing keeps them separate**, because the rates differ. That
distinction is deliberate and is asserted by a test.

---

## Pricing

Money is stored as integer **micro-cents**: `1 cent = 1,000,000 micro-cents`.
Cents alone are too coarse — one token costs a small fraction of a cent, so
rounding per event would leak money. **No float ever touches a money value.**

| Item | Rate | Micro-cents |
|---|---|---|
| API call | $0.00200 / call | `2,000,000` |
| Fresh input tokens | $0.75 / 1M | `75,000,000` |
| **Cached** input tokens | $0.075 / 1M | `7,500,000` |
| Output tokens | $3.75 / 1M | `375,000,000` |
| **Reasoning** tokens | *the output rate* | `375,000,000` |

Pinned in [`app/core/pricing.py`](app/core/pricing.py) and asserted by tests, so a
silent price change breaks the build.

**The three rules that are easy to get wrong:**

1. **Cached input is cheaper** — priced with its own constant, 10× below fresh
   input, and **not also** billed as fresh input. The counts are disjoint.
2. **Reasoning tokens are billed at the output rate** — in code,
   `PRICE_REASONING_PER_MTOK is PRICE_OUTPUT_PER_MTOK`: literally the same object,
   so they cannot drift apart. Never free.
3. **Categories are not summed before pricing** — there is no code path that adds
   token counts together and then applies one rate.

```
cost = (input   × 75,000,000)  ÷ 1,000,000
     + (cached  ×  7,500,000)  ÷ 1,000,000
     + ((output + reasoning) × 375,000,000) ÷ 1,000,000
     + (api_calls × 2,000,000)
```

Rounding is **floor** — a fraction of a micro-cent is never rounded up against the
customer. Worked example (1,200 input · 800 cached · 500 output · 300 reasoning ·
1 call): `90,000 + 6,000 + 300,000 + 2,000,000 = 2,396,000` µ¢ = **$0.023960**.

---

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/generate` | Bearer + `Idempotency-Key` | The billable action |
| `GET` | `/v1/usage` | Bearer | Monthly rollup |
| `POST` | `/v1/billing/checkout` | Bearer | Stripe test-mode Checkout Session |
| `POST` | `/webhooks/stripe` | Stripe signature | Subscription sync |
| `POST` | `/internal/jobs/run` | `X-Internal-Token` | Run the background job now |
| `GET` | `/health` | — | Liveness |

`Idempotency-Key` is **required** on `/v1/generate` — stricter than Stripe, because
every call here costs money and an un-keyed request has no safe retry story. Use a
UUIDv4, at most 255 characters, containing no personal data.

### Status codes

| Status | Meaning |
|---|---|
| `200` | Metered. `idempotent_replay` and the `Idempotent-Replay` header say whether it was a fresh charge or a deduplicated retry. |
| `400` | Missing/oversized `Idempotency-Key`, or a malformed webhook |
| `401` | Missing or unknown API key |
| `402` | **The subscription is not in good standing** — payment must change |
| `409` | Same idempotency key reused with a **different** request body |
| `422` | Body failed validation |
| `429` | **Plan is fine, the monthly allowance is spent.** Includes `Retry-After` |

### 402 vs 429 — our documented convention

`402 Payment Required` is a *reserved, non-standard* status code with **no standard
use convention**, so ours is written down explicitly rather than assumed:

- **`402` is about the plan.** The subscription is `past_due`, `unpaid`, `canceled`
  or `incomplete_expired`. Money must change before any billable call succeeds.
- **`429` is about the allowance.** The plan is in good standing; this month's
  quota is spent. It will succeed again after `reset_at` — which is exactly what
  the `Retry-After` header counts down to.
- **402 is checked first**, because telling an unpaid customer "you're out of
  quota" would be a lie.

### The boundary rule

> A request is allowed while **`used + requested <= limit`**. The request that
> would push the total past the limit is rejected in full.

On Free (1,000 calls): at `used = 999` one more call gives `1000 <= 1000` →
**allowed**. At `used = 1000` the next gives `1001 > 1000` → **`429`**. The limit is
inclusive: a tenant gets exactly the 1,000 calls they were promised.

---

## Stripe setup (test mode, free, no card)

Everything except live Checkout already works without any Stripe account.

1. Create a free Stripe account and stay in **test mode**.
2. Copy your **test** secret key (`sk_test_…`) from the Dashboard's API keys page.
3. Create a recurring test-mode Price for "Pro" and copy its `price_…` id.
4. Install the [Stripe CLI](https://docs.stripe.com/cli/install) and forward events:

   ```bash
   stripe listen --forward-to localhost:8000/webhooks/stripe
   ```

   Copy the `whsec_…` it prints. **A CLI secret and a Dashboard endpoint secret are
   different — do not mix them.**
5. Put all three in `.env` (never in the repo):

   ```
   STRIPE_SECRET_KEY=sk_test_…
   STRIPE_WEBHOOK_SECRET=whsec_…
   STRIPE_PRICE_ID_PRO=price_…
   ```

6. Upgrade a tenant end to end:

   ```bash
   curl -X POST http://localhost:8000/v1/billing/checkout \
     -H "Authorization: Bearer demo_key_acme_free" \
     -H "Content-Type: application/json" -d '{"plan_code":"pro"}'
   ```

   Open the returned `checkout_url`, pay with **4242 4242 4242 4242** and any
   future expiry. The webhook flips the tenant to Pro; `GET /v1/usage` shows the
   new limits.

Replay events without clicking through Checkout:

```bash
stripe trigger checkout.session.completed
```

**Safety rails:** the app **refuses to start** if given an `sk_live_` key. Secrets
are read from the environment only and are redacted by a logging filter before any
record is emitted, so a key cannot leak through an exception trace.

**No Stripe account?** `tools/sign_webhook.py` sends correctly-signed (and, with
`--forge`, deliberately forged) webhooks using a local secret, so signature
verification, the tolerance window, replay dedup and the Free → Pro flip can all be
verified **without any Stripe account at all**.

**With** an account, two more helpers do the same against real Stripe:

```bash
python tools/setup_stripe_pro_price.py          # creates the Pro price, writes price_ id to .env
python tools/replay_real_event.py <event_id>          # replay a REAL event -> processed once
python tools/replay_real_event.py <event_id> --forge  # forged signature -> 400
```

---

## Using Postgres instead of SQLite

SQLite is the default so the project runs anywhere with no infrastructure. The same
models and migrations run on Postgres:

```bash
docker compose up -d
pip install "psycopg[binary]"
# in .env:
DATABASE_URL=postgresql+psycopg://billing:billing@localhost:5432/billing
python seed.py
```

---

## Background job

An in-process APScheduler job (`app/jobs/rollup_and_alerts.py`) runs every 15
minutes, off the request path:

- recomputes month-to-date usage rollups
- emits usage alerts at **80%** and **100%** of either quota
- **retries 3 times with exponential backoff**; after the final failure it writes a
  `job_runs` row with `status='failed'` and logs at `ERROR` — a real failure alert,
  not a silent pass

Alerts are idempotent by construction: a unique index on
`(tenant, usage_type, threshold, period)` means a re-run cannot duplicate one.

Trigger it on demand:

```bash
curl -X POST http://localhost:8000/internal/jobs/run \
  -H "X-Internal-Token: <INTERNAL_JOB_TOKEN from your .env>"
```

---

## Project layout

```
app/
├── api/            HTTP only — routers, schemas, error mapping, auth
├── services/       business logic — raises domain errors, knows no HTTP
├── repositories/   data access — every method takes tenant_id
├── models/         SQLAlchemy ORM — the schema
├── core/           pricing constants, integer money, config, logging, periods
└── jobs/           background rollups and alerts
migrations/         numbered .sql migrations, recorded in schema_migrations
tests/              70 tests covering all five acceptance probes
tools/              offline webhook signer for local verification
docs/               brief, requirements, reference notes, design, architecture
```

Dependencies point one way only: `api → services → repositories → models`.

---

## Limitations — an honest list

- **Stripe is verified live in test mode, but only in test mode.** A real hosted
  Checkout was completed against a Stripe sandbox with card `4242…`, and real
  signed webhooks flipped a tenant Free → Pro (`EVIDENCE.md`). Live mode has
  never been exercised and never will be — the app refuses to start on an
  `sk_live_` key. Without keys in `.env`, `/v1/billing/checkout` returns
  `503 stripe_not_configured` rather than failing obscurely.
- **SQLite is the default.** It is a real database with real constraints and the
  concurrency test passes against it, but SQLite serialises writers. The Postgres
  path exists and the models are portable; it has not been load-tested.
- **The scheduler is in-process.** Two app instances would each run the job.
  Alerts stay correct because of their unique index, but a distributed deployment
  would want a single scheduler or a leader lock.
- **No invoicing, proration, or overage billing.** Out of scope by the brief; they
  are stretch goals.
- **Quota windows are calendar months in UTC**, not per-tenant billing anniversaries.
  A tenant who subscribes on the 20th still resets on the 1st.
- **AI tokens are simulated.** The client reports the counts; the service meters
  and prices them. Metering numbers is the subject here, not generating text.
- **Idempotency records are never pruned.** Stripe drops keys after 24 hours; we
  retain them because they double as the billing audit trail. That is a deliberate
  divergence, and it means the table grows with usage.
- **Tenant API keys are seeded, not managed.** There is no key rotation or
  self-service signup endpoint.

---

## Documentation

| File | Contents |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | Data model, API contract, idempotency strategy, non-goal |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Full path diagrams |
| [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) | The graded contract, as a checklist |
| [`docs/REFERENCES.md`](docs/REFERENCES.md) | Rules extracted from the cited sources, with the code each governs |
| [`EVIDENCE.md`](EVIDENCE.md) | One real pasted proof per requirement |
| [`BUILDLOG.md`](BUILDLOG.md) | Honest AI-usage log |
