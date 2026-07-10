-- MIGRATION 006: Structured Customer Data tables
-- Run this directly in your database to support dynamic customer history storage without Mem0.

CREATE TABLE IF NOT EXISTS customer_preferences (
    id           BIGSERIAL    PRIMARY KEY,
    tenant_id    TEXT         NOT NULL,
    session_id   TEXT         NOT NULL,
    pref_type    TEXT         NOT NULL,
    value        TEXT         NOT NULL,
    created_at   TIMESTAMPTZ  DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE (tenant_id, session_id, pref_type)
);

CREATE INDEX IF NOT EXISTS idx_customer_preferences_lookup
    ON customer_preferences (tenant_id, session_id);

CREATE TABLE IF NOT EXISTS negotiation_history (
    id             BIGSERIAL    PRIMARY KEY,
    tenant_id      TEXT         NOT NULL,
    session_id     TEXT         NOT NULL,
    product_name   TEXT         NOT NULL,
    initial_price  NUMERIC,
    final_price    NUMERIC,
    rounds         INTEGER      DEFAULT 0,
    accepted       BOOLEAN      DEFAULT FALSE,
    quantity       INTEGER      DEFAULT 1,
    counter_offers JSONB        DEFAULT '[]'::jsonb,
    created_at     TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_negotiation_history_lookup
    ON negotiation_history (tenant_id, session_id);

CREATE TABLE IF NOT EXISTS customer_offers (
    id               BIGSERIAL    PRIMARY KEY,
    tenant_id        TEXT         NOT NULL,
    session_id       TEXT         NOT NULL,
    product_name     TEXT         NOT NULL,
    offer_tier       TEXT,
    discount_applied NUMERIC,
    threshold        NUMERIC,
    accepted         BOOLEAN      DEFAULT FALSE,
    created_at       TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_customer_offers_lookup
    ON customer_offers (tenant_id, session_id);

CREATE TABLE IF NOT EXISTS product_views (
    id             BIGSERIAL    PRIMARY KEY,
    tenant_id      TEXT         NOT NULL,
    session_id     TEXT         NOT NULL,
    product_name   TEXT         NOT NULL,
    viewed_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_product_views_lookup
    ON product_views (tenant_id, session_id);
