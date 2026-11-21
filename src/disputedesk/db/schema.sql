-- DisputeDesk app schema. Idempotent: applied by disputedesk.db.apply_schema().
-- All data is synthetic. Money is integer cents.

CREATE TABLE IF NOT EXISTS customers (
    customer_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    email        TEXT NOT NULL,
    card_last4   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS merchants (
    merchant_id          TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    category             TEXT NOT NULL,
    mcc                  TEXT NOT NULL,
    country              TEXT NOT NULL,
    ships_physical_goods BOOLEAN NOT NULL,
    risk_tier            TEXT NOT NULL CHECK (risk_tier IN ('low', 'medium', 'high')),
    onboarded_at         TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    txn_id       TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers (customer_id),
    merchant_id  TEXT NOT NULL REFERENCES merchants (merchant_id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    currency     TEXT NOT NULL,
    channel      TEXT NOT NULL,
    status       TEXT NOT NULL,
    description  TEXT NOT NULL,
    posted_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS transactions_customer_idx ON transactions (customer_id, posted_at);
CREATE INDEX IF NOT EXISTS transactions_merchant_idx ON transactions (merchant_id, posted_at);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_id     TEXT PRIMARY KEY,
    txn_id          TEXT NOT NULL UNIQUE REFERENCES transactions (txn_id),
    carrier         TEXT NOT NULL,
    tracking_number TEXT NOT NULL,
    status          TEXT NOT NULL,
    shipped_at      TIMESTAMPTZ NOT NULL,
    delivered_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS cases (
    case_id            TEXT PRIMARY KEY,
    txn_id             TEXT NOT NULL UNIQUE REFERENCES transactions (txn_id),
    customer_id        TEXT NOT NULL REFERENCES customers (customer_id),
    reason_code        TEXT NOT NULL,
    amount_cents       INTEGER NOT NULL CHECK (amount_cents > 0),
    customer_narrative TEXT NOT NULL,  -- untrusted customer text
    status             TEXT NOT NULL,
    opened_at          TIMESTAMPTZ NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS case_drafts (
    case_id    TEXT NOT NULL REFERENCES cases (case_id),
    version    INTEGER NOT NULL CHECK (version >= 1),
    decision   TEXT NOT NULL,
    rationale  TEXT NOT NULL,
    letter     TEXT NOT NULL,
    author     TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (case_id, version)
);

-- One row per MCP tool call, agent decision and API action (success or failure).
-- Append-only: the triggers below reject UPDATE, DELETE and TRUNCATE for every role.
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL,
    actor       TEXT NOT NULL,
    server      TEXT NOT NULL,
    tool        TEXT NOT NULL,
    case_id     TEXT,
    arguments   JSONB NOT NULL,
    outcome     TEXT NOT NULL,
    error       TEXT,
    duration_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_log_case_idx ON audit_log (case_id, created_at);

CREATE OR REPLACE FUNCTION audit_log_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log;
CREATE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_reject_change();

DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;
CREATE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_reject_change();

-- Columns added after the first release. NULL means "not recorded". Idempotent, so existing
-- databases pick them up the next time the schema is applied.
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS entry_mode TEXT;
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS delivery_address_matches BOOLEAN;
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS signature_confirmed BOOLEAN;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS fraud_declaration_signed BOOLEAN;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS card_reissued BOOLEAN;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS card_reported_lost_at TIMESTAMPTZ;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS merchant_contacted_at TIMESTAMPTZ;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS return_tracking_provided BOOLEAN;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS merchant_refused_return BOOLEAN;
