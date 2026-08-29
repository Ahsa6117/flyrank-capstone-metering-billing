-- 002 · per-tenant metering lock
--
-- Fixes an over-quota race. The quota check reads current usage, then the
-- insert writes it. Between those two statements a second request can read the
-- same total, so two callers at the boundary both see "one call left" and both
-- proceed. Under load that hands out free usage: measured 1006/1000 with 12
-- concurrent requests before this change.
--
-- Idempotency keys do NOT help here -- each request is a genuinely different
-- one. Only serialising the read-then-write per tenant does.
--
-- This column is never read for its value. Bumping it as the first statement of
-- the metering transaction takes a write lock that is held until commit:
-- a row lock on Postgres, the database write lock on SQLite. A second request
-- for the same tenant blocks on that UPDATE and therefore performs its quota
-- read only after the first has committed.

ALTER TABLE tenants ADD COLUMN metering_lock INTEGER NOT NULL DEFAULT 0;
