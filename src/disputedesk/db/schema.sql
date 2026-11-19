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

-- One row per MCP tool call (success or failure). Append-only enforcement lands in M5.
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
