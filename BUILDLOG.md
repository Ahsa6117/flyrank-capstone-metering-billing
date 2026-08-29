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

## Attacking my own service, and what broke

With everything green I went looking for what the tests were *not* covering,
rather than re-running what already passed. Three probes against the running
service; one of them found a real bug.

### The over-quota race — a genuine, shipped bug

Every concurrency test I had written used **one** idempotency key, so they all
proved the same property: a retried request is metered once. That is only half
the problem. Nothing tested many **different** keys arriving at once.

So I drove a tenant to 999 of 1,000 calls and fired 12 simultaneous requests,
each with its own key:

```
responses  : {200: 7, 429: 5}
final usage: 1006/1000
```

Seven got through where one should have. **Six calls given away free.** This is
the second failure mode the brief names — *"giving away unlimited access"* — and
my own `DESIGN.md` asserted it could not happen:

> "the check plus the write happen in one transaction, so two concurrent
> requests at the boundary cannot both observe `used = 999`"

That sentence was wrong, and being in one transaction is exactly why I believed
it. A transaction gives atomicity, not isolation from a concurrent reader: the
quota check is a `SELECT` and the meter is an `INSERT`, and two requests happily
interleave between them. Both read 999, both think they have room, both write.

The unique index that makes idempotency airtight is no help at all here, because
there is no duplicate to detect — these are genuinely different requests.

**The fix** (`migrations/002`, `TenantRepository.lock_for_metering`): the
metering transaction now opens by bumping `tenants.metering_lock`. The value is
never read; incrementing it is what takes a write lock, held to commit — a row
lock on Postgres, the database write lock on SQLite. A second request for the
same tenant blocks on that statement and so reads usage only after the first has
committed.

Ordering is the whole trick. Locking *after* the quota read would achieve
nothing, because the stale read has already happened. Two supporting details:
the gates roll back on rejection so a 429 leaves no trace of the lock, and
SQLite needed `PRAGMA busy_timeout` so the waiting request waits instead of
failing with "database is locked" — which would have turned a contended request
into a 500.

Same attack after the fix:

```
responses  : {200: 1, 429: 11}
final usage: 1000/1000
```

### Two probes that found nothing

Worth recording, because "I checked and it was fine" is also a result:

- **Month boundaries.** Events written 5 days before, 1 second before, exactly
  at, and 1 hour into the window: the rollup counted exactly the last two. The
  lower bound is inclusive, the upper exclusive, and SQLite did not mangle the
  timezone-aware timestamps I half-expected it to.
- **A key rejected for quota.** I expected to find that a 429 poisons a key
  forever, since `REFERENCES.md` I1 quotes Stripe storing the first outcome
  *"regardless of whether it succeeds or fails"*. It does not — the gates raise
  before anything is stored, so a customer who upgrades and retries the same key
  is served. The **code was right and the doc was wrong**, so I wrote the
  divergence down as rule I7 rather than "fixing" working behaviour to match a
  sentence. Pinning a transient failure to a key permanently would be a bug, not
  compliance.

### What I take from this

The tests were not weak because they were badly written; they were weak because
they all attacked the same axis. Idempotency and quota look like one problem
("don't let the same thing happen twice") and are actually two, with two
different mechanisms. I would not have found it by reading the code — I found it
by trying to break the running service, which is the only way this class of bug
ever shows up.

Also: `/health` returned `ok` without touching the database. `capstone.yaml`
points a probe at it, so it now runs `SELECT 1` and reports `degraded` if the
database is unreachable. Small, but a health check that cannot fail is a lie.

---

## Verifying it against real Stripe

The build was finished before any Stripe account existed, so the whole webhook
path was first proven offline with `tools/sign_webhook.py`, which builds a real
HMAC-SHA256 `Stripe-Signature` from a local secret. That was deliberate: it meant
signature verification, the tolerance window and replay dedup were all testable
with no account, and it is still in the repo so anyone can reproduce those proofs
with no account either.

Afterwards a Stripe **sandbox** was created and the same paths were run for real:

- a hosted Checkout paid with `4242 4242 4242 4242` (Sandbox — no real money)
- `stripe listen` forwarding genuinely signed events to `localhost:8000`
- the tenant flipping Free → Pro off `checkout.session.completed`
- a **real** event replayed with a valid signature → `duplicate_ignored`, and the
  same event with a forged signature → `400`

Two things I only learned by running it live, which no offline harness would have
surfaced:

1. **One checkout produces 13 events, unordered.** `invoice.created`,
   `charge.succeeded`, `payment_intent.*` and so on all arrive alongside the one
   event I care about. The "unhandled event type is a `200` no-op" decision, which
   had looked like defensive nicety, turned out to be load-bearing: returning an
   error to those nine would have had Stripe retrying them for three days.
2. **`stripe.Event.retrieve()` is not a dict.** `dict(event)` raises
   `TypeError: Event is not iterable or a mapping`. The same class of SDK-shape
   assumption that bit me earlier with `to_dict_recursive`. Fixed with
   `event.to_dict()`.

Sandbox rather than a plain unactivated account, on purpose: a sandbox cannot
become live mode by a misclick. Belt and braces with the `sk_live_` startup
guard.
