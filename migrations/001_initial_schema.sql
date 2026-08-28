-- 001 · initial schema
--
-- Tenants, plans, subscriptions and usage events, plus the two uniqueness
-- constraints the whole system's correctness rests on:
--   uq_idempotency_tenant_key  -> a retried billable request is metered once
--   processed_webhook_events   -> a replayed Stripe event is processed once
--
-- Money is stored as INTEGER micro-cents. There is deliberately no REAL/FLOAT
-- column in this schema (docs/REFERENCES.md M1).

CREATE TABLE IF NOT EXISTS plans (
    code             TEXT    PRIMARY KEY,
    name             TEXT    NOT NULL,
    quota_api_calls  INTEGER NOT NULL,
    quota_tokens     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tenants (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    -- SHA-256 of the API key; the plaintext key is never stored.
    api_key_hash  TEXT NOT NULL UNIQUE,
    plan_code     TEXT NOT NULL DEFAULT 'free' REFERENCES plans(code),
    created_at    TIMESTAMP NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_tenants_api_key_hash ON tenants(api_key_hash);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                      TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL UNIQUE REFERENCES tenants(id),
    plan_code               TEXT NOT NULL REFERENCES plans(code),
    stripe_customer_id      TEXT,
    stripe_subscription_id  TEXT UNIQUE,
    status                  TEXT NOT NULL DEFAULT 'active',
    current_period_end      TIMESTAMP,
    updated_at              TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_subscriptions_tenant_id ON subscriptions(tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_subscriptions_stripe_sub
    ON subscriptions(stripe_subscription_id);

CREATE TABLE IF NOT EXISTS usage_events (
    id                   TEXT PRIMARY KEY,
    tenant_id            TEXT NOT NULL REFERENCES tenants(id),
    event_type           TEXT NOT NULL,
    api_calls            INTEGER NOT NULL DEFAULT 0,
    -- The four token categories stay separate: pricing needs them apart.
    input_tokens         INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens        INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens     INTEGER NOT NULL DEFAULT 0,
    cost_micro_cents     INTEGER NOT NULL DEFAULT 0,
    idempotency_key      TEXT,
    occurred_at          TIMESTAMP NOT NULL
);

-- Every rollup is "this tenant, this month".
CREATE INDEX IF NOT EXISTS ix_usage_events_tenant_id ON usage_events(tenant_id);
CREATE INDEX IF NOT EXISTS ix_usage_events_tenant_occurred
    ON usage_events(tenant_id, occurred_at);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    id                   TEXT PRIMARY KEY,
    tenant_id            TEXT NOT NULL REFERENCES tenants(id),
    idempotency_key      TEXT NOT NULL,
    request_fingerprint  TEXT NOT NULL,
    status_code          INTEGER NOT NULL,
    response_body        TEXT NOT NULL,
    usage_event_id       TEXT REFERENCES usage_events(id),
    created_at           TIMESTAMP NOT NULL
);

-- THE no-double-count guarantee. The database, not application logic, is the
-- arbiter of "this request was already metered".
CREATE UNIQUE INDEX IF NOT EXISTS uq_idempotency_tenant_key
    ON idempotency_keys(tenant_id, idempotency_key);
CREATE INDEX IF NOT EXISTS ix_idempotency_tenant_id ON idempotency_keys(tenant_id);

-- Webhook replay protection, keyed on Stripe's event.id (never on created).
CREATE TABLE IF NOT EXISTS processed_webhook_events (
    event_id      TEXT PRIMARY KEY,
    event_type    TEXT NOT NULL,
    processed_at  TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_alerts (
    id                 TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL REFERENCES tenants(id),
    usage_type         TEXT NOT NULL,
    threshold_percent  INTEGER NOT NULL,
    period_start       TIMESTAMP NOT NULL,
    message            TEXT NOT NULL,
    created_at         TIMESTAMP NOT NULL
);

-- One alert per tenant per usage type per threshold per month: a re-run of the
-- background job cannot duplicate it.
CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_alert_once
    ON usage_alerts(tenant_id, usage_type, threshold_percent, period_start);
CREATE INDEX IF NOT EXISTS ix_usage_alerts_tenant_id ON usage_alerts(tenant_id);

CREATE TABLE IF NOT EXISTS job_runs (
    id           TEXT PRIMARY KEY,
    job_name     TEXT NOT NULL,
    status       TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 1,
    detail       TEXT NOT NULL DEFAULT '',
    started_at   TIMESTAMP NOT NULL,
    finished_at  TIMESTAMP
);

INSERT INTO plans (code, name, quota_api_calls, quota_tokens)
VALUES ('free', 'Free', 1000, 100000)
ON CONFLICT(code) DO NOTHING;

INSERT INTO plans (code, name, quota_api_calls, quota_tokens)
VALUES ('pro', 'Pro', 50000, 5000000)
ON CONFLICT(code) DO NOTHING;
