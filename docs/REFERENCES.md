# Reference notes — the rules this codebase must obey

Section 13 of the brief lists curated resources. This file records **what each source actually
dictates for our implementation**, read at build time and quoted, so that no rule in this repo is
recalled from memory. Each rule is tagged with the code that must satisfy it.

---

## 1 · Stripe — Idempotent requests
Source: <https://docs.stripe.com/api/idempotent_requests>

What the source says:

- Idempotency is for *"safely retrying requests without accidentally performing the same operation
  twice… if a connection error occurs, you can safely repeat the request without risk of creating a
  second object."*
- *"Stripe's idempotency works by **saving the resulting status code and body** of the first request
  made for any given idempotency key, **regardless of whether it succeeds or fails**. Subsequent
  requests with the same key return the same result, including `500` errors."*
- *"A **client** generates an idempotency key… we suggest using **V4 UUIDs**, or another random
  string with enough entropy to avoid collisions. Idempotency keys are up to **255 characters**
  long. **Avoid using sensitive data** (for example, email addresses or personal identifiers) as
  idempotency keys."*
- *"You can remove keys from the system automatically after they're at least **24 hours** old."*
- *"The idempotency layer **compares incoming parameters to those of the original request and errors
  if they're not the same** to prevent accidental misuse."*
- *"We save results only after the execution of an endpoint begins. If incoming parameters fail
  validation, or the request **conflicts with another request that's executing concurrently**, we
  don't save the idempotent result."*
- *"All `POST` requests accept idempotency keys. Don't send idempotency keys in `GET` and `DELETE`
  requests… These requests are idempotent by definition."*

### Rules adopted → `services/metering.py`, `models/idempotency_key.py`
| # | Rule |
|---|---|
| I1 | Store the **status code + response body** of the first attempt, keyed by `(tenant_id, idempotency_key)`. A replay returns the stored response verbatim and creates **no** new usage event. |
| I2 | Fingerprint the request body (SHA-256 of the canonical JSON). Same key + **different** body → `409 Conflict`, never a silent overwrite. This is Stripe's "compares incoming parameters… and errors" rule. |
| I3 | Key is **client-generated**, max 255 chars, validated at the boundary. Recommend UUIDv4 in the README. |
| I4 | Only `POST /generate` accepts the key. `GET /usage` needs none — it is idempotent by definition. |
| I5 | Concurrency: the unique index on `(tenant_id, idempotency_key)` is the arbiter. The loser of the race catches the `IntegrityError` and returns the winner's stored response — the DB constraint, not application logic, is what makes double-counting impossible. |
| I6 | Records are retained (not pruned at 24h) because they are also our billing audit trail; retention is documented as a deliberate divergence in `DESIGN.md`. |

---

## 2 · Stripe — Verify webhook signatures / Receive webhook events
Sources: <https://docs.stripe.com/webhooks> · <https://docs.stripe.com/webhooks/signature>

What the sources say:

- Header shape: `Stripe-Signature: t=1492774577,v1=5257a869…,v0=6ffbb59b…` — *"The timestamp has a
  `t=` prefix, and each signature has a scheme prefix… the only valid live signature scheme is
  `v1`."* *"To prevent downgrade attacks, **ignore all schemes that aren't `v1`**."*
- `signed_payload` = *"the timestamp (as a string)" + "the character `.`" + "the actual JSON payload
  (that is, the request body)"*.
- *"Compute an HMAC with the **SHA256** hash function. Use the endpoint's **signing secret as the
  key**, and use the `signed_payload` string as the message."*
- *"To protect against timing attacks, use a **constant-time-string comparison**."*
- *"Stripe requires the **raw body** of the request to perform signature verification… Any
  manipulation to the raw body of the request causes the verification to fail."*
- Replay protection: *"Our libraries have a default tolerance of **5 minutes**… **Don't use a
  tolerance value of `0`** — using a tolerance value of 0 disables the recency check entirely."*
- Duplicates: *"Webhook endpoints might occasionally receive the same event more than once. You can
  guard against duplicated event receipts by **logging the event IDs** you've processed, and then
  not processing already-logged events."*
- Ordering: *"Stripe **doesn't guarantee the delivery of events in the order that they're
  generated**… **Don't use `created` to determine event order** or whether you've already processed
  an event. **Track event IDs** to identify duplicate deliveries instead."*
- Status codes: *"Your endpoint must **quickly return a successful status code (2xx)** before any
  complex logic that could cause a timeout."* A failed verification returns `400`.
- Local testing: `stripe listen --forward-to localhost:4242/webhook` prints the `whsec_` secret;
  `stripe trigger <event>` replays events. *"If you're using the Stripe CLI, the secret is printed in
  the Terminal when you run `stripe listen`."* Never mix a CLI secret with a Dashboard secret.

### Rules adopted → `api/webhooks.py`, `services/stripe_sync.py`
| # | Rule |
|---|---|
| W1 | Read the request body with `await request.body()` — **raw bytes**, before any JSON parsing. FastAPI does not mutate it if we never declare a Pydantic body model on this route. |
| W2 | Verify with `stripe.Webhook.construct_event(payload, sig_header, whsec)`. Any `SignatureVerificationError` → **`400`**, and **nothing is written to the DB**. |
| W3 | Keep the default **300 s tolerance**. Never set it to 0. A stale-but-validly-signed replay is rejected. |
| W4 | Dedup on **`event.id`** in a `processed_webhook_events` table with a unique index — never on `created`, never on payload equality. Second delivery of the same id → `200 {"status":"duplicate_ignored"}` with no state change. |
| W5 | Handlers must be **order-independent**: `subscription.deleted` arriving before `subscription.updated` must still leave the tenant in a correct state. We store `stripe_subscription_status` from the event and derive the plan, rather than assuming a sequence. |
| W6 | Return `2xx` fast; the heavy rollup work goes to the background job, not the webhook path. |
| W7 | An unhandled event type is a **`200` no-op**, not an error — otherwise Stripe retries it for three days. |

---

## 3 · Modern Treasury — Floats don't work for storing cents
Source: <https://www.moderntreasury.com/journal/floats-dont-work-for-storing-cents>

What the source says:

- *"To store `$2.78` as a floating point number, we decompose it into powers of two… until we get to
  the closest representation: **2.7799999713897705078125**."*
- Rounding is implementation-dependent: Ruby's `round` is half-away-from-zero, Python 3 is half-even,
  so `-2.225` becomes `-2.23` or `-2.22` depending on the call.
- Cascading error: *"`(price + tax).round(2)` yields `16.77` while `(price + price * tax_rate).round(2)`
  yields `16.78` — a one-cent discrepancy from identical inputs."*
- Their solution: **64-bit integers in minor currency units.** `"$12.34"` is stored as `1234`.

### Rules adopted → `core/money.py`, all cost columns
| # | Rule |
|---|---|
| M1 | **No `float` touches money, anywhere.** Enforced by a test that asserts every cost function returns `int`. |
| M2 | Token prices are per **1,000,000 tokens**, and a single token costs a fraction of a cent — so cents are too coarse. We store money in **micro-cents** (1 cent = 1,000,000 micro-cents) as 64-bit integers, and convert to cents only for display. |
| M3 | Compute with integer arithmetic: `tokens * price_per_million_micro_cents // 1_000_000`. Division happens **once, at the end**, never in intermediate steps — this is the cascading-error rule. |
| M4 | Rounding is explicit and documented: floor division, i.e. we never round a fraction of a micro-cent up against the customer. Stated in `DESIGN.md` and `README.md`. |

---

## 4 · MDN — 429 Too Many Requests
Source: <https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/429> (RFC 6585 §4)

What the source says:

- *"Indicates the client has sent too many requests in a given amount of time. This mechanism is
  commonly called rate limiting."*
- *"A `Retry-After` header **may be included** to indicate how long a client should wait before
  making the request again."* Example: `Retry-After: 3600` (delay in seconds).
- The body format is flexible — HTML, JSON or plain text.

### Rules adopted → `api/errors.py`
| # | Rule |
|---|---|
| Q1 | `429` is returned when the tenant's **plan is healthy but the monthly allowance is exhausted**. |
| Q2 | Every `429` carries a **`Retry-After`** header in seconds — computed as the seconds remaining until the quota window resets at the start of next UTC month, since that is when a retry can actually succeed. |
| Q3 | The JSON body names the exact numbers: usage type, `used`, `limit`, `requested`, and `reset_at`. "Explain why" is a graded requirement (R4). |

---

## 5 · MDN — 402 Payment Required
Source: <https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/402>

What the source says:

- *"A **nonstandard** response status code **reserved for future use**… created to enable digital
  cash or (micro) payment systems and would indicate that requested content is **not available until
  the client makes a payment**. **No standard use convention exists** and different systems use it in
  different contexts."*
- Payment APIs commonly use it for a failed/expired payment state, with a JSON `error` object
  carrying `code`, `doc_url` and `message`.

### Rules adopted → `api/errors.py`
| # | Rule |
|---|---|
| P1 | Because 402 has **no standard convention**, our convention is written down explicitly in `README.md` and `DESIGN.md` — that is the honest way to use a reserved code. |
| P2 | **`402` = the subscription itself is not in good standing** (`past_due`, `unpaid`, `canceled`, `incomplete_expired`). Payment must change before *any* billable call is allowed, whatever the quota says. |
| P3 | **`429` = plan is fine, allowance is spent.** The two are never used interchangeably. 402 is checked **first**, because an unpaid tenant should not be told "you're out of quota". |
| P4 | Body follows the shape MDN documents for payment APIs: `code`, `message`, plus our `upgrade_url`. |

---

## 6 · Gemini API pricing — cached input and thinking tokens
Source: <https://ai.google.dev/gemini-api/docs/pricing>

What the source says:

- Three distinct token classes are priced differently: **fresh input**, **context-cache input**, and
  **output**.
- Output is labelled *"Output price (**including thinking tokens**)"* — reasoning/thinking tokens are
  billed **at the full output rate**, not as a separate or free category.
- Concrete shape of the numbers: fresh input `$0.75` / 1M, cached input `$0.075` / 1M, output
  (incl. thinking) `$3.75` / 1M — cached input is roughly **an order of magnitude cheaper** than
  fresh input.

### Rules adopted → `core/pricing.py`
| # | Rule |
|---|---|
| T1 | Four counters are metered separately: `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens`. They are **never summed into one number** before pricing — this is the brief's *"token categories cannot simply be added together"*. |
| T2 | `cached_input_tokens` are priced at their **own, cheaper rate**, and are **not** also charged as fresh input. Fresh input and cached input are disjoint counts. |
| T3 | `reasoning_tokens` are priced at **exactly the output rate** — the same constant, deliberately not a separate one, so the two can never drift apart. |
| T4 | Cost = `input·Pin + cached·Pcached + (output + reasoning)·Pout`, all in integer micro-cents. |
| T5 | Constants are **pinned in one config module**, imported everywhere, and asserted in tests so a silent price change breaks the build. Quota counting for the token limit uses the **sum of all four** counters (a token consumed is a token consumed), while *pricing* keeps them separate — the distinction is documented, since conflating them is the classic bug here. |

---

## 7 · Stripe — Test mode / Stripe CLI
Sources: <https://docs.stripe.com/webhooks> (local listener section) · brief Section 10

| # | Rule |
|---|---|
| S1 | **Test mode only.** Keys are `sk_test_…`; the live-mode key must never appear. A startup guard **refuses to boot** if `STRIPE_SECRET_KEY` starts with `sk_live_`. |
| S2 | Test card `4242 4242 4242 4242`, any future expiry, any CVC. No real money moves; no credit card is ever required. |
| S3 | Secrets come from `.env` (git-ignored) only. They are **never logged** — the logger redacts anything matching `sk_…` / `whsec_…`. |
| S4 | Local delivery uses `stripe listen --forward-to localhost:8000/webhooks/stripe`; the `whsec_` it prints is the one the app must use. Dashboard secrets and CLI secrets are different and must not be mixed. |
| S5 | `stripe trigger checkout.session.completed` replays events without clicking through Checkout. |
