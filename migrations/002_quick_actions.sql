-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 002: Tenant Quick Actions Configuration
--
-- Adds a generic `quick_actions` JSONB column to the `tenants` table.
-- This column maps action intent names (like "ORDER_CONFIRM", "ORDER_CANCEL")
-- to arrays of exact confirmation phrases. Bypasses LLMs for fast, deterministic,
-- and low-latency/low-cost matches.
-- ══════════════════════════════════════════════════════════════════════════════

-- Step 1: Add quick_actions JSONB column if it does not exist
ALTER TABLE tenants 
ADD COLUMN IF NOT EXISTS quick_actions JSONB DEFAULT NULL;

-- Step 2: Populate quick_actions configuration for the default tenant
UPDATE tenants 
SET quick_actions = '{
    "ORDER_CONFIRM": [
        "yes", "ok", "okay", "sure", "confirm", "confirmed",
        "proceed", "done", "accepted", "great", "perfect",
        "yep", "yup", "go ahead", "do it", "book it",
        "place it", "place order", "let''s do it",
        "send invoice", "generate invoice", "checkout"
    ],
    "ORDER_CANCEL": [
        "cancel", "stop", "nevermind", "abort", "no", "dont proceed", "dont confirm"
    ],
    "ORDER_RESTART": [
        "new order", "start over", "restart"
    ]
}'::jsonb
WHERE tenant_id = 'tenant_inventaa_led_001';

-- Step 3: Verify the changes
SELECT 
    tenant_id, 
    quick_actions->>'ORDER_CONFIRM' AS confirm_phrases_preview,
    quick_actions->>'ORDER_CANCEL' AS cancel_phrases_preview
FROM tenants
WHERE tenant_id = 'tenant_inventaa_led_001';

