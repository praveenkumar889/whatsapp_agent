-- migrations/010_invoice_keyword_shortcuts.sql
--
-- Adds INVOICE_INQUIRY keyword list to every tenant's quick_actions JSON.
--
-- WHY: pipeline/router.py _invoice_guard() previously called _is_invoice_inquiry()
-- (a full LLM call, ~2.5s) on EVERY message — even "help me order garden lights"
-- or "can you do 2000?". The new code skips that LLM call entirely when the
-- customer's message contains none of the keywords in INVOICE_INQUIRY.
--
-- If a tenant's quick_actions already has an INVOICE_INQUIRY key, this is a
-- no-op for that row (|| merges jsonb at the top level, preserving all other keys).
--
-- Customise per tenant by running an UPDATE after this migration:
--   UPDATE tenants
--   SET quick_actions = quick_actions || '{"INVOICE_INQUIRY": ["your","words","here"]}'::jsonb
--   WHERE tenant_id = 'your_tenant_id';

UPDATE tenants
SET quick_actions = COALESCE(quick_actions, '{}'::jsonb)
    || '{"INVOICE_INQUIRY": [
          "invoice",
          "bill",
          "receipt",
          "pdf",
          "download",
          "send invoice",
          "send me invoice",
          "my invoice",
          "where is my invoice",
          "get invoice",
          "payment",
          "tax invoice",
          "gst invoice",
          "order copy"
        ]}'::jsonb
WHERE tenant_id IS NOT NULL;
