-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 005 — Complete Dynamization (Memory config, Regex & Currency overrides)
--
-- WHAT THIS COVERS:
--   Adds columns to `tenants` table to support dynamic memory strategies,
--   TTL weights, regex-based pronoun/number selection overrides, and currency symbol.
--   Seeds defaults for `tenant_inventaa_led_001`.
--
-- Run this in Supabase SQL Editor. Safe to re-run.
-- ══════════════════════════════════════════════════════════════════════════════

-- ── STEP 1: Add new columns to tenants table (safe — IF NOT EXISTS) ───────────

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS memory_ttl_config          JSONB DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS memory_importance_weights  JSONB DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS memory_deny_workflows      JSONB DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS memory_allow_intents       JSONB DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS memory_types_by_intent     JSONB DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS memory_default_types       JSONB DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS quantity_ctx_regex          TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS number_selection_regex      TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS comparison_ctx_regex        TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS number_replacement_pattern  TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS currency_symbol             TEXT DEFAULT 'Rs.';


-- ── STEP 2: Seed defaults for Inventaa LED Lights ─────────────────────────────

UPDATE tenants SET
    memory_ttl_config = '{
        "workflow_snapshot": 1800,
        "product_context": 7200,
        "conversation": 2592000,
        "negotiation_outcome": 7776000,
        "customer_preference": 31536000,
        "purchase_history": null
    }'::jsonb,
    memory_importance_weights = '{
        "workflow_snapshot": 1.0,
        "product_context": 0.9,
        "negotiation_outcome": 0.8,
        "customer_preference": 0.7,
        "conversation": 0.5
    }'::jsonb,
    memory_deny_workflows = '["NEGOTIATING", "COUNTER_PRESENTED", "CONFIRMING", "ORDERING"]'::jsonb,
    memory_allow_intents = '["RECOMMENDATION", "PERSONALIZATION", "PREVIOUS_PURCHASE", "CUSTOMER_PROFILE"]'::jsonb,
    memory_types_by_intent = '{
        "RECOMMENDATION": [["customer_preference", "purchase_summary"], 5],
        "PERSONALIZATION": [["customer_preference"], 3],
        "PREVIOUS_PURCHASE": [["purchase_summary"], 5],
        "CUSTOMER_PROFILE": [["customer_preference", "negotiation_outcome"], 5]
    }'::jsonb,
    memory_default_types = '[["product_context", "customer_preference", "negotiation_outcome"], 3]'::jsonb,
    quantity_ctx_regex = '\b(units?|pieces?|pcs?|qty|quantity|of them)\b',
    number_selection_regex = '(?<![\d])(?:#|no\.?\s*|sr\.?\s*|option\s+|product\s+|item\s+|number\s+)?(\d+)(?![\d])',
    comparison_ctx_regex = '\b(compare|vs\.?|versus|difference|better|which)\b',
    number_replacement_pattern = '(?:#|no\.?\s*|sr\.?\s*|option\s+|product\s+|item\s+|number\s+)?\b{num}\b',
    currency_symbol = 'Rs.'
WHERE tenant_id = 'tenant_inventaa_led_001';
