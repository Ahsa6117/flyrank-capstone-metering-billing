# BUILDLOG

An honest record of how this was built, including where AI helped, where it was
wrong, and what changed as a result. The brief grades honesty here, not
perfection — and the standing rule is that I must be able to explain any 2–3
lines of this codebase on request.

**AI tool used:** Claude (Claude Code), throughout.

---

## How the work was actually split

**What I decided, and AI did not:**

- The stack (FastAPI + SQLAlchemy + SQLite with a Postgres path), after checking
  what this machine could actually run — there is no Docker installed here, and a
  capstone an evaluator cannot start is worth nothing.
- Pro plan limits: 50,000 calls / 5,000,000 tokens.
- Micro-cents rather than cents, once I worked out that one token at $0.75/1M
  costs 0.000075 cents — rounding each event to whole cents would floor almost
  every small call to zero.
- The 402-before-429 ordering, and writing that convention down because 402 is a
  reserved code with no standard meaning.
- Making `Idempotency-Key` **required** rather than optional as Stripe has it.
- Retaining idempotency records instead of pruning them at 24 hours.

**What AI accelerated:**

- Boilerplate: SQLAlchemy model definitions, Pydantic schemas, router wiring.
- Turning the reference sources into `docs/REFERENCES.md`, then implementing
  against those rules.
- Drafting the docs and test scaffolding, which I then edited for accuracy.

---

## Phase order

Followed Section 8 of the brief in order, with each phase committed separately so
the history shows it.

| Phase | Gate | Result |
|---|---|---|
| 0 · capture | brief + requirements + reference notes committed | `befc534` |
| 1 · design | design doc committed | `ed12cf8` |
| 2 · core | same request twice = one event; boundary returns 429/402 | `556b2fa` |
| 3 · Stripe | Checkout flips Free → Pro via webhook | in `556b2fa`, proven in `EVIDENCE.md` |
| 4 · cost & finalize | `/usage` matches the pinned constants | tests + `EVIDENCE.md` |

Phase 0 was not in the brief. I added it because I wanted the graded contract and
the source-derived rules written down *before* any code, so that every later
decision could cite a rule instead of a recollection. `docs/REFERENCES.md` earned
its keep repeatedly — the 5-minute webhook tolerance, the "never dedup on
`created`" rule, and the reserved status of 402 all came from reading the sources
rather than assuming.

---

## Where AI was wrong, and what I changed

### 1. Pinned dependency versions that could not install

AI produced a `requirements.txt` with exact pins (`pydantic==2.10.4`,
`fastapi==0.115.6`). On Python 3.14 there is no prebuilt `pydantic-core` wheel for
that version, so pip fell back to compiling Rust from source and the install
failed with `maturin failed / could not compile rustversion`.

**Changed:** switched to floor pins (`pydantic>=2.11`) so pip resolves versions
that ship cp314 wheels, and committed `requirements.lock.txt` from `pip freeze` so
the exact resolved set is still reproducible. The reasoning is written into the
file as a comment.

### 2. A migration runner that split SQL on every semicolon

The first version of `_split_statements` in `app/db.py` was
`sql.split(";")`. It broke immediately:

```
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) incomplete input
[SQL: CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY, ... -- SHA-256 of the API key]
```

The comment `-- SHA-256 of the API key; the plaintext key is never stored.`
contains a semicolon, so the statement was cut in half.

**Changed:** strip line comments first, then split. My own comment caused the bug,
which was a useful reminder that a naive parser fails on real input, not on the
input you imagined.

### 3. `stripe.Event.to_dict_recursive()` no longer exists

AI wrote `event.to_dict_recursive()` to normalise the verified event. The
installed `stripe` version raises `AttributeError: to_dict_recursive. Did you
mean: '_to_dict_recursive'?` — it had been made private.

**Changed:** since verification has already succeeded at that point, the raw bytes
are trustworthy, so I parse them with `json.loads(payload)` instead. That removes
the dependency on an SDK helper whose name changes between versions. This is the
clearest example of "the AI wrote it" not being an answer: the code looked
plausible and did not run.

### 4. Test fixtures that collided on a unique index

The first fixture set used shared constants (`FREE_KEY = "test_key_free"`) for
every tenant. `tenants.api_key_hash` is `UNIQUE`, so the second test to request
that fixture hit an `IntegrityError` — 19 errors that looked like application bugs
but were fixture reuse.

**Changed:** each fixture mints a UUID-suffixed key. Noted in the fixture's
docstring so the next person does not reintroduce it.

### 5. A test helper that silently skipped its own test case

`_generate(..., body=None)` used `body or {default}`. One parametrised case passes
`{}` (missing prompt) to prove it is rejected — but `{}` is falsy in Python, so the
helper substituted the *valid* default and the endpoint returned `200`. The test
failed, correctly, for the wrong reason: the assertion was right and the harness
was broken.

**Changed:** `body if body is not None else {default}`. Worth recording because a
falsy-default bug in a test helper is exactly the kind of thing that would
otherwise have made a real failure look like a passing suite.

---

## Things I got wrong myself

- I first wrote the quota check as "reject when `used + requested > limit`" and
  the docs as "allowed up to the limit", without pinning down what happens *at*
  exactly the limit. The brief asks that question directly ("At 999 of 1,000? At
  exactly 1,000?"), so I wrote the rule down explicitly — inclusive — and then
  wrote three tests at `limit - 1`, `limit`, and `limit + 1`.
- My initial cost function summed all four token counts and applied a blended
  rate. That is precisely the mistake the brief warns about. Rewrote it to price
  each category with its own constant, and added
  `test_token_categories_are_not_simply_added_together` to make sure it stays
  that way.
- I nearly used `float` for the USD display value. Switched to `Decimal`, because
  `2396000 / 100_000_000` is not exactly `0.023960` in binary floating point.

---

## Decisions I would defend in an interview

- **The unique index is the guarantee, not the code.** `MeterService` deliberately
  attempts the insert and catches `IntegrityError` rather than doing
  `SELECT`-then-`INSERT`. A check-then-insert has a race window between the two
  statements; a unique constraint has none. The 8-thread test in
  `test_concurrent_identical_requests_create_exactly_one_event` exists to prove
  the difference is real rather than theoretical.
- **`PRICE_REASONING_PER_MTOK is PRICE_OUTPUT_PER_MTOK`** — the same object, not
  two equal numbers, so no future edit can price them differently by accident.
- **402 is checked before 429.** Telling an unpaid customer they are out of quota
  is a lie that sends them to the wrong fix.
- **`Retry-After` counts down to the actual month rollover**, not a fixed backoff,
  because that is the first moment a retry can succeed.
- **The webhook route never declares a Pydantic body model.** Declaring one would
  let FastAPI parse and re-serialise the payload, and signature verification is
  over the exact bytes Stripe sent.

---

## What I could not fully verify

Live Stripe Checkout has not been run against a real Stripe account — no keys were
configured while building. The integration is complete and wired end to end, and
signature verification, the tolerance window, replay dedup and the Free → Pro flip
are proven with locally HMAC-signed webhooks (`tools/sign_webhook.py`), which
exercises the same code path Stripe's own delivery would. This is stated in the
README's *Limitations* and in `EVIDENCE.md` rather than glossed over.
