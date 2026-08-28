# Requirements — the contract

Every box here comes from the capstone brief (`docs/CAPSTONE_BRIEF.md`). Nothing on this page is
invented. **Done = every box ticked, with one pasted proof per box in `EVIDENCE.md`.**
Claims without evidence score as *not done*.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done **and** proven in `EVIDENCE.md`.

---

## A. Section 6 — Requirements (the contract)

### A1 · Metering
- [ ] **R1** A billable action creates exactly one usage event, even under retries — deduplicated by
  idempotency key.
- [ ] **R2** Proof in `EVIDENCE.md` that double-counting cannot happen: a test output or a transcript
  of the same request sent twice.

### A2 · Quotas
- [ ] **R3** Usage is checked against the tenant's plan; requests over the limit are rejected.
- [ ] **R4** Responses carry the correct status codes (`429` / `402`) and a message explaining why.

### A3 · Cost calculation
- [ ] **R5** Monthly usage rolls up into a cost figure per tenant.
- [ ] **R6** AI token pricing handles cached input tokens, reasoning tokens, and output pricing
  correctly.
- [ ] **R7** Pricing constants are pinned in config, with proof of correct totals in `EVIDENCE.md`.

### A4 · Stripe integration
- [ ] **R8** Subscription checkout works end-to-end in Stripe test mode.
- [ ] **R9** Webhooks verify signatures, ignore duplicate events, and update tenant plan/status.

### A5 · Data model, tests & documentation
- [ ] **R10** Database includes tenants, plans, subscriptions, and usage events; customer data
  isolated per tenant.
- [ ] **R11** `README` + architecture diagram + setup instructions; the required files from Section 10
  present.

---

## B. Section 12 — Acceptance probes (behavioral, pass/fail)

These are run against the live system by the evaluator. They are promises, not secrets.

- [ ] **PROBE 1** Send the same billable request twice with one idempotency key → exactly one usage
  event; the second response mirrors the first.
- [ ] **PROBE 2** Drive a tenant to its exact quota → the request at the boundary behaves per the
  documented rule; the one after returns `429` / `402` with a clear message.
- [ ] **PROBE 3** Complete a Stripe test Checkout → the webhook flips the tenant Free → Pro;
  `GET /usage` shows the new limits.
- [ ] **PROBE 4** Send a forged webhook (bad signature) → `400`, nothing changes. Replay a real event
  twice → processed once.
- [ ] **PROBE 5** Check the pinned pricing rules → cached-input and reasoning-token rules produce the
  exact expected totals; `GET /usage` matches.

---

## C. Section 12 — Shared requirements (every capstone must show these)

- [ ] **S1** Layered architecture — data / logic / HTTP separated.
- [ ] **S2** Validation at the boundary — bad input → clean `4xx`, never a `500`.
- [ ] **S3** ≥1 background job — slow/bulk work off the request path, retries + failure alert.
- [ ] **S4** Real persistence — schema as migrations, right indexes, isolated tenants.
- [ ] **S5** Idempotency where it matters — the retried action happens once.
- [ ] **S6** Secrets clean — env only, encrypted if stored, never logged.
- [ ] **S7** Cost tracked, if AI is used — per call, attributed, with a budget guard.

---

## D. Section 10 — Required files at submission

- [ ] **F1** `README.md` — what the system does, an architecture diagram (image or ASCII sketch),
  exact run + seed steps, and an honest "limitations" note.
- [ ] **F2** `capstone.yaml` — manifest: `run:` (one command), `seed:`, `test:` (optional),
  `base_url:` and the endpoints to probe.
- [ ] **F3** `EVIDENCE.md` — one pasted proof per Section 6 checkbox.
- [ ] **F4** `BUILDLOG.md` — AI-usage log: where AI helped, where it was wrong, what was changed.
- [ ] **F5** `.env.example` — every environment variable, with safe placeholder values.

---

## E. Section 3 + 11 — Ground rules and repo hygiene (hard constraints)

- [ ] **G1** Stripe **test mode only**. Never live mode. Test card `4242 4242 4242 4242`, any future
  expiry.
- [ ] **G2** Stripe secrets live in `.env` (git-ignored): the API key and the `whsec_` webhook secret.
  A committed Stripe key — even a test one — is an instant repo-hygiene fail.
- [ ] **G3** Money is stored as **integers** (cents / micro-units), never floats.
- [ ] **G4** One dedicated **public** GitHub repo from day one, never inside a repo holding other work.
  Name: `flyrank-capstone-metering-billing`.
- [ ] **G5** `.env` is in `.gitignore` **before the first commit**; `.env.example` ships placeholders.
- [ ] **G6** Small, meaningful commits — each phase in Section 8 visible in the history.
- [ ] **G7** A stranger can run it: one documented run command plus a seed step, on a clean machine.
- [ ] **G8** `BUILDLOG.md` is honest, and every line of this codebase is explainable on request.

---

## F. Section 7 — Realistic scope (where to stop)

In scope: 2 plans (Free / Pro) · 2 usage types (API calls + AI tokens) · 1 dummy billable endpoint
(`POST /generate`). AI tokens are **simulated** — no model call, no AI key.

Explicitly **out** of core scope (stretch goals only, and only after every box above is green):
invoicing, proration, overage billing, usage alerts, reconciliation job.
