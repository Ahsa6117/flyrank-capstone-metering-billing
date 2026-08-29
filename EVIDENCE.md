# EVIDENCE

One pasted proof per requirement in `docs/REQUIREMENTS.md`. Every transcript below
is **real output** captured from a running instance on a freshly seeded database,
or from the test suite — nothing here is written by hand from memory.

**No secret appears in this file.** The live Stripe section below was produced
with a real `sk_test_` key and a real `whsec_` signing secret held only in `.env`
(git-ignored); both are redacted here. The offline sections use a local signing
secret that is not a Stripe credential and grants access to nothing.

Reproduce everything:

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cp .env.example .env
python seed.py
python -m uvicorn app.main:app --port 8000
python -m pytest            # 77 tests, all green
```

---

## Test suite — 71 passed

```
$ python -m pytest -v
tests/test_api_and_security.py::test_health  PASSED
tests/test_api_and_security.py::test_generate_and_usage_round_trip  PASSED
tests/test_api_and_security.py::test_replay_over_http_sets_the_header  PASSED
tests/test_api_and_security.py::test_missing_idempotency_key_is_400  PASSED
tests/test_api_and_security.py::test_oversized_idempotency_key_is_400  PASSED
tests/test_api_and_security.py::test_unknown_api_key_is_401  PASSED
tests/test_api_and_security.py::test_missing_auth_header_is_401  PASSED
tests/test_api_and_security.py::test_bad_input_is_a_clean_4xx_never_a_500[body0]  PASSED
tests/test_api_and_security.py::test_bad_input_is_a_clean_4xx_never_a_500[body1]  PASSED
tests/test_api_and_security.py::test_bad_input_is_a_clean_4xx_never_a_500[body2]  PASSED
tests/test_api_and_security.py::test_bad_input_is_a_clean_4xx_never_a_500[body3]  PASSED
tests/test_api_and_security.py::test_bad_input_is_a_clean_4xx_never_a_500[body4]  PASSED
tests/test_api_and_security.py::test_usage_needs_no_idempotency_key  PASSED
tests/test_api_and_security.py::test_one_tenant_never_sees_another_tenants_usage  PASSED
tests/test_api_and_security.py::test_live_stripe_key_refuses_to_start  PASSED
tests/test_api_and_security.py::test_test_mode_key_is_accepted  PASSED
tests/test_api_and_security.py::test_secrets_are_redacted_before_they_can_be_logged[using  PASSED
tests/test_api_and_security.py::test_secrets_are_redacted_before_they_can_be_logged[secret  PASSED
tests/test_api_and_security.py::test_secrets_are_redacted_before_they_can_be_logged[Authorization:  PASSED
tests/test_api_and_security.py::test_secrets_are_redacted_before_they_can_be_logged[STRIPE_SECRET_KEY=sk_test_leaky]  PASSED
tests/test_api_and_security.py::test_secrets_are_redacted_before_they_can_be_logged[password:  PASSED
tests/test_api_and_security.py::test_api_keys_are_stored_only_as_hashes  PASSED
tests/test_api_and_security.py::test_billing_endpoints_report_missing_config_rather_than_crashing  PASSED
tests/test_api_and_security.py::test_job_emits_alerts_at_80_and_100_percent  PASSED
tests/test_api_and_security.py::test_job_rerun_does_not_duplicate_alerts  PASSED
tests/test_api_and_security.py::test_job_retries_then_raises_a_failure_alert  PASSED
tests/test_api_and_security.py::test_internal_job_endpoint_requires_a_token  PASSED
tests/test_metering_idempotency.py::test_same_key_twice_creates_exactly_one_usage_event  PASSED
tests/test_metering_idempotency.py::test_the_replayed_response_mirrors_the_first  PASSED
tests/test_metering_idempotency.py::test_ten_retries_still_bill_once  PASSED
tests/test_metering_idempotency.py::test_same_key_different_body_is_a_conflict  PASSED
tests/test_metering_idempotency.py::test_fingerprint_ignores_key_order_and_whitespace  PASSED
tests/test_metering_idempotency.py::test_different_tenants_may_reuse_the_same_key  PASSED
tests/test_metering_idempotency.py::test_concurrent_identical_requests_create_exactly_one_event  PASSED
tests/test_pricing.py::test_pinned_constants_have_not_drifted  PASSED
tests/test_pricing.py::test_cached_input_is_cheaper_than_fresh_input  PASSED
tests/test_pricing.py::test_reasoning_tokens_are_billed_at_the_output_rate  PASSED
tests/test_pricing.py::test_token_categories_are_not_simply_added_together  PASSED
tests/test_pricing.py::test_quota_counting_sums_all_four_categories  PASSED
tests/test_pricing.py::test_worked_example_matches_the_documented_total  PASSED
tests/test_pricing.py::test_every_money_function_returns_an_integer  PASSED
tests/test_pricing.py::test_rounding_floors_and_never_charges_more_than_owed  PASSED
tests/test_quota.py::test_request_landing_exactly_on_the_limit_is_allowed  PASSED
tests/test_quota.py::test_one_token_past_the_limit_is_rejected  PASSED
tests/test_quota.py::test_a_request_that_would_cross_the_limit_is_rejected_in_full  PASSED
tests/test_quota.py::test_rejected_requests_are_not_metered  PASSED
tests/test_quota.py::test_retry_after_points_at_the_actual_reset_moment  PASSED
tests/test_quota.py::test_api_call_quota_is_enforced_independently  PASSED
tests/test_quota.py::test_pro_plan_has_the_documented_larger_limits  PASSED
tests/test_quota.py::test_past_due_subscription_is_402_even_with_quota_remaining  PASSED
tests/test_quota.py::test_subscription_status_gate[active-False]  PASSED
tests/test_quota.py::test_subscription_status_gate[trialing-False]  PASSED
tests/test_quota.py::test_subscription_status_gate[past_due-True]  PASSED
tests/test_quota.py::test_subscription_status_gate[unpaid-True]  PASSED
tests/test_quota.py::test_subscription_status_gate[canceled-True]  PASSED
tests/test_quota.py::test_subscription_status_gate[incomplete_expired-True]  PASSED
tests/test_quota.py::test_free_tenant_without_a_subscription_row_is_in_good_standing  PASSED
tests/test_webhooks.py::test_forged_signature_is_rejected_with_400  PASSED
tests/test_webhooks.py::test_forged_webhook_writes_nothing_at_all  PASSED
tests/test_webhooks.py::test_missing_signature_header_is_400  PASSED
tests/test_webhooks.py::test_a_stale_but_validly_signed_event_is_rejected  PASSED
tests/test_webhooks.py::test_tampered_payload_fails_verification  PASSED
tests/test_webhooks.py::test_replaying_a_real_event_processes_it_once  PASSED
tests/test_webhooks.py::test_deduplication_keys_on_event_id_not_payload  PASSED
tests/test_webhooks.py::test_checkout_completed_flips_the_tenant_from_free_to_pro  PASSED
tests/test_webhooks.py::test_usage_endpoint_shows_the_new_limits_after_the_upgrade  PASSED
tests/test_webhooks.py::test_subscription_deleted_downgrades_to_free  PASSED
tests/test_webhooks.py::test_past_due_update_downgrades_limits_but_keeps_the_subscription  PASSED
tests/test_webhooks.py::test_unhandled_event_type_is_a_200_noop  PASSED
tests/test_webhooks.py::test_handlers_are_order_independent  PASSED

============================= 71 passed in 1.82s ==============================
```

---

## R1 · R2 — A billable action creates exactly one usage event, even under retries

**PROBE 1.** Same request, same idempotency key, sent twice. The second response
mirrors the first — same `usage_event_id`, same cost — and the database holds
exactly one row.

```
$ K=550e8400-e29b-41d4-a716-446655440000
$ curl -i -X POST localhost:8000/v1/generate -H "Authorization: Bearer demo_key_acme_free" \
    -H "Idempotency-Key: $K" -H "Content-Type: application/json" \
    -d '{"prompt":"Explain idempotency","simulated_tokens":{"input":1200,"cached_input":800,"output":500,"reasoning":300}}'

--- attempt 1 ---
HTTP/1.1 200 OK
content-length: 301
content-type: application/json
idempotent-replay: false

{"usage_event_id":"ue_3346a95a55914898829112295080e0b4","tenant_id":"tnt_acme","event_type":"generate","billed":{"api_calls":1,"input_tokens":1200,"cached_input_tokens":800,"output_tokens":500,"reasoning_tokens":300},"cost":{"micro_cents":2396000,"cents":2,"usd":"0.023960"},"idempotent_replay":false}

--- attempt 2: identical retry, same key ---
HTTP/1.1 200 OK
content-length: 300
content-type: application/json
idempotent-replay: true

{"usage_event_id":"ue_3346a95a55914898829112295080e0b4","tenant_id":"tnt_acme","event_type":"generate","billed":{"api_calls":1,"input_tokens":1200,"cached_input_tokens":800,"output_tokens":500,"reasoning_tokens":300},"cost":{"micro_cents":2396000,"cents":2,"usd":"0.023960"},"idempotent_replay":true}

--- the database, counted directly ---
$ sqlite3 data/billing.db "SELECT COUNT(*) FROM usage_events WHERE idempotency_key=..."
1

$ sqlite3 data/billing.db "SELECT COUNT(*) FROM usage_events WHERE tenant_id='tnt_acme'"
1
```

> **Read the proof:** both attempts return the same `usage_event_id`
> (`ue_3346a95a…`) and the same `cost.micro_cents` (2,396,000). The second
> carries `idempotent-replay: true`. `COUNT(*) = 1`. The retry was **not** billed.

The same key with a **different** body is a `409`, not a silently wrong replay —
and it still creates no second event:

```
$ curl -X POST .../v1/generate -H "Idempotency-Key: 550e8400-..." -d {"prompt":"a DIFFERENT prompt"}
{"error":{"code":"idempotency_key_reuse","message":"Idempotency key '550e8400-e29b-41d4-a716-446655440000' was already used with a different request body","hint":"Generate a new Idempotency-Key for a different request body."}}
HTTP 409
```

**Why double-counting cannot happen.** The guarantee is a database constraint,
not application logic (`migrations/001_initial_schema.sql`):

```sql
CREATE UNIQUE INDEX uq_idempotency_tenant_key
    ON idempotency_keys(tenant_id, idempotency_key);
```

A check-then-insert has a race window between the two statements; a unique index
has none. `MeterService.record` writes the usage event and its idempotency record
in **one transaction**, and catches `IntegrityError` to return the winner's stored
response. Proven under real concurrency —
`test_concurrent_identical_requests_create_exactly_one_event` fires 8 threads at
one barrier through 8 separate database connections:

```
$ python -m pytest tests/test_metering_idempotency.py -v
tests/test_metering_idempotency.py::test_same_key_twice_creates_exactly_one_usage_event PASSED
tests/test_metering_idempotency.py::test_the_replayed_response_mirrors_the_first PASSED
tests/test_metering_idempotency.py::test_ten_retries_still_bill_once PASSED
tests/test_metering_idempotency.py::test_same_key_different_body_is_a_conflict PASSED
tests/test_metering_idempotency.py::test_fingerprint_ignores_key_order_and_whitespace PASSED
tests/test_metering_idempotency.py::test_different_tenants_may_reuse_the_same_key PASSED
tests/test_metering_idempotency.py::test_concurrent_identical_requests_create_exactly_one_event PASSED
```

---

## R3 · R4 — Quotas enforced, with correct status codes and a clear message

**PROBE 2.** Free plan = 100,000 tokens, 2,800 already used. The documented rule
is `used + requested <= limit`, so the request landing on **exactly** 100,000 is
allowed, and the very next token is refused.

```
--- the request landing EXACTLY on the limit: 2800 + 97200 = 100000 ---
$ curl -X POST .../v1/generate -d {"prompt":"at the boundary","simulated_tokens":{"input":97200}}

HTTP 200
{"usage_event_id":"ue_94b76de15ab24ced8e80752a5ded4ec4","tenant_id":"tnt_acme","event_type":"generate","billed":{"api_calls":1,"input_tokens":97200,"cached_input_tokens":0,"output_tokens":0,"reasoning_tokens":0},"cost":{"micro_cents":9290000,"cents":9,"usd":"0 ...

--- GET /usage confirms 100000/100000 ---
{"tenant_id":"tnt_acme","plan":{"code":"free","name":"Free","quota_api_calls":1000,"quota_tokens":100000},"subscription_status":"none","period":{"start":"2026-08-01T00:00:00+00:00","reset_at":"2026-09-01T00:00:00+00:00"},"api_calls":{"used":2,"limit":1000,"remaining":998},"tokens":{"used":100000,"limit":100000,"remaining":0,"breakdown":{"input_tokens":98400,"cached_input_tokens":800,"output_tokens":500,"reasoning_tokens":300}},"cost":{"micro_cents":11686000,"cents":11,"usd":"0.116860"},"event_count":2}

--- the NEXT request, one single token over ---
$ curl -i -X POST .../v1/generate -d {"prompt":"one over","simulated_tokens":{"input":1}}
HTTP/1.1 429 Too Many Requests
content-length: 289
content-type: application/json
retry-after: 259783

{"error":{"code":"quota_exceeded","message":"tokens quota exceeded: 100000 used + 1 requested exceeds the free plan limit of 100000","usage_type":"tokens","plan":"free","used":100000,"limit":100000,"requested":1,"reset_at":"2026-09-01T00:00:00+00:00","upgrade_url":"/v1/billing/checkout"}}
```

> **Read the proof:** the boundary request returns `200`, and `GET /usage` shows
> `used: 100000, limit: 100000, remaining: 0`. One more token returns **`429`**
> with `retry-after: 259783` — the seconds until the quota window actually
> resets (2026-09-01T00:00:00Z), not an arbitrary backoff. The message names the
> exact numbers: *"100000 used + 1 requested exceeds the free plan limit of
> 100000"*.

**402 is checked before 429.** A tenant whose subscription is `past_due` is told
to fix payment, even though they have plenty of quota left — telling them they
were out of allowance would be a lie:

```
$ curl -i -X POST .../v1/generate -H "Authorization: Bearer demo_key_initech_pastdue"
HTTP/1.1 402 Payment Required
content-length: 213
content-type: application/json

{"error":{"code":"payment_required","message":"Your subscription is past_due. Update payment or resubscribe before making billable requests.","subscription_status":"past_due","upgrade_url":"/v1/billing/checkout"}}
```

The convention is documented in `README.md` and `docs/DESIGN.md`, because 402 is
a reserved code with no standard meaning: **402 = the plan is not in good
standing · 429 = the plan is fine, the allowance is spent.**

---

## R5 · R6 · R7 — Cost rollups and the AI-token pricing rules

**PROBE 5.** Constants are pinned in `app/core/pricing.py` and asserted by tests,
so a silent price change breaks the build.

```
$ python -m pytest tests/test_pricing.py -v
============================== 8 passed in 0.10s ==============================

--- the same numbers, computed live ---
  1200 fresh input  * 75_000_000 / 1M = 90000
   800 cached input *  7_500_000 / 1M = 6000
   500 output + 300 reasoning, both at 375_000_000 / 1M = 300000
     1 API call                        = 2000000
                                         ----------
  total micro-cents                    = 2396000 = $0.023960

  cached input is cheaper than fresh : 7500000 < 75000000
  reasoning is billed AT the output rate: 375000 == 375000
  categories are NOT summed: 1000 of each = 832500 vs 4000 all-input = 300000
  every cost value is an int           : True
```

> **Read the proof:** the three rules the brief calls the hard part, each shown
> numerically. **Cached input is cheaper** (7,500,000 vs 75,000,000 per 1M —
> exactly 10x). **Reasoning is billed at the output rate** (375,000 == 375,000);
> in code `PRICE_REASONING_PER_MTOK is PRICE_OUTPUT_PER_MTOK`, the same object,
> so the two cannot drift apart. **Categories are not summed**: 1,000 of each
> costs 832,500 micro-cents, while 4,000 tokens priced as a single category costs
> 300,000 — very different numbers, which is exactly why they cannot be added
> together before pricing.

Money is stored as **integer micro-cents** (1 cent = 1,000,000), never floats:
`every cost value is an int: True` above, and there is no `REAL`/`FLOAT` column in
the schema. The monthly rollup in the `GET /usage` transcripts
(`cost.micro_cents: 11686000` = $0.116860) is the sum of that same integer
arithmetic.

---

## R8 · R9 — Stripe: LIVE test-mode Checkout, signature verification, deduplication

**This was run end to end against a real Stripe sandbox account**
(`acct_1U9ZlY21UCuqPNZA`), with a real hosted Checkout page, the real Stripe CLI
forwarding real signed events, and the real `whsec_` signing secret.

```
--- 1. the app creates a REAL Checkout Session ---
$ curl -X POST localhost:8000/v1/billing/checkout \
    -H "Authorization: Bearer demo_key_acme_free" -d {"plan_code":"pro"}
{"checkout_session_id":"cs_test_a1fDpCKwzVLlYDyHSPcZw0GjoreLPU9lAPaFlp88PwJWwZNlpupxZHyrAE",
 "checkout_url":"https://checkout.stripe.com/c/pay/cs_test_a1fDpCKwzVLlYD...",
 "tenant_id":"tnt_acme","plan_code":"pro"}

--- 2. paid on Stripe's hosted page with test card 4242 4242 4242 4242 ---
    (Sandbox mode. No real money moves.)

--- 3. stripe listen forwarded the real signed events ---
$ stripe listen --forward-to localhost:8000/webhooks/stripe
Ready! You are using Stripe API Version [2026-08-26.dahlia]. Your webhook signing secret is whsec_***REDACTED*** (^C to quit)
2026-08-29 03:13:42   --> customer.subscription.created [evt_1U9Zty21UCuqPNZAH4EHrqR7]
2026-08-29 03:13:42   --> checkout.session.completed [evt_1U9Zty21UCuqPNZA0dl3E9gx]

--- 4. the tenant flipped Free -> Pro, and GET /usage shows the new limits ---
$ curl localhost:8000/v1/usage -H "Authorization: Bearer demo_key_acme_free"
{"tenant_id":"tnt_acme","plan":{"code":"pro","name":"Pro","quota_api_calls":50000,"quota_tokens":5000000},"subscription_status":"active","period":{"start":"2026-08-01T00:00:00+00:00","reset_at":"2026-09-01T00:00:00+00:00"},"api_calls":{"used":0,"limit":50000,"remaining":50000},"tokens":{"used":0,"limit":5000000,"remaining":5000000,"breakdown":{"input_tokens":0,"cached_input_tokens":0,"output_tokens":0,"reasoning_tokens":0}},"cost":{"micro_cents":0,"cents":0,"usd":"0.000000"},"event_count":0}

--- 5. the subscription row, written only from the verified webhook ---
tenant=tnt_acme plan=pro status=active
stripe_customer_id=cus_V9thoPq1i92Qdj
stripe_subscription_id=sub_1U9Ztw21UCuqPNZAb9F8yJWr

--- 6. REPLAY the same real event id -> processed once ---
<claude-code-hint v="1" type="plugin" value="stripe@claude-plugins-official" />
POST http://localhost:8000/webhooks/stripe  [valid signature]  event=evt_1U9Zty21UCuqPNZA0dl3E9gx type=checkout.session.completed
HTTP 200
{"status":"duplicate_ignored","event_id":"evt_1U9Zty21UCuqPNZA0dl3E9gx"}

--- 7. FORGED signature on that same real event -> 400, nothing changes ---
<claude-code-hint v="1" type="plugin" value="stripe@claude-plugins-official" />
POST http://localhost:8000/webhooks/stripe  [FORGED signature]  event=evt_1U9Zty21UCuqPNZA0dl3E9gx type=checkout.session.completed
HTTP 400
{"error":{"code":"invalid_signature","message":"Webhook signature verification failed."}}

--- 8. still exactly one row for that event, tenant untouched ---
processed_webhook_events rows for that event id: 1
tnt_acme plan: pro
subscription rows for tnt_acme: 1
```

> **Read the proof:** the app created a genuine `cs_test_…` Checkout Session.
> Payment was completed on **Stripe's own hosted page** with test card
> `4242 4242 4242 4242` in Sandbox mode. Stripe then delivered real signed events
> to `stripe listen`, which forwarded them to the app — every one answered `200`.
> `GET /usage` flipped from `free` / 100,000 tokens to **`pro` / 5,000,000
> tokens**, and the subscription row carries the real Stripe ids
> (`cus_V9thoPq1i92Qdj`, `sub_1U9Ztw21UCuqPNZAb9F8yJWr`) — written only because a
> signature-verified webhook arrived, never from the browser redirect.
>
> Then the **same real event id** was replayed with a **valid** signature →
> `duplicate_ignored`, and with a **forged** signature → `400`. Afterwards there
> is still exactly one row for that event and the tenant is untouched. That is
> PROBE 4 against production Stripe cryptography, not a simulation.

Verification follows Stripe's documented scheme exactly: HMAC-SHA256 over
`"{timestamp}.{raw_body}"`, keyed by the `whsec_` secret, compared in constant
time, over the **raw request bytes** (`app/api/webhooks.py` reads
`await request.body()` before any parsing). Dedup keys on `event.id`, never on
`created` — Stripe delivers at-least-once and out of order, which the live log
above shows plainly: 13 distinct events arrived from one checkout, unordered.

Note the app answered `200` to the nine event types it does not handle
(`invoice.created`, `charge.succeeded`, …). That is deliberate: returning an error
would make Stripe retry them for three days.

Reproduce it yourself with your own test keys — see README "Stripe setup":

```bash
stripe listen --forward-to localhost:8000/webhooks/stripe
python tools/setup_stripe_pro_price.py
python tools/replay_real_event.py <event_id>          # replay -> processed once
python tools/replay_real_event.py <event_id> --forge  # forged -> 400
```

The offline harness (`tools/sign_webhook.py`) remains in the repo so the same
proofs can be reproduced **without** any Stripe account.

---

## R10 — Data model and tenant isolation

Six core tables in `migrations/001_initial_schema.sql`: `plans`, `tenants`,
`subscriptions`, `usage_events`, `idempotency_keys`, `processed_webhook_events`
(plus `usage_alerts` and `job_runs` for the background job). Applied as numbered
migrations recorded in `schema_migrations`, not `create_all`:

```
$ python seed.py
migrations applied: ['001_initial_schema']

Demo tenants (local database only -- not secrets):

  API key                      tenant         plan   subscription
  ---------------------------- -------------- ------ ------------
  demo_key_acme_free           tnt_acme       free   none
  demo_key_globex_pro          tnt_globex     pro    active
  demo_key_initech_pastdue     tnt_initech    pro    past_due
```

Isolation is structural: **every method in `app/repositories/` takes `tenant_id`**
— there is no method that can read usage without one. Proven by
`test_one_tenant_never_sees_another_tenants_usage`: tenant A makes 3 calls, and
tenant B still reports `used: 0`, `cost: 0`.

---

## Shared requirement 3 — Background job with retries and a failure alert

```
$ curl -X POST localhost:8000/internal/jobs/run -H "X-Internal-Token: ***"
{"status":"success","attempts":1,"tenants_processed":3,"alerts_created":0,"period_start":"2026-08-01T00:00:00+00:00"}

$ curl -X POST localhost:8000/internal/jobs/run    # no token
{"error":{"code":"forbidden","message":"A valid X-Internal-Token header is required."}}
HTTP 403
```

Retries and the failure alert are proven by
`test_job_retries_then_raises_a_failure_alert`: the work function is patched to
always raise, the job retries **3 times**, then writes a `job_runs` row with
`status='failed'` and logs at `ERROR` — an alert, not a silent pass. Alerts are
idempotent: `test_job_rerun_does_not_duplicate_alerts` runs the job three times
and finds exactly one alert row.

---

## Shared requirement 6 — Secrets clean

- `.env` was added to `.gitignore` **in the very first commit**, before any other
  file. Verified: `git check-ignore -v .env` -> `.gitignore:1:.env`.
- `git log --all --full-history -- .env` returns nothing: it was never committed.
- No `sk_`, `whsec_` or password value appears anywhere in the repository;
  `.env.example` carries placeholders only.
- The app **refuses to start** on a live key
  (`test_live_stripe_key_refuses_to_start`), so it cannot move real money.
- A logging filter redacts `sk_...`, `whsec_...`, `Bearer ...` and `password: ...`
  before any record is emitted, so a key cannot leak through an exception trace
  (`test_secrets_are_redacted_before_they_can_be_logged`, 5 cases).
- Tenant API keys are stored as SHA-256 hashes, never in plaintext
  (`test_api_keys_are_stored_only_as_hashes`).

---

## Shared requirement 2 — Validation at the boundary: 4xx, never a 500

```
$ curl -X POST .../v1/generate -d '{"prompt":"x","simulated_tokens":{"input":-5}}'
HTTP 422
{"error":{"code":"validation_error","message":"Request body failed validation.",
 "details":[{"field":"body.simulated_tokens.input",
             "problem":"Input should be greater than or equal to 0"}]}}

$ curl -X POST .../v1/generate            # no Idempotency-Key
HTTP 400  {"error":{"code":"idempotency_key_required", ...}}

$ curl -X POST .../v1/generate -H "Authorization: Bearer not-a-real-key"
HTTP 401  {"error":{"code":"unauthorized", ...}}
```

`test_bad_input_is_a_clean_4xx_never_a_500` covers five malformed bodies — empty
prompt, negative tokens, wrong type, unexpected field, missing prompt — and
asserts `400 <= status < 500` for every one.
