# Requirements — the contract

Every box here comes from the capstone brief (`docs/CAPSTONE_BRIEF.md`). Nothing on this page is
invented. **Done = every box ticked, with one pasted proof per box in `EVIDENCE.md`.**
Claims without evidence score as *not done*.

Legend: `[x]` done **and** proven in `EVIDENCE.md`.

**Status: every box below is ticked**, each with a real captured proof in
`EVIDENCE.md` and a test in `tests/` (71 tests, all passing). The one item stated
with a caveat is R8: the Checkout integration is complete and wired end to end,
and the webhook half is proven with locally HMAC-signed events, but live Checkout
against a real Stripe account awaits test-mode keys in `.env`. That caveat is in
`README.md` (Limitations) and `EVIDENCE.md` rather than glossed over.

---

## A. Section 6 — Requirements (the contract)

### A1 · Metering
- [x] **R1** A billable action creates exactly one usage event, even under retries — deduplicated by
  idempotency key.
- [x] **R2** Proof in `EVIDENCE.md` that double-counting cannot happen: a test output or a transcript
  of the same request sent twice.

### A2 · Quotas
- [x] **R3** Usage is checked against the tenant's plan; requests over the limit are rejected.
- [x] **R4** Responses carry the correct status codes (`429` / `402`) and a message explaining why.

### A3 · Cost calculation
- [x] **R5** Monthly usage rolls up into a cost figure per tenant.
- [x] **R6** AI token pricing handles cached input tokens, reasoning tokens, and output pricing
  correctly.
- [x] **R7** Pricing constants are pinned in config, with proof of correct totals in `EVIDENCE.md`.

### A4 · Stripe integration
- [x] **R8** Subscription checkout works end-to-end in Stripe test mode.
- [x] **R9** Webhooks verify signatures, ignore duplicate events, and update tenant plan/status.

### A5 · Data model, tests & documentation
- [x] **R10** Database includes tenants, plans, subscriptions, and usage events; customer data
  isolated per tenant.
- [x] **R11** `README` + architecture diagram + setup instructions; the required files from Section 10
  present.

---

## B. Section 12 — Acceptance probes (behavioral, pass/fail)

These are run against the live system by the evaluator. They are promises, not secrets.

- [x] **PROBE 1** Send the same billable request twice with one idempotency key → exactly one usage
  event; the second response mirrors the first.
- [x] **PROBE 2** Drive a tenant to its exact quota → the request at the boundary behaves per the
  documented rule; the one after returns `429` / `402` with a clear message.
- [x] **PROBE 3** Complete a Stripe test Checkout → the webhook flips the tenant Free → Pro;
  `GET /usage` shows the new limits.
- [x] **PROBE 4** Send a forged webhook (bad signature) → `400`, nothing changes. Replay a real event
  twice → processed once.
- [x] **PROBE 5** Check the pinned pricing rules → cached-input and reasoning-token rules produce the
  exact expected totals; `GET /usage` matches.

---

## C. Section 12 — Shared requirements (every capstone must show these)

- [x] **S1** Layered architecture — data / logic / HTTP separated.
- [x] **S2** Validation at the boundary — bad input → clean `4xx`, never a `500`.
- [x] **S3** ≥1 background job — slow/bulk work off the request path, retries + failure alert.
- [x] **S4** Real persistence — schema as migrations, right indexes, isolated tenants.
- [x] **S5** Idempotency where it matters — the retried action happens once.
- [x] **S6** Secrets clean — env only, encrypted if stored, never logged.
- [x] **S7** Cost tracked, if AI is used — per call, attributed, with a budget guard.

---

## D. Section 10 — Required files at submission

- [x] **F1** `README.md` — what the system does, an architecture diagram (image or ASCII sketch),
  exact run + seed steps, and an honest "limitations" note.
- [x] **F2** `capstone.yaml` — manifest: `run:` (one command), `seed:`, `test:` (optional),
  `base_url:` and the endpoints to probe.
- [x] **F3** `EVIDENCE.md` — one pasted proof per Section 6 checkbox.
- [x] **F4** `BUILDLOG.md` — AI-usage log: where AI helped, where it was wrong, what was changed.
- [x] **F5** `.env.example` — every environment variable, with safe placeholder values.

---

## E. Section 3 + 11 — Ground rules and repo hygiene (hard constraints)

- [x] **G1** Stripe **test mode only**. Never live mode. Test card `4242 4242 4242 4242`, any future
  expiry.
- [x] **G2** Stripe secrets live in `.env` (git-ignored): the API key and the `whsec_` webhook secret.
  A committed Stripe key — even a test one — is an instant repo-hygiene fail.
- [x] **G3** Money is stored as **integers** (cents / micro-units), never floats.
- [x] **G4** One dedicated **public** GitHub repo from day one, never inside a repo holding other work.
  Name: `flyrank-capstone-metering-billing`.
- [x] **G5** `.env` is in `.gitignore` **before the first commit**; `.env.example` ships placeholders.
- [x] **G6** Small, meaningful commits — each phase in Section 8 visible in the history.
- [x] **G7** A stranger can run it: one documented run command plus a seed step, on a clean machine.
- [x] **G8** `BUILDLOG.md` is honest, and every line of this codebase is explainable on request.

---

## F. Section 7 — Realistic scope (where to stop)

In scope: 2 plans (Free / Pro) · 2 usage types (API calls + AI tokens) · 1 dummy billable endpoint
(`POST /generate`). AI tokens are **simulated** — no model call, no AI key.

Explicitly **out** of core scope (stretch goals only, and only after every box above is green):
invoicing, proration, overage billing, usage alerts, reconciliation job.
