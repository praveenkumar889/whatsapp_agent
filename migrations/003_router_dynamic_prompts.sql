-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 003 — router.py Dynamic Prompts & Config
--
-- WHAT THIS COVERS:
--   pipeline/router.py reads 7 prompt keys from the database via get_prompt().
--   All hardcoded fallbacks in pipeline/router.py have been REMOVED.
--   Seeding these prompts here is MANDATORY for the router to work.
--
--   Additionally, it adds the `default_quantity_unit` configuration column
--   to the tenants table to support different business domains (LED, Hospitals, etc.)
--
-- PROMPT KEYS COVERED:
--   1. neg_still_bargaining_prompt            (_neg_guard Phase 2)
--   2. order_summary_full_discount_prompt     (_build_order_summary)
--   3. order_summary_store_discount_only_prompt (_build_order_summary)
--   4. order_summary_plain_price_prompt       (_build_order_summary)
--   5. order_summary_savings_line_prompt      (_build_order_summary)
--   6. order_summary_footer_prompt            (_build_order_summary)
--   7. fast_order_confirm_check_prompt        (_check_fast_confirm)
--
-- CONFIG COLUMNS COVERED:
--   - default_quantity_unit                   (e.g., 'units', 'strips', 'boxes')
--
-- Run this ONE file in Supabase SQL Editor.
-- Safe to re-run — uses ADD COLUMN IF NOT EXISTS and ON CONFLICT DO NOTHING.
-- ══════════════════════════════════════════════════════════════════════════════


-- ── STEP 1: Add new columns to tenants (safe — IF NOT EXISTS) ─────────────────

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS neg_still_bargaining_prompt              TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS order_summary_full_discount_prompt       TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS order_summary_store_discount_only_prompt TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS order_summary_plain_price_prompt         TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS order_summary_savings_line_prompt        TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS order_summary_footer_prompt              TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS fast_order_confirm_check_prompt          TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS default_quantity_unit                    TEXT DEFAULT 'units';


-- ── STEP 2: Seed prompts and config for Inventaa LED Lights ───────────────────
--
-- VARIABLES REFERENCE per prompt:
--   neg_still_bargaining_prompt        → {sender_name} {final_price} {product} {quantity} {total}
--   order_summary_full_discount_prompt → {sender_name} {product} {qty} {price_raw} {auto_pct}
--                                        {auto_unit} {agreed_price} {sub} {gst_pct} {gst} {tot}
--   order_summary_store_discount_only  → {sender_name} {product} {qty} {price_raw}
--                                        {auto_pct} {auto_unit} {sub} {gst_pct} {gst} {tot}
--   order_summary_plain_price_prompt   → {sender_name} {product} {qty} {agreed_price}
--                                        {sub} {gst_pct} {gst} {tot}
--   order_summary_savings_line_prompt  → {tot_save}
--   order_summary_footer_prompt        → (no variables)
--   fast_order_confirm_check_prompt    → (no variables)

UPDATE tenants SET

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. neg_still_bargaining_prompt
--    Fired when customer keeps bargaining AFTER bot already presented final price.
--    Goal: politely hold the current price without being rude.
--    Variables: {sender_name} {final_price} {product} {quantity} {total}
-- ─────────────────────────────────────────────────────────────────────────────
neg_still_bargaining_prompt = 'I completely understand, {sender_name}. 🙏

*Rs.{final_price}/unit* is genuinely our absolute best price for *{product}* — we cannot go lower than this.

For *{quantity} units*, your total would be *Rs.{total}* + GST.

Would you like to proceed at this price? Just reply *Confirm* and I''ll prepare your order right away! 😊',


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. order_summary_full_discount_prompt
--    Shown when BOTH a store discount (auto_pct) AND a negotiated reduction
--    (n_save > 0) are applied. Shows all three price tiers.
--    Variables: {sender_name} {product} {qty} {price_raw} {auto_pct} {auto_unit}
--               {agreed_price} {sub} {gst_pct} {gst} {tot}
-- ─────────────────────────────────────────────────────────────────────────────
order_summary_full_discount_prompt = 'Here''s your order summary, {sender_name}! 🎉

• *Product:* {product}
• *Quantity:* {qty} units
• *Regular price:* Rs.{price_raw}/unit
• *Store offer ({auto_pct}% OFF):* Rs.{auto_unit}/unit
• *Your negotiated price:* Rs.{agreed_price}/unit
• *Subtotal:* Rs.{sub}
• *GST ({gst_pct}%):* Rs.{gst}
• *Total Payable:* Rs.{tot}',


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. order_summary_store_discount_only_prompt
--    Shown when store discount applies but NO additional negotiated reduction.
--    Variables: {sender_name} {product} {qty} {price_raw} {auto_pct} {auto_unit}
--               {sub} {gst_pct} {gst} {tot}
-- ─────────────────────────────────────────────────────────────────────────────
order_summary_store_discount_only_prompt = 'Here''s your order summary, {sender_name}! 🎉

• *Product:* {product}
• *Quantity:* {qty} units
• *Regular price:* Rs.{price_raw}/unit
• *Store offer ({auto_pct}% OFF):* Rs.{auto_unit}/unit
• *Subtotal:* Rs.{sub}
• *GST ({gst_pct}%):* Rs.{gst}
• *Total Payable:* Rs.{tot}',


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. order_summary_plain_price_prompt
--    Shown when NO store discount applies — agreed/negotiated price only.
--    Variables: {sender_name} {product} {qty} {agreed_price} {sub} {gst_pct} {gst} {tot}
-- ─────────────────────────────────────────────────────────────────────────────
order_summary_plain_price_prompt = 'Here''s your order summary, {sender_name}! 🎉

• *Product:* {product}
• *Quantity:* {qty} units
• *Price per unit:* Rs.{agreed_price}
• *Subtotal:* Rs.{sub}
• *GST ({gst_pct}%):* Rs.{gst}
• *Total Payable:* Rs.{tot}',


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. order_summary_savings_line_prompt
--    Appended below the summary block when customer saved money vs list price.
--    Variables: {tot_save}
-- ─────────────────────────────────────────────────────────────────────────────
order_summary_savings_line_prompt = '🎁 *You save Rs.{tot_save} on this order!*',


-- ─────────────────────────────────────────────────────────────────────────────
-- 6. order_summary_footer_prompt
--    Call-to-action line appended at the end of every order summary.
--    No variables — plain CTA text only. Customize per tenant.
-- ─────────────────────────────────────────────────────────────────────────────
order_summary_footer_prompt = 'Reply *Confirm* to place your order and receive your invoice instantly! 📄',


-- ─────────────────────────────────────────────────────────────────────────────
-- 7. fast_order_confirm_check_prompt
--    Used by pipeline/router.py _check_fast_confirm() — binary YES/NO
--    classifier: is the customer fast-confirming the order?
--    No variables — customer message is passed as user content directly.
-- ─────────────────────────────────────────────────────────────────────────────
fast_order_confirm_check_prompt = 'The bot just showed the customer an order summary and asked them to reply "Confirm" or "Proceed" to place their order and receive their invoice.

Is the customer''s message a confirmation to place the order?

Reply YES if:
- Customer says: "confirm", "proceed", "yes", "ok", "okay", "sure", "go ahead", "done", "place it", "book it", "do it", "let''s go", "accept"
- Customer uses thumbs up emoji (👍, 👌)
- Customer says "confirmed at [price]", "ok proceed with [price]"
- Any variation that clearly means they want to finalise the order

Reply NO if:
- Customer is changing quantity ("add 2 more", "make it 10 units")
- Customer is negotiating further ("can you reduce more?", "what about Rs.1500?")
- Customer is asking a question ("how long to deliver?", "is this available?")
- Customer says "cancel", "never mind", "hold on"

Reply ONLY "YES" or "NO".',

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. default_quantity_unit
--    Sets the default quantity unit for the business (e.g. 'units', 'strips', 'boxes')
-- ─────────────────────────────────────────────────────────────────────────────
default_quantity_unit = 'units'

WHERE tenant_id = 'tenant_inventaa_led_001';


-- ══════════════════════════════════════════════════════════════════════════════
-- STEP 3: Register new prompts in prompt_templates (normalised prompt store)
--
-- The application reads prompt_templates FIRST, then falls back to tenants
-- columns. Inserting here makes prompts immediately available on the hot path.
-- ══════════════════════════════════════════════════════════════════════════════

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
SELECT
    t.tenant_id,
    col.prompt_name,
    'en',
    1,
    'active',
    col.prompt_text,
    col.variables,
    col.description
FROM tenants t,
LATERAL (VALUES

    ('neg_still_bargaining_prompt',
     t.neg_still_bargaining_prompt,
     'sender_name,final_price,product,quantity,total',
     'Holds the final price politely when customer keeps bargaining after final offer'),

    ('order_summary_full_discount_prompt',
     t.order_summary_full_discount_prompt,
     'sender_name,product,qty,price_raw,auto_pct,auto_unit,agreed_price,sub,gst_pct,gst,tot',
     'Order summary when both store discount and negotiated reduction apply'),

    ('order_summary_store_discount_only_prompt',
     t.order_summary_store_discount_only_prompt,
     'sender_name,product,qty,price_raw,auto_pct,auto_unit,sub,gst_pct,gst,tot',
     'Order summary when only store discount applies with no additional negotiation'),

    ('order_summary_plain_price_prompt',
     t.order_summary_plain_price_prompt,
     'sender_name,product,qty,agreed_price,sub,gst_pct,gst,tot',
     'Order summary when no store discount applies — agreed price shown directly'),

    ('order_summary_savings_line_prompt',
     t.order_summary_savings_line_prompt,
     'tot_save',
     'Savings line appended below order summary when customer saved money vs list price'),

    ('order_summary_footer_prompt',
     t.order_summary_footer_prompt,
     '',
     'Call-to-action footer appended to every order summary — tells customer to reply Confirm'),

    ('fast_order_confirm_check_prompt',
     t.fast_order_confirm_check_prompt,
     '',
     'Binary YES/NO classifier — detects fast-confirm intent after order summary shown')

) AS col(prompt_name, prompt_text, variables, description)
WHERE t.tenant_id = 'tenant_inventaa_led_001'
  AND col.prompt_text IS NOT NULL
  AND col.prompt_text <> ''
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;


-- ── STEP 4: Verify all 7 prompts and config seeded in tenants table ───────────

SELECT
    tenant_id,
    biz_name,
    default_quantity_unit,
    CASE WHEN neg_still_bargaining_prompt              IS NOT NULL THEN 'OK' ELSE 'MISSING' END AS neg_still_bargaining,
    CASE WHEN order_summary_full_discount_prompt       IS NOT NULL THEN 'OK' ELSE 'MISSING' END AS summary_full_discount,
    CASE WHEN order_summary_store_discount_only_prompt IS NOT NULL THEN 'OK' ELSE 'MISSING' END AS summary_store_only,
    CASE WHEN order_summary_plain_price_prompt         IS NOT NULL THEN 'OK' ELSE 'MISSING' END AS summary_plain,
    CASE WHEN order_summary_savings_line_prompt        IS NOT NULL THEN 'OK' ELSE 'MISSING' END AS savings_line,
    CASE WHEN order_summary_footer_prompt              IS NOT NULL THEN 'OK' ELSE 'MISSING' END AS footer,
    CASE WHEN fast_order_confirm_check_prompt          IS NOT NULL THEN 'OK' ELSE 'MISSING' END AS fast_confirm_check
FROM tenants
WHERE tenant_id = 'tenant_inventaa_led_001';


-- ── STEP 5: Verify prompt_templates rows ──────────────────────────────────────

SELECT
    prompt_name,
    language,
    version,
    status,
    LEFT(prompt_text, 80) AS preview
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name IN (
      'neg_still_bargaining_prompt',
      'order_summary_full_discount_prompt',
      'order_summary_store_discount_only_prompt',
      'order_summary_plain_price_prompt',
      'order_summary_savings_line_prompt',
      'order_summary_footer_prompt',
      'fast_order_confirm_check_prompt'
  )
ORDER BY prompt_name;


-- ══════════════════════════════════════════════════════════════════════════════
-- HOW TO ADD A SECOND TENANT (example: a hospital)
-- ══════════════════════════════════════════════════════════════════════════════
-- Copy this UPDATE block, set your tenant_id, customise the wording.
-- No ALTER TABLE needed — columns already exist from STEP 1.
--
-- UPDATE tenants SET
--
-- default_quantity_unit = 'strips',
--
-- neg_still_bargaining_prompt = 'I understand, {sender_name}.
-- *Rs.{final_price}/unit* is our best price for *{product}* — we cannot go lower.
-- For *{quantity} units* the total is *Rs.{total}* + applicable taxes.
-- Would you like to proceed? Reply *Confirm* to place the order.',
--
-- order_summary_full_discount_prompt = 'Order summary, {sender_name}:
-- • *Item:* {product}
-- • *Quantity:* {qty} units
-- • *List price:* Rs.{price_raw}/unit
-- • *Institutional discount ({auto_pct}% OFF):* Rs.{auto_unit}/unit
-- • *Negotiated price:* Rs.{agreed_price}/unit
-- • *Subtotal:* Rs.{sub}
-- • *GST ({gst_pct}%):* Rs.{gst}
-- • *Total:* Rs.{tot}',
--
-- order_summary_store_discount_only_prompt = 'Order summary, {sender_name}:
-- • *Item:* {product}
-- • *Quantity:* {qty} units
-- • *List price:* Rs.{price_raw}/unit
-- • *Institutional discount ({auto_pct}% OFF):* Rs.{auto_unit}/unit
-- • *Subtotal:* Rs.{sub}
-- • *GST ({gst_pct}%):* Rs.{gst}
-- • *Total:* Rs.{tot}',
--
-- order_summary_plain_price_prompt = 'Order summary, {sender_name}:
-- • *Item:* {product}
-- • *Quantity:* {qty} units
-- • *Unit price:* Rs.{agreed_price}
-- • *Subtotal:* Rs.{sub}
-- • *GST ({gst_pct}%):* Rs.{gst}
-- • *Total:* Rs.{tot}',
--
-- order_summary_savings_line_prompt = 'You saved Rs.{tot_save} on this order.',
--
-- order_summary_footer_prompt = 'Reply *Confirm* to finalise your order and receive your invoice. 📄',
--
-- fast_order_confirm_check_prompt = 'Bot showed order summary. Is customer confirming?
-- YES: confirm, proceed, yes, ok, done, book, 👍
-- NO: qty change, discount request, question, cancel
-- Reply ONLY "YES" or "NO".'
--
-- WHERE tenant_id = 'your_new_tenant_id_here';
--
-- Then re-run the INSERT INTO prompt_templates block above with the new tenant_id.
-- ══════════════════════════════════════════════════════════════════════════════
