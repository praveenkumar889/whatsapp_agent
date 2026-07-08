-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 002: Context Builder Dynamic Configuration Columns
-- ══════════════════════════════════════════════════════════════════════════════

-- ── STEP 1: Add new configuration columns to tenants table ────────────────────
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS cb_product_marker             TEXT DEFAULT 'PRODUCT_CONTEXT:',
    ADD COLUMN IF NOT EXISTS cb_workflow_marker            TEXT DEFAULT 'WORKFLOW_SNAPSHOT:',
    ADD COLUMN IF NOT EXISTS cb_neg_outcome_marker         TEXT DEFAULT 'NEG_OUTCOME:',
    ADD COLUMN IF NOT EXISTS cb_product_format             TEXT DEFAULT '{name} | Rs.{price} | Warranty: {warranty} | Waterproof: {waterproof}',
    ADD COLUMN IF NOT EXISTS cb_workflow_format_state      TEXT DEFAULT 'State: {state}',
    ADD COLUMN IF NOT EXISTS cb_workflow_format_product    TEXT DEFAULT ' — {product} x{quantity}',
    ADD COLUMN IF NOT EXISTS cb_workflow_format_price      TEXT DEFAULT ' @ Rs.{offer_price}',
    ADD COLUMN IF NOT EXISTS cb_preferences_prefix         TEXT DEFAULT 'Preferences — ',
    ADD COLUMN IF NOT EXISTS cb_neg_profile_format         TEXT DEFAULT 'Typically accepts {avg_discount_pct}% discount',
    ADD COLUMN IF NOT EXISTS cb_neg_profile_rounds         TEXT DEFAULT ' in {typical_rounds} rounds',
    ADD COLUMN IF NOT EXISTS cb_neg_profile_budget         TEXT DEFAULT ', budget {budget_range}',
    ADD COLUMN IF NOT EXISTS cb_conversation_limit         INTEGER DEFAULT 2,
    ADD COLUMN IF NOT EXISTS cb_conversation_max_len       INTEGER DEFAULT 100,
    ADD COLUMN IF NOT EXISTS cb_exclude_markers            TEXT DEFAULT 'PRODUCT_CONTEXT:,WORKFLOW_SNAPSHOT:,NEG_OUTCOME:';

-- ── STEP 2: Backfill / update default tenant configurations ───────────────────
UPDATE tenants SET
    cb_product_marker             = 'PRODUCT_CONTEXT:',
    cb_workflow_marker            = 'WORKFLOW_SNAPSHOT:',
    cb_neg_outcome_marker         = 'NEG_OUTCOME:',
    cb_product_format             = '{name} | Rs.{price} | Warranty: {warranty} | Waterproof: {waterproof}',
    cb_workflow_format_state      = 'State: {state}',
    cb_workflow_format_product    = ' — {product} x{quantity}',
    cb_workflow_format_price      = ' @ Rs.{offer_price}',
    cb_preferences_prefix         = 'Preferences — ',
    cb_neg_profile_format         = 'Typically accepts {avg_discount_pct}% discount',
    cb_neg_profile_rounds         = ' in {typical_rounds} rounds',
    cb_neg_profile_budget         = ', budget {budget_range}',
    cb_conversation_limit         = 2,
    cb_conversation_max_len       = 100,
    cb_exclude_markers            = 'PRODUCT_CONTEXT:,WORKFLOW_SNAPSHOT:,NEG_OUTCOME:'
WHERE tenant_id = 'tenant_inventaa_led_001';
