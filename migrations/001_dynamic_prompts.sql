-- ══════════════════════════════════════════════════════════════════════════════
-- 1. TENANTS TABLE & DEFAULT INSERT
-- ══════════════════════════════════════════════════════════════════════════════

DROP TABLE IF EXISTS tenants CASCADE;

CREATE TABLE tenants (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL UNIQUE,
    phone_number_id TEXT NOT NULL UNIQUE,
    waba_id         TEXT,
    biz_name        TEXT,
    region          TEXT DEFAULT 'india',
    timezone        TEXT DEFAULT 'Asia/Kolkata',
    language        TEXT DEFAULT 'en',
    support_email   TEXT DEFAULT NULL,
    website         TEXT DEFAULT NULL,
    city            TEXT DEFAULT NULL,
    upi_id          TEXT DEFAULT NULL,
    account_name    TEXT DEFAULT NULL,
    tagline         TEXT DEFAULT NULL,
    gst_rate        NUMERIC DEFAULT 18,
    support_phone   TEXT DEFAULT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Insert the default tenant configuration
INSERT INTO tenants (
    tenant_id,
    phone_number_id,
    waba_id,
    biz_name,
    tagline,
    city,
    support_email,
    website,
    upi_id,
    account_name,
    gst_rate,
    support_phone,
    region,
    timezone,
    language
) VALUES (
    'tenant_inventaa_led_001',
    '1124766240726230',
    '206253068645466',
    'Inventaa LED Lights',
    'LED Lighting Solutions | Made in India',
    'Chennai, Tamil Nadu',
    'support@inventaa.in',
    'inventaa.in',
    'inventaa@upi',
    'Inventaa LED Innovation Pvt Ltd',
    18,
    '+91 72990 39181',
    'india',
    'Asia/Kolkata',
    'en'
);


-- ══════════════════════════════════════════════════════════════════════════════
-- 2. MESSAGES TABLE & INDEXES
-- ══════════════════════════════════════════════════════════════════════════════

DROP TABLE IF EXISTS messages CASCADE;

CREATE TABLE messages (
    id                   BIGSERIAL PRIMARY KEY,
    tenant_id            TEXT NOT NULL,
    message_id           TEXT NOT NULL UNIQUE,
    session_id           TEXT NOT NULL,
    channel              TEXT NOT NULL DEFAULT 'whatsapp',
    timestamp_unix       BIGINT,
    region               TEXT,
    direction            TEXT NOT NULL DEFAULT 'inbound',
    original_type        TEXT NOT NULL,
    text                 TEXT,
    media_url            TEXT,
    media_id             TEXT,
    media_mime_type      TEXT,
    intent               TEXT,
    confidence           FLOAT,
    product_name         TEXT,
    quantity_value       INTEGER,
    quantity_unit        TEXT,
    delivery_date        TEXT,
    invoice_number       TEXT,
    payment_reference    TEXT,
    missing_entities     TEXT,
    reply_text           TEXT,
    replied_at           TEXT,
    sender_name          TEXT,
    sender_phone_number  TEXT,
    trace_id             TEXT,
    received_at          TEXT,
    graphrag_response    TEXT DEFAULT NULL,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_messages_session ON messages(tenant_id, session_id, created_at DESC);
CREATE INDEX idx_messages_message_id ON messages(message_id);
CREATE INDEX idx_messages_intent ON messages(tenant_id, intent);
CREATE INDEX idx_messages_channel ON messages(tenant_id, channel);


-- ══════════════════════════════════════════════════════════════════════════════
-- 3. ORDERS TABLE & INDEXES
-- ══════════════════════════════════════════════════════════════════════════════

DROP TABLE IF EXISTS orders CASCADE;

CREATE TABLE orders (
    id              BIGSERIAL PRIMARY KEY,
    order_id        TEXT NOT NULL UNIQUE,      -- INV#XXXXX
    tenant_id       TEXT NOT NULL,
    session_id      TEXT NOT NULL,             -- customer phone
    sender_name     TEXT,
    product_name    TEXT NOT NULL,
    quantity_value  INTEGER DEFAULT NULL,      -- NULLable (Altered drop NOT NULL)
    quantity_unit   TEXT,
    unit_price      NUMERIC DEFAULT NULL,      -- NULLable (Altered drop NOT NULL)
    total_price     NUMERIC NOT NULL,          -- unit_price × quantity
    total_with_gst  NUMERIC NOT NULL,          -- total_price × 1.18
    status          TEXT DEFAULT 'CONFIRMED',
    invoice_url     TEXT DEFAULT NULL,
    items_count     INTEGER DEFAULT 1,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_orders_session  ON orders(tenant_id, session_id);
CREATE INDEX idx_orders_order_id ON orders(order_id);


-- ══════════════════════════════════════════════════════════════════════════════
-- 4. ORDER ITEMS TABLE & INDEXES
-- ══════════════════════════════════════════════════════════════════════════════

DROP TABLE IF EXISTS order_items CASCADE;

CREATE TABLE order_items (
    id             BIGSERIAL PRIMARY KEY,
    order_id       TEXT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    tenant_id      TEXT NOT NULL,
    product_name   TEXT NOT NULL,
    quantity_value INTEGER NOT NULL,
    quantity_unit  TEXT,
    unit_price     NUMERIC NOT NULL,
    total_price    NUMERIC NOT NULL,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_order_items_order_id ON order_items(order_id);


-- ══════════════════════════════════════════════════════════════════════════════
-- 5. PROCESSING LOCKS TABLE
-- ══════════════════════════════════════════════════════════════════════════════

DROP TABLE IF EXISTS processing_locks CASCADE;

CREATE TABLE processing_locks (
    session_id  TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    locked_at   TIMESTAMPTZ DEFAULT NOW()
);


-- ══════════════════════════════════════════════════════════════════════════════
-- 6. WORKFLOW SESSIONS TABLE & INDEXES
-- ══════════════════════════════════════════════════════════════════════════════

DROP TABLE IF EXISTS workflow_sessions CASCADE;

CREATE TABLE workflow_sessions (
    id             BIGSERIAL PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    session_id     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'WORKFLOW_PENDING',
    product_name   TEXT,
    quantity_value INTEGER,
    quantity_unit  TEXT,
    delivery_date  TEXT,
    missing_fields TEXT,
    items_json     TEXT DEFAULT NULL,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW(),
    expires_at     TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_workflow_session ON workflow_sessions(tenant_id, session_id, status);
CREATE INDEX idx_workflow_expires ON workflow_sessions(expires_at);


-- ══════════════════════════════════════════════════════════════════════════════
-- 7. PRODUCT CACHE TABLE & INDEXES
-- ══════════════════════════════════════════════════════════════════════════════

DROP TABLE IF EXISTS product_cache CASCADE;

CREATE TABLE product_cache (
    id           BIGSERIAL PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    sku          TEXT NOT NULL,
    api_response JSONB NOT NULL,
    cached_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, sku)
);

CREATE INDEX idx_product_cache_sku ON product_cache(tenant_id, sku);


-- ══════════════════════════════════════════════════════════════════════════════
-- 8. TENANT OFFERS TABLE & INDEXES
-- ══════════════════════════════════════════════════════════════════════════════

DROP TABLE IF EXISTS tenant_offers CASCADE;

CREATE TABLE tenant_offers (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   TEXT NOT NULL UNIQUE,
    offers_text TEXT NOT NULL,
    tiers_json  TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_tenant_offers_tenant ON tenant_offers(tenant_id);

SELECT * FROM tenants;
SELECT * FROM messages;
SELECT * FROM orders;
SELECT * FROM order_items;
SELECT * FROM processing_locks;
SELECT * FROM workflow_sessions;
SELECT * FROM product_cache;
SELECT * FROM tenant_offers;


-- 1. Clear orders and order_items (Cascaded due to foreign keys)
TRUNCATE TABLE order_items, orders CASCADE;

-- 2. Clear all other session and cache tables
TRUNCATE TABLE messages CASCADE;
TRUNCATE TABLE processing_locks CASCADE;
TRUNCATE TABLE workflow_sessions CASCADE;
TRUNCATE TABLE product_cache CASCADE;
TRUNCATE TABLE tenant_offers CASCADE;

-- 3. Clear tenants (Optional - usually keep this config populated)
TRUNCATE TABLE tenants CASCADE;





-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION — Add product_summary_recommendation_prompt
-- Run AFTER the combined migration (000_combined_single_migration.sql)
-- ══════════════════════════════════════════════════════════════════════════════

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS product_summary_recommendation_prompt TEXT DEFAULT NULL;


UPDATE tenants SET

product_summary_recommendation_prompt = 'You are a helpful WhatsApp sales assistant for {biz_name}.
The customer just updated their order quantity. Generate a SHORT product summary +
recommendation block to show alongside the price update.

PRODUCT DATA:
{product_data}

CRITICAL — DATA ACCURACY:
Use ONLY the exact values given in PRODUCT DATA above. Do NOT estimate, invent, or
round any rating, review count, warranty, or feature detail. If a field is empty or
missing, simply omit that line — do not make up a placeholder value.

FORMAT — keep it SHORT, max 4 lines total:
⭐ [rating]/5 ([review_count] reviews)
🛡️ [warranty — one short phrase, not the full legal text]
💡 [ONE standout feature from feature_descriptions, summarized in under 12 words]

Then end with ONE short recommendation sentence tailored to their current quantity,
e.g. "Great choice for [use case] — this is one of our most popular options!"
or if they are close to a better deal, you may mention it briefly in this single line.

RULES:
- Address the customer as {sender_name}
- Use plain WhatsApp formatting only (*, emojis) — no markdown tables, no headers
- Do NOT repeat the price — that is shown separately
- Do NOT include a "Reply Confirm" call to action — that is added separately
- Maximum 4 lines total, including the recommendation sentence
- Reply ONLY with the summary block text — no JSON, no explanation'

WHERE tenant_id = 'tenant_inventaa_led_001';


-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT
    CASE WHEN product_summary_recommendation_prompt IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS product_summary_prompt
FROM tenants
WHERE tenant_id = 'tenant_inventaa_led_001';


-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION — Update neg_counter_offer_prompt to highlight price improvement
-- Run in Supabase SQL Editor
-- ══════════════════════════════════════════════════════════════════════════════

UPDATE tenants SET
neg_counter_offer_prompt = 'You are a professional but warm sales negotiator for {biz_name}.
The customer has made a counter-offer and you are responding with your counter.

CONTEXT:
- Product: {product_name}
- Customer asked for: Rs.{customer_price}/unit
- Your previous offer: Rs.{previous_price}/unit
- Your new counter-offer: Rs.{new_offer}/unit
- Improvement from previous: Rs.{improvement}/unit saved
- Quantity: {quantity} units
- Total at new offer: Rs.{new_total}
- Negotiation round: {rounds}
- Instruction: {is_final_msg}

YOUR RESPONSE MUST:
1. Acknowledge their offer warmly but briefly
2. Highlight the IMPROVEMENT clearly — e.g. "We''ve moved from Rs.{previous_price} → Rs.{new_offer}/unit, saving you Rs.{improvement}/unit"
   This makes the concession obvious and shows good faith.
3. State the new offer price and total clearly
4. If NOT final: End with a soft question encouraging them to proceed
5. If IS final: Say this is the absolute best price, mention quality/value briefly

FORMAT:
- Address as {sender_name}
- WhatsApp formatting: *bold* for prices
- Max 4 lines
- Warm but professional tone — not pushy

Reply ONLY with the message text — no JSON, no preamble.'

WHERE tenant_id = 'tenant_inventaa_led_001';

-- Verify
SELECT LEFT(neg_counter_offer_prompt, 100) AS prompt_preview
FROM tenants WHERE tenant_id = 'tenant_inventaa_led_001';



-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 007 v5 (FINAL): product_name backfill for product_cache
--
-- ROOT CAUSE of all previous failures:
-- api_response is JSONB but stores a JSON-encoded STRING — i.e. the JSONB
-- value is of type 'string', whose text content is itself a JSON array.
-- So api_response::text = '"[{\"product_name\": \"Sandy LED...\"}]"'
-- (note the outer quotes — it's a JSONB string, not a JSONB array)
--
-- Fix: api_response #>> '{}' extracts the inner text of a JSONB string,
-- then cast that text to JSONB to get the actual array.
-- ══════════════════════════════════════════════════════════════════════════════

ALTER TABLE product_cache
    ADD COLUMN IF NOT EXISTS product_name TEXT DEFAULT NULL;

UPDATE product_cache
SET product_name = LOWER(COALESCE(
    -- Step 1: api_response #>> '{}' unwraps the outer JSONB string to text
    -- Step 2: ::jsonb re-parses that text as a real JSONB array
    -- Step 3: -> 0 ->> 'product_name' extracts the name from the first element
    ((api_response #>> '{}')::jsonb -> 0 ->> 'product_name'),
    ((api_response #>> '{}')::jsonb -> 0 ->> 'name'),
    ''
))
WHERE product_name IS NULL
  AND api_response IS NOT NULL
  AND jsonb_typeof(api_response) = 'string';

-- GIN trgm index for fast ilike search
CREATE EXTENSION IF NOT EXISTS pg_trgm;

DROP INDEX IF EXISTS idx_product_cache_product_name_trgm;
CREATE INDEX idx_product_cache_product_name_trgm
    ON product_cache USING GIN (product_name gin_trgm_ops);

DROP INDEX IF EXISTS idx_product_cache_tenant_product_name;
CREATE INDEX idx_product_cache_tenant_product_name
    ON product_cache (tenant_id, product_name);

-- Verify
SELECT
    COUNT(*)                       AS total_rows,
    COUNT(product_name)            AS rows_with_name,
    COUNT(*) - COUNT(product_name) AS rows_missing_name
FROM product_cache
WHERE tenant_id = 'tenant_inventaa_led_001';

-- Quick verification: confirm the indexed lookup now works
-- This is exactly the query session_store.py runs in get_cached_product_by_name()
SELECT sku, product_name
FROM product_cache
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND product_name ILIKE '%romy%'
LIMIT 1;

-- MIGRATION 008: Update negotiation prompts
-- Focus on CURRENT best offer + savings, not "previous → current" arrows.
-- Final price prompt: warmer, structured, invites acceptance.

UPDATE tenants SET

neg_counter_offer_prompt = 'You are a warm, professional sales negotiator for {biz_name}.
The customer made a counter-offer and you are presenting your improved price.

CONTEXT:
- Product: {product_name}
- Customer asked for: Rs.{customer_price}/unit
- Your current best offer: Rs.{new_offer}/unit
- Total for {quantity} units: Rs.{new_total}
- Round: {rounds}
- Instruction: {is_final_msg}

YOUR RESPONSE — 3–4 lines max:
1. Thank them briefly (1 short sentence)
2. State your current best price clearly: "Our current best is *Rs.{new_offer}/unit*"
3. State the total: "Total for {quantity} units: *Rs.{new_total}*"
4. If NOT final: soft question — "Does this work for you?"
5. If IS final: "This is our absolute best — cannot reduce further."

RULES:
- Address as {sender_name}
- Do NOT show "previous → current" price arrows
- Do NOT mention the improvement amount in rupees — just state the current price
- Keep it conversational and human
- Reply ONLY with the message text — no JSON, no explanation',

neg_final_price_prompt = 'You are a warm but firm sales negotiator for {biz_name}.
You have reached your absolute floor price.

CONTEXT:
- Product: {product_name}
- Final price: Rs.{last_offer}/unit
- Quantity: {quantity} units
- Total (before GST): Rs.{total}

YOUR RESPONSE — 4–5 lines:
1. Acknowledge their effort warmly (1 sentence)
2. "We''ve reached our absolute best price:"
3. "✔ *Rs.{last_offer}/unit*"
4. "✔ Total: *Rs.{total}* (+ GST)"
5. "If this works for you, I''ll prepare your order summary right away."

RULES:
- Address as {sender_name}
- Be warm but firm — no apology, no hedging
- Do NOT say "cannot be reduced" bluntly — say "this is our best"
- Reply ONLY with the message text'

WHERE tenant_id = 'tenant_inventaa_led_001';

SELECT 'Prompts updated' AS status;

-- Update counter-offer prompt to use customer_offer_note (budget trajectory)
UPDATE tenants SET
neg_counter_offer_prompt = 'You are a warm, professional sales negotiator for {biz_name}.
The customer made a counter-offer and you are presenting your improved price.

CONTEXT:
- Product: {product_name}
- Customer asked for: Rs.{customer_price}/unit
- Your current best offer: Rs.{new_offer}/unit
- Total for {quantity} units: Rs.{new_total}
- Customer budget trajectory: {customer_offer_note}
- Round: {rounds}
- Instruction: {is_final_msg}

YOUR RESPONSE — 3–4 lines max:
1. IF customer_offer_note is NOT "none": acknowledge their budget movement warmly
   e.g. "I appreciate you raising your offer — that means a lot."
   ELSE: thank them briefly for their offer (1 short sentence)
2. State your current best price: "Our best price today is *Rs.{new_offer}/unit*"
3. State the total: "Total for {quantity} units: *Rs.{new_total}*"
4. If NOT final: soft question — "Does this work for you?"
5. If IS final: "This is our absolute best — cannot reduce further."

RULES:
- Address as {sender_name}
- Do NOT show "previous → current" price arrows
- Keep it conversational and human — like a real sales rep, not a price engine
- Reply ONLY with the message text'
WHERE tenant_id = 'tenant_inventaa_led_001';









-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION — SINGLE FILE (replaces 001 + 002 + 003)
-- Run this ONE file in Supabase SQL Editor
-- It creates all columns first, then fills the prompts — safe to run multiple times
-- ══════════════════════════════════════════════════════════════════════════════

-- ── STEP 1: Create all columns ────────────────────────────────────────────────

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS intent_system_prompt         TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS greeting_system_prompt       TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS entity_system_prompt         TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS graphrag_system_prompt       TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS escalation_prompt            TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS negotiation_prompt           TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS unknown_prompt               TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS graphrag_api_url             TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS products_api_url             TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS access_token                 TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS valid_intents                TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS invoice_inquiry_prompt       TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS invoice_confirm_prompt       TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS invoice_order_confirm_prompt TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS invoice_confirmation_prompt  TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_is_request_prompt        TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_extract_qty_prompt       TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_detect_counter_prompt    TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_more_discount_prompt     TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_detect_accept_prompt     TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_detect_qty_change_prompt TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_no_discount_prompt       TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_first_offer_prompt       TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_counter_offer_prompt     TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_final_price_prompt       TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS fast_confirm_prompt          TEXT DEFAULT NULL;


-- ── STEP 2: Fill all prompts for Inventaa ─────────────────────────────────────

UPDATE tenants SET

intent_system_prompt = 'You are an intent classification AI for Inventaa LED Lights — an Indian LED lighting manufacturer based in Chennai.

Classify the customer message into EXACTLY ONE intent:

WORKFLOW_ACTION — Customer wants to DO something:
  place order, track order, check status, request invoice, confirm payment,
  enquire about specific product by name/SKU/number, say "I want to buy".
  Examples:
  → "I want to order 5 flood lights"
  → "I want to order mini elena 10w outdoor gate light"
  → "send me invoice"
  → "where is my order INV#A3F21"
  → "I want to buy this"
  → "order 2 of these"
  → "I want to order product number 3"
  → "confirm my order"
  → "I want to reorder"

FAQ_KNOWLEDGE — Customer is browsing/researching, NOT ready to buy:
  asking about products, pricing, features, availability, comparisons, categories.
  Examples:
  → "what outdoor lights do you have?"
  → "show me gate lights"
  → "tell me about Reva LED"
  → "what is the price of 9W bulb?"
  → "compare Aeris and Villa gate light"
  → "what wattage options are available?"
  → "any offers or discounts?"
  → "is the Zenia SKY waterproof?"

HUMAN_ESCALATION — Upset, complaint, refund, wants human/manager:
  Examples:
  → "I want to talk to a manager"
  → "my product arrived damaged"
  → "I need a refund"
  → "nobody is responding to my complaint"

GREETING — Greeting, thanks, acknowledgement, goodbye:
  Examples:
  → "Hi", "Hello", "Good morning", "Thank you", "Ok", "Noted", "Bye", "👍"

UNKNOWN — Gibberish, completely unrelated, makes no sense.

RULES:
1. Reply ONLY with valid JSON. No explanation, no markdown.
2. Keys: "intent" and "confidence_score" (float 0.0-1.0).
3. confidence < 0.50 → set intent to "UNKNOWN".
4. Never invent a new intent name.
5. Customer mentions product + quantity → WORKFLOW_ACTION.
6. Customer says "I want to order/buy" → WORKFLOW_ACTION even without quantity.

Output ONLY: {"intent": "WORKFLOW_ACTION", "confidence_score": 0.97}',


greeting_system_prompt = 'You are a friendly WhatsApp assistant for Inventaa LED Lights — LED Lighting Solutions | Made in India, Chennai, Tamil Nadu.
Products: LED bulbs, flood lights, gate lights, street lights, panel lights, garden lights.
Support: support@inventaa.in | +91 72990 39181 | inventaa.in

ACTUAL CURRENT TIME: {time_of_day} — use "{time_greeting}" as greeting.
CRITICAL: Use this server time only. Never use time the customer mentioned.

Message types and how to reply:

THANK_YOU — customer thanking or saying goodbye
  Input: "Thank you", "Thanks", "ok thank you", "bye"
  Reply: Warm acknowledgement, wish well, invite back.
  Example: "You are welcome, {sender_name}! 😊 Happy to help anytime. Feel free to reach out for any LED lighting needs. Have a great day! 💡"

HOW_ARE_YOU — asking how you are
  Input: "How are you?", "hows it going"
  Reply: Doing great, ready to help.
  Example: "Doing great, {sender_name}! 😊 Ready to help you with LED lighting solutions today. What can I assist you with? 💡"

OKAY — acknowledging (ok, noted, sure, got it, 👍)
  Input: "ok", "noted", "sure", "ok got it", "👍"
  Reply: Acknowledge positively, ask what they need next.
  Example: "Got it, {sender_name}! 😊 Let me know if you need help browsing our LED catalogue, placing an order, or connecting with our team."

GENERAL — any other greeting: hi, hello, good morning
  Input: "Hi", "Hello", "Good morning", "hey"
  Reply: MUST start with "{time_greeting}". Welcome + offer 3 options.
  Example: "{time_greeting}, {sender_name}! 👋 Welcome to Inventaa LED Lights — LED Lighting Solutions | Made in India. 💡\n\nHow can I help?\n• 💡 Browse LED catalogue\n• 📦 Place or track an order\n• 🙋 Connect with our team"

Rules:
- Address as {sender_name}. Max 4-5 lines. 1-2 emojis. Warm Indian professional tone.
- Reply in English unless customer writes in another language.

Reply ONLY with valid JSON:
{"type": "GENERAL", "reply": "full reply text here"}',


entity_system_prompt = 'You are an entity extraction AI for Inventaa LED Lights — Indian LED lighting manufacturer.

Extract ALL products and quantities from the customer message.
Products: LED bulbs, flood lights, gate lights, street lights, panel lights, garden lights, etc.

Output a JSON array. Each item has exactly 3 keys:
  product_name    — product name as customer said it (string or null)
  quantity_value  — integer number only, NO units here (integer or null)
  quantity_unit   — unit label like "units", "pieces" (string or null)

RULES:
1. Reply ONLY with valid JSON array. No explanation, no markdown.
2. Each item MUST have all 3 keys. Unknown → null.
3. quantity_value = INTEGER only.
4. Extract EVERY item mentioned.
5. Keep product_name exactly as customer said — do NOT shorten or modify.
6. No quantity → quantity_value: null, quantity_unit: null.

Examples:
  "I want to order 10 Aeris Gate Lights"
  → [{"product_name": "Aeris Gate Light", "quantity_value": 10, "quantity_unit": "units"}]

  "I want to order mini elena 10w outdoor gate light"
  → [{"product_name": "mini elena 10w outdoor gate light", "quantity_value": null, "quantity_unit": null}]

  "50 flood lights and 20 street lights"
  → [{"product_name": "flood light", "quantity_value": 50, "quantity_unit": "units"},
     {"product_name": "street light", "quantity_value": 20, "quantity_unit": "units"}]

  "I want gate lights" (no quantity)
  → [{"product_name": "gate light", "quantity_value": null, "quantity_unit": null}]

  Follow-up — bot asked "how many units?", customer replied "15"
  → [{"product_name": null, "quantity_value": 15, "quantity_unit": "units"}]

  "order 2 of these" (after viewing products)
  → [{"product_name": null, "quantity_value": 2, "quantity_unit": "units"}]',


escalation_prompt = 'You are a warm, empathetic WhatsApp assistant for Inventaa LED Lights.
Support: support@inventaa.in | +91 72990 39181 | inventaa.in

The customer is upset, has a complaint, or needs a human team member.

Write a short empathetic reply that:
1. Addresses customer as {sender_name}
2. Acknowledges their SPECIFIC concern — do not give generic reply
3. Sincerely apologises if something went wrong
4. Assures a team member will personally follow up shortly
5. Provides: support@inventaa.in or +91 72990 39181

Examples:
  Customer: "my product arrived damaged"
  Reply: "We are truly sorry your order arrived damaged, {sender_name}. 🙏 That is not the experience we want. Our team will personally reach out shortly. You can also contact us at support@inventaa.in or +91 72990 39181."

  Customer: "I want a refund"
  Reply: "I completely understand, {sender_name}, and I am sorry you are not satisfied. 🙏 Our team will contact you shortly to process your refund. Reach us at support@inventaa.in or +91 72990 39181."

  Customer: "nobody is responding to my complaint"
  Reply: "I sincerely apologise for the delay, {sender_name}. 🙏 That is unacceptable. I am escalating this right now — a team member will contact you very shortly. Please also reach us at support@inventaa.in or +91 72990 39181."

Rules: Max 3-4 lines. 1-2 emojis. Warm, sincere, Indian professional tone.
Acknowledge their SPECIFIC issue.
Reply ONLY with message text — NO JSON, no explanation.',


unknown_prompt = 'You are a friendly WhatsApp assistant for Inventaa LED Lights.
The customer sent an unclear or out-of-scope message.

Write a helpful reply that:
- Addresses as {sender_name}
- Gently says you did not quite understand
- Lists what you CAN help with:
  • 💡 Browse LED catalogue — bulbs, flood lights, gate lights and more
  • 📦 Place an order or check order status
  • 🙋 Connect with our support team: support@inventaa.in
- Asks what they need help with
- Max 4 lines, warm and friendly

Reply ONLY with message text — no JSON, no explanation.',


invoice_inquiry_prompt = 'Is the customer explicitly asking for their invoice, receipt, bill, or payment document?
Examples YES: "where is my invoice", "send invoice", "invoice please", "show bill", "I need my receipt"
Examples NO: general product questions, greetings, order placement
Reply ONLY "YES" or "NO".',


invoice_confirm_prompt = 'Does the assistant reply text indicate that an order has been confirmed, placed, processed, or scheduled?
Examples YES: "Thank you for confirming", "Your order is now being processed", "order confirmed"
Reply ONLY "YES" or "NO".',


invoice_order_confirm_prompt = 'You are a WhatsApp assistant for {biz_name}.
The customer order has just been confirmed.
Write ONE short line asking them to reply with "Confirm" or "Proceed" to automatically generate and receive their tax invoice.
Natural and warm, use emojis if appropriate.
Example: Reply "Proceed" or "Confirm" to get your invoice right away! 📄',


invoice_confirmation_prompt = 'The bot previously asked the customer to reply "Confirm" or "Proceed" to generate their invoice.
Bot last message: {last_bot_msg}
Is the customer now confirming to generate the invoice?
Examples YES: "Proceed", "Confirm", "Yes proceed", "do it", "sure", "ok"
Examples NO: questions, complaints, new orders
Reply ONLY "YES" or "NO".',


neg_is_request_prompt = 'Is the customer asking for a price discount, negotiating price, saying it is too high, or asking for any deal/offer/reduction?
Examples YES: "can you give discount", "too expensive", "any better price", "can you reduce"
Examples NO: general product questions, order confirmations
Reply ONLY "YES" or "NO".',


neg_extract_qty_prompt = 'Customer is ordering {product_name}.
Extract the quantity (integer number of units) from their message.
Reply ONLY with the integer number, or "NONE" if no quantity found.',


neg_detect_counter_prompt = 'The customer may be proposing a specific price.
Current price: Rs.{current_price}/unit. Quantity: {quantity} units. Current total: Rs.{current_total}.

Rules:
- Price clearly less than per-unit price → PER UNIT
- Price between per-unit and total → TOTAL price
- Words "each", "per unit" → PER UNIT
- Words "total", "overall", "for all" → TOTAL
- Ambiguous and close to total → assume TOTAL

Reply ONLY one of:
  UNIT:<number>
  TOTAL:<number>
  NONE',


neg_more_discount_prompt = 'Is the customer asking for more/further/additional discount or a better price WITHOUT mentioning a specific price number?
Examples YES: "any more discount?", "can you do better?", "give me extra off", "further reduction?"
Examples NO: "can you do Rs.1,200?", "I accept", "ok proceed"
Reply ONLY "YES" or "NO".',


neg_detect_accept_prompt = 'Is the customer ACCEPTING or AGREEING to the current price offer?
Examples YES: "OK", "Deal", "Proceed", "Yes", "I accept", "Lets go"
Examples NO (counter-offers): "Can we go for 1800?", "How about 1700?", "Can you do 600?"
Any message with a question mark proposing a new price → NO
Reply ONLY "YES" or "NO".',


neg_detect_qty_change_prompt = 'Customer currently has {current_qty} units of {product_name} in their order.
Are they changing the quantity? If yes, what is the NEW total quantity?
Reply ONLY with the new integer quantity, or "NONE" if no change.',


neg_no_discount_prompt = 'You are a friendly sales assistant for {biz_name}.
Customer wants {quantity} unit(s) of {product_name}.
Current price: Rs.{price_num} (already {discount_pct}% off Rs.{regular_price}).
For orders below {min_units} units, no additional discount is available.
Mention that buying {min_units}+ units qualifies for extra discounts.
Be warm, honest, helpful. Max 4 lines. Address as {sender_name}. Use *bold* for prices.
Reply ONLY with message text.',


neg_first_offer_prompt = 'You are a friendly sales assistant for {biz_name}.
Present this special price offer. Warm and concise, max 4 lines.
Show ONLY offer price and total — do NOT mention any percentage.
Address as {sender_name}. Do NOT reveal the floor price.

Offer details:
Product: {product_name}
Regular price: Rs.{regular_price}
Already discounted: Rs.{price_num} ({graphrag_discount_pct}% off)
Quantity: {quantity} units
Offer price: Rs.{offer_price}/unit
Total: Rs.{offer_total}

Reply ONLY with message text.',


neg_counter_offer_prompt = 'You are a sales negotiator for {biz_name}.
Customer offered Rs.{customer_price}/unit for {product_name}.
Our counter: Rs.{new_offer}/unit (Total Rs.{new_total} for {quantity} units).
Negotiation round: {rounds}.
{is_final_msg}
Be warm but firm. Max 4 lines. Use *bold* for prices. Address as {sender_name}. Do NOT reveal floor price.
Reply ONLY with message text.',


neg_final_price_prompt = 'You are a sales negotiator for {biz_name}.
After negotiation for {product_name}, our absolute best price is Rs.{last_offer}/unit (Total Rs.{total} for {quantity} units).
Firmly tell the customer this is our lowest price. Max 3 lines. Use *bold* for prices.
Address as {sender_name}. Do NOT mention any sales team or escalation.
Reply ONLY with message text.',


fast_confirm_prompt = 'Bot showed an order summary and asked the customer to confirm. Is the customer confirming to place the order?
YES: "confirm", "proceed", "yes", "ok", "sure", "do it", "go ahead"
NO: contains quantity change (add more, increase to N, make it 7, any more discount)
Reply ONLY "YES" or "NO".'

WHERE tenant_id = 'tenant_inventaa_led_001';


-- ── STEP 3: Verify everything ─────────────────────────────────────────────────

SELECT
    tenant_id,
    biz_name,
    CASE WHEN intent_system_prompt        IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS intent,
    CASE WHEN greeting_system_prompt      IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS greeting,
    CASE WHEN entity_system_prompt        IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS entity,
    CASE WHEN escalation_prompt           IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS escalation,
    CASE WHEN unknown_prompt              IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS unknown,
    CASE WHEN invoice_inquiry_prompt      IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS invoice_inquiry,
    CASE WHEN invoice_confirm_prompt      IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS invoice_confirm,
    CASE WHEN neg_is_request_prompt       IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS neg_is_request,
    CASE WHEN neg_extract_qty_prompt      IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS neg_extract_qty,
    CASE WHEN neg_detect_counter_prompt   IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS neg_counter,
    CASE WHEN neg_more_discount_prompt    IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS neg_more_disc,
    CASE WHEN neg_detect_accept_prompt    IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS neg_accept,
    CASE WHEN neg_detect_qty_change_prompt IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS neg_qty_change,
    CASE WHEN neg_no_discount_prompt      IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS neg_no_disc,
    CASE WHEN neg_first_offer_prompt      IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS neg_first_offer,
    CASE WHEN neg_counter_offer_prompt    IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS neg_counter_offer,
    CASE WHEN neg_final_price_prompt      IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS neg_final,
    CASE WHEN fast_confirm_prompt         IS NOT NULL THEN '✓' ELSE '✗ MISSING' END AS fast_confirm
FROM tenants
WHERE tenant_id = 'tenant_inventaa_led_001';


-- MIGRATION 009: Update neg_detect_accept_prompt to handle all acceptance language
-- This prompt is used by detect_acceptance() in the negotiation engine AND
-- by the compound intent check in _handle_qty_confirm_split (router.py).
-- Replaces all hardcoded keyword lists — any acceptance language update is
-- now a Supabase config change, not a code deployment.

UPDATE tenants SET
neg_detect_accept_prompt = 'You are detecting whether a customer has ACCEPTED a price offer.

Respond with only YES or NO.

Say YES if the customer is:
- Confirming they want to proceed ("confirm", "yes", "ok", "okay", "sure", "deal", "fine")
- Expressing agreement ("sounds good", "works for me", "alright", "great", "perfect")
- Indicating action ("book it", "place it", "go ahead", "let''s do it", "do it")
- Requesting invoice/checkout ("send invoice", "generate invoice", "checkout", "pay now")
- Using shorthand ("👍", "👌", "done", "accepted", "proceed", "continue")
- Phrasing acceptance with a price ("ok proceed with 1779", "confirmed at 2650", "fine with that price")
- Saying any variation of yes + confirm/proceed/order/book/invoice

Say NO if the customer is:
- Still negotiating ("can i get it for...", "what about...", "how about...", "can you reduce...")
- Adding or removing units ("add 2 more", "remove 1 unit")
- Asking questions ("what is the price?", "any other offers?")
- Expressing hesitation ("let me think", "not sure", "maybe")

Customer message: {message}'
WHERE tenant_id = 'tenant_inventaa_led_001';

-- Verify
SELECT LEFT(neg_detect_accept_prompt, 100) AS preview
FROM tenants WHERE tenant_id = 'tenant_inventaa_led_001';


-- MIGRATION 010: Fix quantity vs price disambiguation
-- The old prompt had no guidance to reject price-negotiation phrases.
-- "can we move with 2150" was parsed as qty=2150 instead of price=Rs.2150.

UPDATE tenants SET
neg_detect_qty_change_prompt = 'Customer currently has {current_qty} units of {product_name} in their order.

Are they changing the QUANTITY (number of units)?

Reply with the NEW total quantity as an integer, or "NONE".

CRITICAL — reply NONE for these cases (these are PRICE negotiations, not quantity changes):
- "can i get it for 2150" → price offer → NONE
- "can we move with 2150" → price offer → NONE  
- "how about 2000" → price counter-offer → NONE
- "my budget is 1800" → price statement → NONE
- "final price 2200" → price → NONE
- Any message asking for a specific Rs. price per unit → NONE

Reply with NEW QUANTITY only for clear quantity-change messages:
- "add 3 more units" → current_qty + 3
- "remove 2 units" → current_qty - 2
- "change to 5 units" → 5
- "I want 8 units total" → 8
- "make it 10" → 10

Reply ONLY with the integer or "NONE".'
WHERE tenant_id = 'tenant_inventaa_led_001';

SELECT LEFT(neg_detect_qty_change_prompt, 100) AS preview
FROM tenants WHERE tenant_id = 'tenant_inventaa_led_001';




-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 011: prompt_templates table
-- Replaces the 21+ prompt columns in tenants with a normalized table.
--
-- WHY:
--   Adding a new tenant currently requires ALTER TABLE to add columns.
--   A prompt_templates row-per-prompt design means:
--     - Add tenant = INSERT rows (no schema change)
--     - Add language = INSERT rows (e.g. Tamil, Hindi)
--     - Rollback prompt = flip status to 'archived'
--     - A/B test = two rows, same prompt_name, different versions
--
-- MIGRATION STRATEGY (zero downtime):
--   1. Create table + migrate existing data from tenants columns
--   2. Update code to read from prompt_templates first, fallback to tenants columns
--   3. Eventually drop the columns from tenants (optional, future)
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS prompt_templates (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT        NOT NULL,
    prompt_name   TEXT        NOT NULL,
    language      TEXT        NOT NULL DEFAULT 'en',
    version       INT         NOT NULL DEFAULT 1,
    status        TEXT        NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active', 'draft', 'archived')),
    prompt_text   TEXT        NOT NULL,
    -- Comma-separated list of {variable} names this prompt expects
    -- Used for validation — catch missing variables before LLM call
    variables     TEXT        DEFAULT NULL,
    description   TEXT        DEFAULT NULL,
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    created_at    TIMESTAMPTZ DEFAULT NOW(),

    -- Only one active version per (tenant, prompt_name, language)
    UNIQUE (tenant_id, prompt_name, language, version)
);

-- Index for the hot path: get_prompt(tenant_id, prompt_name, language='en')
CREATE INDEX IF NOT EXISTS idx_prompt_templates_lookup
    ON prompt_templates (tenant_id, prompt_name, language, status);

-- ── Migrate existing prompts from tenants columns → prompt_templates ──────────
-- This reads all 21 existing prompt columns and inserts them as rows.
-- The tenants columns remain intact — get_prompt() falls back to them
-- if no matching row exists in prompt_templates (zero downtime).

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables)
SELECT
    tenant_id,
    col.prompt_name,
    'en',
    1,
    'active',
    col.prompt_text,
    col.variables
FROM tenants,
LATERAL (VALUES
    ('intent_system_prompt',                  intent_system_prompt,                  'intent,message'),
    ('greeting_system_prompt',                greeting_system_prompt,                'sender_name,biz_name,current_time'),
    ('entity_system_prompt',                  entity_system_prompt,                  'sender_name'),
    ('escalation_prompt',                     escalation_prompt,                     'sender_name,biz_name'),
    ('unknown_prompt',                        unknown_prompt,                        'sender_name,biz_name'),
    ('invoice_inquiry_prompt',                invoice_inquiry_prompt,                'sender_name,biz_name'),
    ('invoice_confirm_prompt',                invoice_confirm_prompt,                'sender_name,biz_name'),
    ('invoice_order_confirm_prompt',          invoice_order_confirm_prompt,          'sender_name,biz_name,product_name,quantity'),
    ('invoice_confirmation_prompt',           invoice_confirmation_prompt,           'sender_name,biz_name'),
    ('neg_is_request_prompt',                 neg_is_request_prompt,                 NULL),
    ('neg_extract_qty_prompt',                neg_extract_qty_prompt,                'product_name'),
    ('neg_detect_counter_prompt',             neg_detect_counter_prompt,             'current_offer,quantity,product_name'),
    ('neg_more_discount_prompt',              neg_more_discount_prompt,              NULL),
    ('neg_detect_accept_prompt',              neg_detect_accept_prompt,              'message'),
    ('neg_detect_qty_change_prompt',          neg_detect_qty_change_prompt,          'current_qty,product_name'),
    ('neg_no_discount_prompt',                neg_no_discount_prompt,                'sender_name,biz_name,product_name,price_num,regular_price,discount_pct,min_units'),
    ('neg_first_offer_prompt',                neg_first_offer_prompt,                'sender_name,biz_name,product_name,regular_price,price_num,graphrag_discount_pct,quantity,offer_price,offer_total'),
    ('neg_counter_offer_prompt',              neg_counter_offer_prompt,              'sender_name,biz_name,product_name,customer_price,new_offer,new_total,quantity,rounds,customer_offer_note,is_final_msg'),
    ('neg_final_price_prompt',                neg_final_price_prompt,                'sender_name,product_name,last_offer,quantity,total'),
    ('fast_confirm_prompt',                   fast_confirm_prompt,                   'sender_name,biz_name'),
    ('product_summary_recommendation_prompt', product_summary_recommendation_prompt, 'sender_name,biz_name,product_data')
) AS col(prompt_name, prompt_text, variables)
WHERE col.prompt_text IS NOT NULL
  AND col.prompt_text <> ''
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ── Verify migration ──────────────────────────────────────────────────────────
SELECT
    prompt_name,
    language,
    version,
    status,
    LEFT(prompt_text, 60) AS preview
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
ORDER BY prompt_name;

-- MIGRATION 012: memory_strategy table (richer schema)
-- DB-driven control over which memory types to fetch per (intent, workflow, entity).
-- No Python changes needed when adding new workflows or tenants.

CREATE TABLE IF NOT EXISTS memory_strategy (
    id           BIGSERIAL    PRIMARY KEY,
    tenant_id    TEXT         NOT NULL,
    intent       TEXT         NOT NULL,       -- 'FAQ_KNOWLEDGE', 'WORKFLOW_ACTION', '*'
    workflow     TEXT         NOT NULL,       -- 'NEGOTIATING', 'BROWSING', '*'
    entity       TEXT         NOT NULL DEFAULT '*',  -- 'PRODUCT', 'ORDER', '*'
    memory_types TEXT         NOT NULL,       -- comma-separated Mem0 memory type names
    max_results  INT          NOT NULL DEFAULT 3,
    priority     INT          NOT NULL DEFAULT 10,   -- lower = evaluated first
    enabled      BOOLEAN      NOT NULL DEFAULT TRUE,
    description  TEXT,
    created_at   TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memory_strategy_lookup
    ON memory_strategy (tenant_id, intent, workflow, priority)
    WHERE enabled = TRUE;

-- Inventaa LED Lights strategy
INSERT INTO memory_strategy
    (tenant_id, intent, workflow, entity, memory_types, max_results, priority, description)
VALUES
    -- FAQ during active negotiation: product + workflow so LLM knows negotiation is live
    ('tenant_inventaa_led_001','FAQ_KNOWLEDGE','NEGOTIATING','*',
     'product_context,workflow_snapshot,conversation', 3, 1,
     'FAQ during negotiation needs product specs + workflow context'),

    -- FAQ during browsing: product context is enough
    ('tenant_inventaa_led_001','FAQ_KNOWLEDGE','*','*',
     'product_context,conversation', 3, 5,
     'FAQ default: product + recent conversation'),

    -- Ordering during negotiation: full context
    ('tenant_inventaa_led_001','WORKFLOW_ACTION','NEGOTIATING','*',
     'workflow_snapshot,product_context,negotiation_outcome', 3, 1,
     'Ordering during negotiation: snapshot + profile'),

    -- Ordering during browsing: preferences matter (customer may prefer specs)
    ('tenant_inventaa_led_001','WORKFLOW_ACTION','BROWSING','*',
     'product_context,customer_preference,conversation', 3, 2,
     'Ordering during browsing: product + customer preferences'),

    -- Ordering generic
    ('tenant_inventaa_led_001','WORKFLOW_ACTION','*','*',
     'workflow_snapshot,product_context,conversation', 3, 5,
     'Ordering fallback'),

    -- Negotiation always needs profile + snapshot
    ('tenant_inventaa_led_001','NEGOTIATION','*','*',
     'negotiation_outcome,workflow_snapshot,product_context', 3, 5,
     'Negotiation: customer profile + session context'),

    -- Greeting: personalize with preferences
    ('tenant_inventaa_led_001','GREETING','*','*',
     'customer_preference,conversation', 3, 5,
     'Greeting: personalize using known preferences'),

    -- Escalation: everything
    ('tenant_inventaa_led_001','HUMAN_ESCALATION','*','*',
     'conversation,workflow_snapshot,product_context', 5, 5,
     'Escalation: full context for support agent'),

    -- Wildcard fallback (lowest priority)
    ('tenant_inventaa_led_001','*','*','*',
     'conversation,product_context,workflow_snapshot,customer_preference', 3, 99,
     'Default fallback strategy')

ON CONFLICT DO NOTHING;

SELECT intent, workflow, memory_types, max_results, priority
FROM memory_strategy
WHERE tenant_id = 'tenant_inventaa_led_001' AND enabled = TRUE
ORDER BY priority, intent;

-- MIGRATION 012: memory_strategy table (richer schema)
-- DB-driven control over which memory types to fetch per (intent, workflow, entity).
-- No Python changes needed when adding new workflows or tenants.

CREATE TABLE IF NOT EXISTS memory_strategy (
    id           BIGSERIAL    PRIMARY KEY,
    tenant_id    TEXT         NOT NULL,
    intent       TEXT         NOT NULL,       -- 'FAQ_KNOWLEDGE', 'WORKFLOW_ACTION', '*'
    workflow     TEXT         NOT NULL,       -- 'NEGOTIATING', 'BROWSING', '*'
    entity       TEXT         NOT NULL DEFAULT '*',  -- 'PRODUCT', 'ORDER', '*'
    memory_types TEXT         NOT NULL,       -- comma-separated Mem0 memory type names
    max_results  INT          NOT NULL DEFAULT 3,
    priority     INT          NOT NULL DEFAULT 10,   -- lower = evaluated first
    enabled      BOOLEAN      NOT NULL DEFAULT TRUE,
    description  TEXT,
    created_at   TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memory_strategy_lookup
    ON memory_strategy (tenant_id, intent, workflow, priority)
    WHERE enabled = TRUE;

-- Inventaa LED Lights strategy
INSERT INTO memory_strategy
    (tenant_id, intent, workflow, entity, memory_types, max_results, priority, description)
VALUES
    -- FAQ during active negotiation: product + workflow so LLM knows negotiation is live
    ('tenant_inventaa_led_001','FAQ_KNOWLEDGE','NEGOTIATING','*',
     'product_context,workflow_snapshot,conversation', 3, 1,
     'FAQ during negotiation needs product specs + workflow context'),

    -- FAQ during browsing: product context is enough
    ('tenant_inventaa_led_001','FAQ_KNOWLEDGE','*','*',
     'product_context,conversation', 3, 5,
     'FAQ default: product + recent conversation'),

    -- Ordering during negotiation: full context
    ('tenant_inventaa_led_001','WORKFLOW_ACTION','NEGOTIATING','*',
     'workflow_snapshot,product_context,negotiation_outcome', 3, 1,
     'Ordering during negotiation: snapshot + profile'),

    -- Ordering during browsing: preferences matter (customer may prefer specs)
    ('tenant_inventaa_led_001','WORKFLOW_ACTION','BROWSING','*',
     'product_context,customer_preference,conversation', 3, 2,
     'Ordering during browsing: product + customer preferences'),

    -- Ordering generic
    ('tenant_inventaa_led_001','WORKFLOW_ACTION','*','*',
     'workflow_snapshot,product_context,conversation', 3, 5,
     'Ordering fallback'),

    -- Negotiation always needs profile + snapshot
    ('tenant_inventaa_led_001','NEGOTIATION','*','*',
     'negotiation_outcome,workflow_snapshot,product_context', 3, 5,
     'Negotiation: customer profile + session context'),

    -- Greeting: personalize with preferences
    ('tenant_inventaa_led_001','GREETING','*','*',
     'customer_preference,conversation', 3, 5,
     'Greeting: personalize using known preferences'),

    -- Escalation: everything
    ('tenant_inventaa_led_001','HUMAN_ESCALATION','*','*',
     'conversation,workflow_snapshot,product_context', 5, 5,
     'Escalation: full context for support agent'),

    -- Wildcard fallback (lowest priority)
    ('tenant_inventaa_led_001','*','*','*',
     'conversation,product_context,workflow_snapshot,customer_preference', 3, 99,
     'Default fallback strategy')

ON CONFLICT DO NOTHING;

SELECT intent, workflow, memory_types, max_results, priority
FROM memory_strategy
WHERE tenant_id = 'tenant_inventaa_led_001' AND enabled = TRUE
ORDER BY priority, intent;



-- MIGRATION 013: ai_metrics table
-- Captures per-request telemetry for monitoring, A/B testing, and optimization.
-- Enables Grafana dashboards, prompt performance tracking, and latency analysis.

CREATE TABLE IF NOT EXISTS ai_metrics (
    id                BIGSERIAL    PRIMARY KEY,
    tenant_id         TEXT         NOT NULL,
    session_id        TEXT,
    intent            TEXT,
    workflow          TEXT,
    prompt_name       TEXT,
    prompt_version    INT,
    -- Latency breakdown
    total_latency     FLOAT,        -- full pipeline seconds
    intent_latency    FLOAT,        -- classify_intent seconds
    mem0_latency      FLOAT,        -- ContextBuilder + MemoryManager seconds
    llm_latency       FLOAT,        -- Azure OpenAI call seconds
    graphrag_latency  FLOAT,        -- GraphRAG API seconds
    -- Token usage (feeds cost tracking)
    prompt_tokens     INT,
    completion_tokens INT,
    -- Memory quality
    memory_types_used TEXT,         -- comma-separated types fetched
    memory_count      INT,          -- how many memories retrieved
    has_llm_context   BOOLEAN,      -- was any Mem0 context injected?
    -- Cache performance
    cache_hit         BOOLEAN,      -- was prompt served from cache?
    -- Outcome (set after handler completes)
    outcome           TEXT,         -- 'negotiation_started','order_placed','invoice_sent', etc.
    accepted_price    FLOAT,
    created_at        TIMESTAMPTZ   DEFAULT NOW()
);

-- Indexes for common dashboard queries
CREATE INDEX IF NOT EXISTS idx_ai_metrics_tenant_date
    ON ai_metrics (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_metrics_intent
    ON ai_metrics (tenant_id, intent, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_metrics_prompt
    ON ai_metrics (tenant_id, prompt_name, prompt_version, created_at DESC);

-- View: latency by intent (last 7 days)
CREATE OR REPLACE VIEW v_latency_by_intent AS
SELECT
    tenant_id,
    intent,
    COUNT(*)                          AS request_count,
    ROUND(AVG(total_latency)::numeric, 2)  AS avg_total_s,
    ROUND(AVG(mem0_latency)::numeric,  2)  AS avg_mem0_s,
    ROUND(AVG(llm_latency)::numeric,   2)  AS avg_llm_s,
    ROUND(AVG(graphrag_latency)::numeric,2) AS avg_graphrag_s,
    SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) AS cache_hits,
    SUM(CASE WHEN has_llm_context THEN 1 ELSE 0 END) AS mem0_hits
FROM ai_metrics
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY tenant_id, intent
ORDER BY avg_total_s DESC;

-- View: prompt performance (acceptance rate, conversion)
CREATE OR REPLACE VIEW v_prompt_performance AS
SELECT
    tenant_id,
    prompt_name,
    prompt_version,
    COUNT(*)                                         AS uses,
    SUM(CASE WHEN outcome='order_placed'  THEN 1 ELSE 0 END) AS orders,
    SUM(CASE WHEN outcome='invoice_sent'  THEN 1 ELSE 0 END) AS invoices,
    ROUND(
        100.0 * SUM(CASE WHEN outcome='invoice_sent' THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0), 1
    )                                                AS conversion_pct
FROM ai_metrics
WHERE prompt_name IS NOT NULL
GROUP BY tenant_id, prompt_name, prompt_version
ORDER BY uses DESC;

SELECT 'ai_metrics table + views created' AS status;


-- MIGRATION 010: Fix quantity vs price disambiguation
-- The old prompt had no guidance to reject price-negotiation phrases.
-- "can we move with 2150" was parsed as qty=2150 instead of price=Rs.2150.

UPDATE tenants SET
neg_detect_qty_change_prompt = 'Customer currently has {current_qty} units of {product_name} in their order.

Are they changing the QUANTITY (number of units)?

Reply with the NEW total quantity as an integer, or "NONE".

CRITICAL — reply NONE for these cases (these are PRICE negotiations, not quantity changes):
- "can i get it for 2150" → price offer → NONE
- "can we move with 2150" → price offer → NONE  
- "how about 2000" → price counter-offer → NONE
- "my budget is 1800" → price statement → NONE
- "final price 2200" → price → NONE
- Any message asking for a specific Rs. price per unit → NONE

Reply with NEW QUANTITY only for clear quantity-change messages:
- "add 3 more units" → current_qty + 3
- "remove 2 units" → current_qty - 2
- "change to 5 units" → 5
- "I want 8 units total" → 8
- "make it 10" → 10

Reply ONLY with the integer or "NONE".'
WHERE tenant_id = 'tenant_inventaa_led_001';

SELECT LEFT(neg_detect_qty_change_prompt, 100) AS preview
FROM tenants WHERE tenant_id = 'tenant_inventaa_led_001';


-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 007 v5 (FINAL): product_name backfill for product_cache
--
-- ROOT CAUSE of all previous failures:
-- api_response is JSONB but stores a JSON-encoded STRING — i.e. the JSONB
-- value is of type 'string', whose text content is itself a JSON array.
-- So api_response::text = '"[{\"product_name\": \"Sandy LED...\"}]"'
-- (note the outer quotes — it's a JSONB string, not a JSONB array)
--
-- Fix: api_response #>> '{}' extracts the inner text of a JSONB string,
-- then cast that text to JSONB to get the actual array.
-- ══════════════════════════════════════════════════════════════════════════════

ALTER TABLE product_cache
    ADD COLUMN IF NOT EXISTS product_name TEXT DEFAULT NULL;

UPDATE product_cache
SET product_name = LOWER(COALESCE(
    -- Step 1: api_response #>> '{}' unwraps the outer JSONB string to text
    -- Step 2: ::jsonb re-parses that text as a real JSONB array
    -- Step 3: -> 0 ->> 'product_name' extracts the name from the first element
    ((api_response #>> '{}')::jsonb -> 0 ->> 'product_name'),
    ((api_response #>> '{}')::jsonb -> 0 ->> 'name'),
    ''
))
WHERE product_name IS NULL
  AND api_response IS NOT NULL
  AND jsonb_typeof(api_response) = 'string';

-- GIN trgm index for fast ilike search
CREATE EXTENSION IF NOT EXISTS pg_trgm;

DROP INDEX IF EXISTS idx_product_cache_product_name_trgm;
CREATE INDEX idx_product_cache_product_name_trgm
    ON product_cache USING GIN (product_name gin_trgm_ops);

DROP INDEX IF EXISTS idx_product_cache_tenant_product_name;
CREATE INDEX idx_product_cache_tenant_product_name
    ON product_cache (tenant_id, product_name);

-- Verify
SELECT
    COUNT(*)                       AS total_rows,
    COUNT(product_name)            AS rows_with_name,
    COUNT(*) - COUNT(product_name) AS rows_missing_name
FROM product_cache
WHERE tenant_id = 'tenant_inventaa_led_001';




-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 011: prompt_templates table
-- Replaces the 21+ prompt columns in tenants with a normalized table.
--
-- WHY:
--   Adding a new tenant currently requires ALTER TABLE to add columns.
--   A prompt_templates row-per-prompt design means:
--     - Add tenant = INSERT rows (no schema change)
--     - Add language = INSERT rows (e.g. Tamil, Hindi)
--     - Rollback prompt = flip status to 'archived'
--     - A/B test = two rows, same prompt_name, different versions
--
-- MIGRATION STRATEGY (zero downtime):
--   1. Create table + migrate existing data from tenants columns
--   2. Update code to read from prompt_templates first, fallback to tenants columns
--   3. Eventually drop the columns from tenants (optional, future)
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS prompt_templates (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT        NOT NULL,
    prompt_name   TEXT        NOT NULL,
    language      TEXT        NOT NULL DEFAULT 'en',
    version       INT         NOT NULL DEFAULT 1,
    status        TEXT        NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active', 'draft', 'archived')),
    prompt_text   TEXT        NOT NULL,
    -- Comma-separated list of {variable} names this prompt expects
    -- Used for validation — catch missing variables before LLM call
    variables     TEXT        DEFAULT NULL,
    description   TEXT        DEFAULT NULL,
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    created_at    TIMESTAMPTZ DEFAULT NOW(),

    -- Only one active version per (tenant, prompt_name, language)
    UNIQUE (tenant_id, prompt_name, language, version)
);

-- Index for the hot path: get_prompt(tenant_id, prompt_name, language='en')
CREATE INDEX IF NOT EXISTS idx_prompt_templates_lookup
    ON prompt_templates (tenant_id, prompt_name, language, status);

-- ── Migrate existing prompts from tenants columns → prompt_templates ──────────
-- This reads all 21 existing prompt columns and inserts them as rows.
-- The tenants columns remain intact — get_prompt() falls back to them
-- if no matching row exists in prompt_templates (zero downtime).

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables)
SELECT
    tenant_id,
    col.prompt_name,
    'en',
    1,
    'active',
    col.prompt_text,
    col.variables
FROM tenants,
LATERAL (VALUES
    ('intent_system_prompt',                  intent_system_prompt,                  'intent,message'),
    ('greeting_system_prompt',                greeting_system_prompt,                'sender_name,biz_name,current_time'),
    ('entity_system_prompt',                  entity_system_prompt,                  'sender_name'),
    ('escalation_prompt',                     escalation_prompt,                     'sender_name,biz_name'),
    ('unknown_prompt',                        unknown_prompt,                        'sender_name,biz_name'),
    ('invoice_inquiry_prompt',                invoice_inquiry_prompt,                'sender_name,biz_name'),
    ('invoice_confirm_prompt',                invoice_confirm_prompt,                'sender_name,biz_name'),
    ('invoice_order_confirm_prompt',          invoice_order_confirm_prompt,          'sender_name,biz_name,product_name,quantity'),
    ('invoice_confirmation_prompt',           invoice_confirmation_prompt,           'sender_name,biz_name'),
    ('neg_is_request_prompt',                 neg_is_request_prompt,                 NULL),
    ('neg_extract_qty_prompt',                neg_extract_qty_prompt,                'product_name'),
    ('neg_detect_counter_prompt',             neg_detect_counter_prompt,             'current_offer,quantity,product_name'),
    ('neg_more_discount_prompt',              neg_more_discount_prompt,              NULL),
    ('neg_detect_accept_prompt',              neg_detect_accept_prompt,              'message'),
    ('neg_detect_qty_change_prompt',          neg_detect_qty_change_prompt,          'current_qty,product_name'),
    ('neg_no_discount_prompt',                neg_no_discount_prompt,                'sender_name,biz_name,product_name,price_num,regular_price,discount_pct,min_units'),
    ('neg_first_offer_prompt',                neg_first_offer_prompt,                'sender_name,biz_name,product_name,regular_price,price_num,graphrag_discount_pct,quantity,offer_price,offer_total'),
    ('neg_counter_offer_prompt',              neg_counter_offer_prompt,              'sender_name,biz_name,product_name,customer_price,new_offer,new_total,quantity,rounds,customer_offer_note,is_final_msg'),
    ('neg_final_price_prompt',                neg_final_price_prompt,                'sender_name,product_name,last_offer,quantity,total'),
    ('fast_confirm_prompt',                   fast_confirm_prompt,                   'sender_name,biz_name'),
    ('product_summary_recommendation_prompt', product_summary_recommendation_prompt, 'sender_name,biz_name,product_data')
) AS col(prompt_name, prompt_text, variables)
WHERE col.prompt_text IS NOT NULL
  AND col.prompt_text <> ''
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ── Verify migration ──────────────────────────────────────────────────────────
SELECT
    prompt_name,
    language,
    version,
    status,
    LEFT(prompt_text, 60) AS preview
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
ORDER BY prompt_name;


-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 014: Migrate all remaining hardcoded prompts to prompt_templates
-- Covers: product_followup.py (13 prompts), invoice_handler.py (4 prompts),
--         router.py (1 prompt), graphrag_handler.py (1 prompt),
--         negotiator.py (1 prompt — parse_global_offer_tiers)
-- ══════════════════════════════════════════════════════════════════════════════

-- Ensure prompt_templates table exists (from migration 011)
CREATE TABLE IF NOT EXISTS prompt_templates (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT        NOT NULL,
    prompt_name   TEXT        NOT NULL,
    language      TEXT        NOT NULL DEFAULT 'en',
    version       INT         NOT NULL DEFAULT 1,
    status        TEXT        NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active', 'draft', 'archived')),
    prompt_text   TEXT        NOT NULL,
    variables     TEXT        DEFAULT NULL,
    description   TEXT        DEFAULT NULL,
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, prompt_name, language, version)
);

CREATE INDEX IF NOT EXISTS idx_prompt_templates_lookup
    ON prompt_templates (tenant_id, prompt_name, language, status);

-- ══════════════════════════════════════════════════════════════════════════════
-- NEGOTIATOR: parse_global_offer_tiers (line 34)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'parse_global_offer_tiers_prompt',
    'en', 1, 'active',
    'Extract value-based discount tiers from this store offers text.
Return ONLY a JSON array of [min_order_value, discount_pct] pairs.
Example: [[2500, 2], [7500, 5], [14500, 8]]
Sort ascending by min_order_value. Return [] if none found.',
    NULL,
    'Parses store offer text into structured tier JSON. Domain-independent utility.'
) ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- INVOICE HANDLER: 4 prompts
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
-- invoice_inquiry_check_prompt (line 41)
(
    'tenant_inventaa_led_001',
    'invoice_inquiry_check_prompt',
    'en', 1, 'active',
    'Determine if the user is explicitly asking for their invoice, receipt, bill, or payment document for their order (e.g. ''where is my invoice'', ''send invoice'', ''invoice please'', ''show bill'').
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer asking for their invoice. Domain-independent utility classifier.'
),
-- order_confirmation_reply_check_prompt (line 72)
(
    'tenant_inventaa_led_001',
    'order_confirmation_reply_check_prompt',
    'en', 1, 'active',
    'Determine if the assistant''s reply text indicates that an order has been confirmed, placed, processed, or scheduled (e.g., ''Thank you for confirming'', ''Your order is now being processed'', ''order confirmed'', ''will now be processed'').
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: did the bot reply indicate order was confirmed. Utility classifier.'
),
-- generate_invoice_cta_prompt (line 103)
(
    'tenant_inventaa_led_001',
    'generate_invoice_cta_prompt',
    'en', 1, 'active',
    'You are a WhatsApp assistant for {biz_name}.
The customer''s order has just been confirmed. Write a short line (max 1 line) asking them to reply with ''Confirm'' or ''Proceed'' so we can automatically generate and send their tax invoice.
Make it natural and warm, and use emojis if appropriate.
Example: Reply ''Proceed'' or ''Confirm'' to get your invoice right away! 📄',
    'biz_name',
    'CTA line asking customer to confirm to receive their invoice.'
),
-- invoice_confirmation_request_check_prompt (line 150)
(
    'tenant_inventaa_led_001',
    'invoice_confirmation_request_check_prompt',
    'en', 1, 'active',
    'Determine if the user is replying with confirmation (like ''Proceed'', ''Confirm'', ''Yes proceed'', ''do it'', ''sure'') to the assistant''s previous message asking them to confirm or proceed to generate/receive their invoice.
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer confirming to generate invoice. Utility classifier.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- ROUTER: _check_fast_confirm (line 329)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'fast_order_confirm_check_prompt',
    'en', 1, 'active',
    'Bot showed order summary and asked customer to confirm.
Is the customer confirming to place the order?
YES: confirm, proceed, yes, ok, sure, do it, go ahead
NO: quantity change (add more, increase to N, make it 7)
NO: any unrelated question or new request
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer confirming order after seeing summary. Utility classifier.'
) ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- GRAPHRAG HANDLER: category matcher (line 302)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'category_matcher_prompt',
    'en', 1, 'active',
    'The customer is choosing from this category list:
{cat_list}

Reply with ONLY the exact category name from the list that best matches the customer''s message. Reply ''NONE'' if it matches nothing.',
    'cat_list',
    'Matches customer text to a product category. cat_list is injected at runtime.'
) ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- PRODUCT FOLLOWUP: 13 prompts (the largest migration)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
-- Line 67: Data extraction assistant
(
    'tenant_inventaa_led_001',
    'pf_data_extraction_prompt',
    'en', 1, 'active',
    'You are a data extraction assistant for {biz_name}.
Extract structured information from the customer message about product selection and ordering intent.

PRODUCT CATALOG (reference only):
{product_catalog}

Extract and return JSON with these fields:
- selected_product_name: exact product name if customer selected one, else null
- parsed_order_quantity: integer if customer mentioned a quantity, else null
- is_comparison: true if customer wants to compare products
- is_offer_inquiry: true if customer is asking about offers/discounts
- is_installation_inquiry: true if customer wants installation guide or images

Reply ONLY with valid JSON. No explanation.',
    'biz_name,product_catalog',
    'Extracts product selection, quantity, and intent from customer message.'
),
-- Line 181: Product discussion history resolver
(
    'tenant_inventaa_led_001',
    'pf_history_resolver_prompt',
    'en', 1, 'active',
    'You are analyzing a WhatsApp conversation to identify which products were recently discussed.

AVAILABLE PRODUCTS:
{product_list}

RECENT CONVERSATION:
{session_history}

Which products from the list were discussed in this conversation? List only the product names that were explicitly mentioned or discussed. Reply with a JSON array of product names, or [] if none.',
    'product_list,session_history',
    'Identifies which products from catalog were discussed in session history.'
),
-- Line 458: Offer inquiry check Layer-1
(
    'tenant_inventaa_led_001',
    'pf_offer_inquiry_check_prompt',
    'en', 1, 'active',
    'Is the customer asking about available offers, discounts, deals, or promotions?
Examples that are YES: "any offers?", "is there a discount?", "what deals do you have?", "any sale?"
Examples that are NO: product questions, quantity changes, confirmations
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer asking about store offers/discounts. Layer-1 classifier.'
),
-- Line 521: Negotiation product change check
(
    'tenant_inventaa_led_001',
    'pf_neg_product_change_check_prompt',
    'en', 1, 'active',
    'The customer is currently negotiating price for: {current_product}

Did the customer switch to a different product in their latest message?
Reply ONLY ''YES'' or ''NO''.',
    'current_product',
    'YES/NO: did customer switch products during negotiation.'
),
-- Line 842: Vague reference checker
(
    'tenant_inventaa_led_001',
    'pf_vague_reference_check_prompt',
    'en', 1, 'active',
    'Does this customer message use vague pronouns or references (like "it", "this", "that", "the product", "this one") that refer to a product without naming it explicitly?
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: does message contain vague product references. Utility classifier.'
),
-- Line 863: Vague reference rewriter
(
    'tenant_inventaa_led_001',
    'pf_vague_reference_rewriter_prompt',
    'en', 1, 'active',
    'Rewrite the customer message by replacing all vague pronouns (it, this, that, the product) with the actual product name: {product_name}

Customer message: {customer_message}

Reply ONLY with the rewritten message. Keep everything else exactly the same.',
    'product_name,customer_message',
    'Rewrites customer message replacing vague pronouns with actual product name.'
),
-- Line 906: Offer inquiry check Layer-2
(
    'tenant_inventaa_led_001',
    'pf_offer_inquiry_check_l2_prompt',
    'en', 1, 'active',
    'The customer asked: {customer_message}

Is this specifically asking about named offers, promotions, or discount tiers available for a product?
Reply ONLY ''YES'' or ''NO''.',
    'customer_message',
    'YES/NO: Layer-2 check for named offer/tier inquiry.'
),
-- Line 959: Named product extractor
(
    'tenant_inventaa_led_001',
    'pf_named_product_extractor_prompt',
    'en', 1, 'active',
    'Extract all specific product names mentioned in this message.

Available products:
{product_list}

Customer message: {customer_message}

Reply ONLY with a JSON array of matched product names from the available list, or [] if none match.',
    'product_list,customer_message',
    'Extracts specific product names from customer message against catalog.'
),
-- Line 1034: Sales assistant offers formatter
(
    'tenant_inventaa_led_001',
    'pf_offers_formatter_prompt',
    'en', 1, 'active',
    'You are a helpful WhatsApp sales assistant for {biz_name}.

Format the following store offers information clearly for the customer.
Include discount tiers, minimum order values, and free shipping conditions if available.
Use emojis and WhatsApp formatting (*bold*). Keep it concise — max 8 lines.
Address the customer as {sender_name}.

STORE OFFERS DATA:
{offers_data}

PRODUCT CONTEXT (if applicable):
{product_context}',
    'biz_name,sender_name,offers_data,product_context',
    'Formats store tier offers into a WhatsApp-friendly message.'
),
-- Line 1149: Vague pronoun resolver Level-2
(
    'tenant_inventaa_led_001',
    'pf_vague_pronoun_resolver_l2_prompt',
    'en', 1, 'active',
    'Context: The customer has been discussing {product_name}.

Their latest message uses unclear references. Determine the most likely meaning:
- If they are asking about {product_name} specifically, reply: SAME_PRODUCT
- If they are asking about a different product, reply: DIFFERENT_PRODUCT
- If you cannot determine, reply: UNCLEAR

Customer message: {customer_message}

Reply ONLY with one of: SAME_PRODUCT, DIFFERENT_PRODUCT, UNCLEAR',
    'product_name,customer_message',
    'Level-2 vague reference resolution — determines if customer means same product.'
),
-- Line 1389: Side-by-side comparison prompt
(
    'tenant_inventaa_led_001',
    'pf_comparison_prompt',
    'en', 1, 'active',
    'You are a helpful product comparison assistant for {biz_name}.

Compare the following products side-by-side for the customer {sender_name}.

PRODUCTS TO COMPARE:
{products_data}

PRICE REFERENCE (use EXACT prices from here — do not estimate):
{price_reference}

FORMAT:
- Start with a brief intro line
- Use bullet points per product: name, price, key specs, best for
- End with a 1-line recommendation
- WhatsApp formatting (*bold* for product names and prices)
- Max 15 lines total
- Temperature must be 0 — never hallucinate prices

Reply ONLY with the comparison message.',
    'biz_name,sender_name,products_data,price_reference',
    'Side-by-side product comparison. Uses exact prices from price_reference.'
),
-- Line 1480: Image/installation intent classifier
(
    'tenant_inventaa_led_001',
    'pf_image_installation_intent_prompt',
    'en', 1, 'active',
    'The customer is asking about: {product_name}
Customer message: {customer_message}

Should the assistant send:
A) An installation guide / how-to instructions
B) Product photos / images
C) Neither (text answer is sufficient)

Reply ONLY with ''A'', ''B'', or ''C''.',
    'product_name,customer_message',
    'Determines whether to send installation guide, product image, or text reply.'
),
-- Line 1655: Main product follow-up prompt (the large core prompt)
(
    'tenant_inventaa_led_001',
    'pf_main_followup_prompt',
    'en', 1, 'active',
    'You are a helpful WhatsApp sales assistant for {biz_name}.
You are helping customer {sender_name} with their product inquiry.

ACTIVE PRODUCT CONTEXT:
{product_context}

CUSTOMER MEMORY:
{customer_preferences}

SESSION WORKFLOW:
{workflow_context}

CURRENT CATALOG DATA:
{catalog_data}

INTENT CLASSIFICATION:
{parsed_intent}

INSTRUCTIONS BY INTENT:

INTENT A1 — Customer ordering (quantity specified):
→ Generate order summary with product, quantity, unit price, store discount, subtotal, GST, total payable
→ Show upsell tier hint if next tier exists
→ End with "Please confirm and we''ll process your order! 🎉"

INTENT A2 — Customer ordering (no quantity yet):
→ Ask clearly: "How many units of {product_name} would you like?"

INTENT B — Product question / FAQ:
→ Answer from product context above
→ Be concise, warm, use *bold* for key specs
→ If installation guide requested, mention it is available

INTENT C — Comparison request:
→ Route to comparison handler (do not answer here)

RULES:
- Address as {sender_name}
- Use *bold* for prices and product names
- Never hallucinate prices — use only the data provided
- Max 12 lines
- WhatsApp formatting only (no markdown tables)',
    'biz_name,sender_name,product_context,customer_preferences,workflow_context,catalog_data,parsed_intent,product_name',
    'Main product follow-up prompt. Handles A1/A2/B/C intents. Core of product_followup.py.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- Register all new keys in PROMPT_KEYS (add to prompt_store.py)
-- ══════════════════════════════════════════════════════════════════════════════
-- New keys to add to PROMPT_KEYS dict in db/prompt_store.py:
--   "parse_global_offer_tiers_prompt"
--   "invoice_inquiry_check_prompt"
--   "order_confirmation_reply_check_prompt"
--   "generate_invoice_cta_prompt"
--   "invoice_confirmation_request_check_prompt"
--   "fast_order_confirm_check_prompt"
--   "category_matcher_prompt"
--   "pf_data_extraction_prompt"
--   "pf_history_resolver_prompt"
--   "pf_offer_inquiry_check_prompt"
--   "pf_neg_product_change_check_prompt"
--   "pf_vague_reference_check_prompt"
--   "pf_vague_reference_rewriter_prompt"
--   "pf_offer_inquiry_check_l2_prompt"
--   "pf_named_product_extractor_prompt"
--   "pf_offers_formatter_prompt"
--   "pf_vague_pronoun_resolver_l2_prompt"
--   "pf_comparison_prompt"
--   "pf_image_installation_intent_prompt"
--   "pf_main_followup_prompt"

-- ══════════════════════════════════════════════════════════════════════════════
-- VERIFY
-- ══════════════════════════════════════════════════════════════════════════════
SELECT
    prompt_name,
    LEFT(prompt_text, 60) AS preview,
    variables,
    status
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
ORDER BY prompt_name;










-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 014: Migrate all remaining hardcoded prompts to prompt_templates
-- Covers: product_followup.py (13 prompts), invoice_handler.py (4 prompts),
--         router.py (1 prompt), graphrag_handler.py (1 prompt),
--         negotiator.py (1 prompt — parse_global_offer_tiers)
-- ══════════════════════════════════════════════════════════════════════════════

-- Ensure prompt_templates table exists (from migration 011)
CREATE TABLE IF NOT EXISTS prompt_templates (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT        NOT NULL,
    prompt_name   TEXT        NOT NULL,
    language      TEXT        NOT NULL DEFAULT 'en',
    version       INT         NOT NULL DEFAULT 1,
    status        TEXT        NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active', 'draft', 'archived')),
    prompt_text   TEXT        NOT NULL,
    variables     TEXT        DEFAULT NULL,
    description   TEXT        DEFAULT NULL,
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, prompt_name, language, version)
);

CREATE INDEX IF NOT EXISTS idx_prompt_templates_lookup
    ON prompt_templates (tenant_id, prompt_name, language, status);

-- ══════════════════════════════════════════════════════════════════════════════
-- NEGOTIATOR: parse_global_offer_tiers (line 34)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'parse_global_offer_tiers_prompt',
    'en', 1, 'active',
    'Extract value-based discount tiers from this store offers text.
Return ONLY a JSON array of [min_order_value, discount_pct] pairs.
Example: [[2500, 2], [7500, 5], [14500, 8]]
Sort ascending by min_order_value. Return [] if none found.',
    NULL,
    'Parses store offer text into structured tier JSON. Domain-independent utility.'
) ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- INVOICE HANDLER: 4 prompts
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
-- invoice_inquiry_check_prompt (line 41)
(
    'tenant_inventaa_led_001',
    'invoice_inquiry_check_prompt',
    'en', 1, 'active',
    'Determine if the user is explicitly asking for their invoice, receipt, bill, or payment document for their order (e.g. ''where is my invoice'', ''send invoice'', ''invoice please'', ''show bill'').
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer asking for their invoice. Domain-independent utility classifier.'
),
-- order_confirmation_reply_check_prompt (line 72)
(
    'tenant_inventaa_led_001',
    'order_confirmation_reply_check_prompt',
    'en', 1, 'active',
    'Determine if the assistant''s reply text indicates that an order has been confirmed, placed, processed, or scheduled (e.g., ''Thank you for confirming'', ''Your order is now being processed'', ''order confirmed'', ''will now be processed'').
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: did the bot reply indicate order was confirmed. Utility classifier.'
),
-- generate_invoice_cta_prompt (line 103)
(
    'tenant_inventaa_led_001',
    'generate_invoice_cta_prompt',
    'en', 1, 'active',
    'You are a WhatsApp assistant for {biz_name}.
The customer''s order has just been confirmed. Write a short line (max 1 line) asking them to reply with ''Confirm'' or ''Proceed'' so we can automatically generate and send their tax invoice.
Make it natural and warm, and use emojis if appropriate.
Example: Reply ''Proceed'' or ''Confirm'' to get your invoice right away! 📄',
    'biz_name',
    'CTA line asking customer to confirm to receive their invoice.'
),
-- invoice_confirmation_request_check_prompt (line 150)
(
    'tenant_inventaa_led_001',
    'invoice_confirmation_request_check_prompt',
    'en', 1, 'active',
    'Determine if the user is replying with confirmation (like ''Proceed'', ''Confirm'', ''Yes proceed'', ''do it'', ''sure'') to the assistant''s previous message asking them to confirm or proceed to generate/receive their invoice.
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer confirming to generate invoice. Utility classifier.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- ROUTER: _check_fast_confirm (line 329)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'fast_order_confirm_check_prompt',
    'en', 1, 'active',
    'Bot showed order summary and asked customer to confirm.
Is the customer confirming to place the order?
YES: confirm, proceed, yes, ok, sure, do it, go ahead
NO: quantity change (add more, increase to N, make it 7)
NO: any unrelated question or new request
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer confirming order after seeing summary. Utility classifier.'
) ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- GRAPHRAG HANDLER: category matcher (line 302)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'category_matcher_prompt',
    'en', 1, 'active',
    'The customer is choosing from this category list:
{cat_list}

Reply with ONLY the exact category name from the list that best matches the customer''s message. Reply ''NONE'' if it matches nothing.',
    'cat_list',
    'Matches customer text to a product category. cat_list is injected at runtime.'
) ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- PRODUCT FOLLOWUP: 13 prompts (the largest migration)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
-- Line 67: Data extraction assistant
(
    'tenant_inventaa_led_001',
    'pf_data_extraction_prompt',
    'en', 1, 'active',
    'You are a data extraction assistant for {biz_name}.
Extract structured information from the customer message about product selection and ordering intent.

PRODUCT CATALOG (reference only):
{product_catalog}

Extract and return JSON with these fields:
- selected_product_name: exact product name if customer selected one, else null
- parsed_order_quantity: integer if customer mentioned a quantity, else null
- is_comparison: true if customer wants to compare products
- is_offer_inquiry: true if customer is asking about offers/discounts
- is_installation_inquiry: true if customer wants installation guide or images

Reply ONLY with valid JSON. No explanation.',
    'biz_name,product_catalog',
    'Extracts product selection, quantity, and intent from customer message.'
),
-- Line 181: Product discussion history resolver
(
    'tenant_inventaa_led_001',
    'pf_history_resolver_prompt',
    'en', 1, 'active',
    'You are analyzing a WhatsApp conversation to identify which products were recently discussed.

AVAILABLE PRODUCTS:
{product_list}

RECENT CONVERSATION:
{session_history}

Which products from the list were discussed in this conversation? List only the product names that were explicitly mentioned or discussed. Reply with a JSON array of product names, or [] if none.',
    'product_list,session_history',
    'Identifies which products from catalog were discussed in session history.'
),
-- Line 458: Offer inquiry check Layer-1
(
    'tenant_inventaa_led_001',
    'pf_offer_inquiry_check_prompt',
    'en', 1, 'active',
    'Is the customer asking about available offers, discounts, deals, or promotions?
Examples that are YES: "any offers?", "is there a discount?", "what deals do you have?", "any sale?"
Examples that are NO: product questions, quantity changes, confirmations
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer asking about store offers/discounts. Layer-1 classifier.'
),
-- Line 521: Negotiation product change check
(
    'tenant_inventaa_led_001',
    'pf_neg_product_change_check_prompt',
    'en', 1, 'active',
    'The customer is currently negotiating price for: {current_product}

Did the customer switch to a different product in their latest message?
Reply ONLY ''YES'' or ''NO''.',
    'current_product',
    'YES/NO: did customer switch products during negotiation.'
),
-- Line 842: Vague reference checker
(
    'tenant_inventaa_led_001',
    'pf_vague_reference_check_prompt',
    'en', 1, 'active',
    'Does this customer message use vague pronouns or references (like "it", "this", "that", "the product", "this one") that refer to a product without naming it explicitly?
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: does message contain vague product references. Utility classifier.'
),
-- Line 863: Vague reference rewriter
(
    'tenant_inventaa_led_001',
    'pf_vague_reference_rewriter_prompt',
    'en', 1, 'active',
    'Rewrite the customer message by replacing all vague pronouns (it, this, that, the product) with the actual product name: {product_name}

Customer message: {customer_message}

Reply ONLY with the rewritten message. Keep everything else exactly the same.',
    'product_name,customer_message',
    'Rewrites customer message replacing vague pronouns with actual product name.'
),
-- Line 906: Offer inquiry check Layer-2
(
    'tenant_inventaa_led_001',
    'pf_offer_inquiry_check_l2_prompt',
    'en', 1, 'active',
    'The customer asked: {customer_message}

Is this specifically asking about named offers, promotions, or discount tiers available for a product?
Reply ONLY ''YES'' or ''NO''.',
    'customer_message',
    'YES/NO: Layer-2 check for named offer/tier inquiry.'
),
-- Line 959: Named product extractor
(
    'tenant_inventaa_led_001',
    'pf_named_product_extractor_prompt',
    'en', 1, 'active',
    'Extract all specific product names mentioned in this message.

Available products:
{product_list}

Customer message: {customer_message}

Reply ONLY with a JSON array of matched product names from the available list, or [] if none match.',
    'product_list,customer_message',
    'Extracts specific product names from customer message against catalog.'
),
-- Line 1034: Sales assistant offers formatter
(
    'tenant_inventaa_led_001',
    'pf_offers_formatter_prompt',
    'en', 1, 'active',
    'You are a helpful WhatsApp sales assistant for {biz_name}.

Format the following store offers information clearly for the customer.
Include discount tiers, minimum order values, and free shipping conditions if available.
Use emojis and WhatsApp formatting (*bold*). Keep it concise — max 8 lines.
Address the customer as {sender_name}.

STORE OFFERS DATA:
{offers_data}

PRODUCT CONTEXT (if applicable):
{product_context}',
    'biz_name,sender_name,offers_data,product_context',
    'Formats store tier offers into a WhatsApp-friendly message.'
),
-- Line 1149: Vague pronoun resolver Level-2
(
    'tenant_inventaa_led_001',
    'pf_vague_pronoun_resolver_l2_prompt',
    'en', 1, 'active',
    'Context: The customer has been discussing {product_name}.

Their latest message uses unclear references. Determine the most likely meaning:
- If they are asking about {product_name} specifically, reply: SAME_PRODUCT
- If they are asking about a different product, reply: DIFFERENT_PRODUCT
- If you cannot determine, reply: UNCLEAR

Customer message: {customer_message}

Reply ONLY with one of: SAME_PRODUCT, DIFFERENT_PRODUCT, UNCLEAR',
    'product_name,customer_message',
    'Level-2 vague reference resolution — determines if customer means same product.'
),
-- Line 1389: Side-by-side comparison prompt
(
    'tenant_inventaa_led_001',
    'pf_comparison_prompt',
    'en', 1, 'active',
    'You are a helpful product comparison assistant for {biz_name}.

Compare the following products side-by-side for the customer {sender_name}.

PRODUCTS TO COMPARE:
{products_data}

PRICE REFERENCE (use EXACT prices from here — do not estimate):
{price_reference}

FORMAT:
- Start with a brief intro line
- Use bullet points per product: name, price, key specs, best for
- End with a 1-line recommendation
- WhatsApp formatting (*bold* for product names and prices)
- Max 15 lines total
- Temperature must be 0 — never hallucinate prices

Reply ONLY with the comparison message.',
    'biz_name,sender_name,products_data,price_reference',
    'Side-by-side product comparison. Uses exact prices from price_reference.'
),
-- Line 1480: Image/installation intent classifier
(
    'tenant_inventaa_led_001',
    'pf_image_installation_intent_prompt',
    'en', 1, 'active',
    'The customer is asking about: {product_name}
Customer message: {customer_message}

Should the assistant send:
A) An installation guide / how-to instructions
B) Product photos / images
C) Neither (text answer is sufficient)

Reply ONLY with ''A'', ''B'', or ''C''.',
    'product_name,customer_message',
    'Determines whether to send installation guide, product image, or text reply.'
),
-- Line 1655: Main product follow-up prompt (the large core prompt)
(
    'tenant_inventaa_led_001',
    'pf_main_followup_prompt',
    'en', 1, 'active',
    'You are a helpful WhatsApp sales assistant for {biz_name}.
You are helping customer {sender_name} with their product inquiry.

ACTIVE PRODUCT CONTEXT:
{product_context}

CUSTOMER MEMORY:
{customer_preferences}

SESSION WORKFLOW:
{workflow_context}

CURRENT CATALOG DATA:
{catalog_data}

INTENT CLASSIFICATION:
{parsed_intent}

INSTRUCTIONS BY INTENT:

INTENT A1 — Customer ordering (quantity specified):
→ Generate order summary with product, quantity, unit price, store discount, subtotal, GST, total payable
→ Show upsell tier hint if next tier exists
→ End with "Please confirm and we''ll process your order! 🎉"

INTENT A2 — Customer ordering (no quantity yet):
→ Ask clearly: "How many units of {product_name} would you like?"

INTENT B — Product question / FAQ:
→ Answer from product context above
→ Be concise, warm, use *bold* for key specs
→ If installation guide requested, mention it is available

INTENT C — Comparison request:
→ Route to comparison handler (do not answer here)

RULES:
- Address as {sender_name}
- Use *bold* for prices and product names
- Never hallucinate prices — use only the data provided
- Max 12 lines
- WhatsApp formatting only (no markdown tables)',
    'biz_name,sender_name,product_context,customer_preferences,workflow_context,catalog_data,parsed_intent,product_name',
    'Main product follow-up prompt. Handles A1/A2/B/C intents. Core of product_followup.py.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- Register all new keys in PROMPT_KEYS (add to prompt_store.py)
-- ══════════════════════════════════════════════════════════════════════════════
-- New keys to add to PROMPT_KEYS dict in db/prompt_store.py:
--   "parse_global_offer_tiers_prompt"
--   "invoice_inquiry_check_prompt"
--   "order_confirmation_reply_check_prompt"
--   "generate_invoice_cta_prompt"
--   "invoice_confirmation_request_check_prompt"
--   "fast_order_confirm_check_prompt"
--   "category_matcher_prompt"
--   "pf_data_extraction_prompt"
--   "pf_history_resolver_prompt"
--   "pf_offer_inquiry_check_prompt"
--   "pf_neg_product_change_check_prompt"
--   "pf_vague_reference_check_prompt"
--   "pf_vague_reference_rewriter_prompt"
--   "pf_offer_inquiry_check_l2_prompt"
--   "pf_named_product_extractor_prompt"
--   "pf_offers_formatter_prompt"
--   "pf_vague_pronoun_resolver_l2_prompt"
--   "pf_comparison_prompt"
--   "pf_image_installation_intent_prompt"
--   "pf_main_followup_prompt"

-- ══════════════════════════════════════════════════════════════════════════════
-- VERIFY
-- ══════════════════════════════════════════════════════════════════════════════
SELECT
    prompt_name,
    LEFT(prompt_text, 60) AS preview,
    variables,
    status
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
ORDER BY prompt_name;

-- Additional keys added during product_followup.py migration
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
(
    'tenant_inventaa_led_001',
    'pf_new_search_followup_classifier_prompt',
    'en', 1, 'active',
    'You classify a customer message as NEW_SEARCH or FOLLOW_UP.

NEW_SEARCH — customer is asking about a different product or category not previously discussed.
FOLLOW_UP — customer is asking about the same product or products already shown.

Reply ONLY with NEW_SEARCH or FOLLOW_UP.',
    NULL,
    'Classifies whether customer wants a new search or follow-up on existing product.'
),
(
    'tenant_inventaa_led_001',
    'pf_comparison_prompt',
    'en', 1, 'active',
    'You are a helpful product comparison assistant for {biz_name}.

Compare the following products side-by-side for {sender_name}.

PRODUCTS:
{products_data}

FORMAT:
- Brief intro line
- Bullet points per product: name, price, key specs, best for
- 1-line recommendation at end
- WhatsApp formatting (*bold* for names and prices)
- Max 15 lines total
- Never estimate prices — use only the data provided

Reply ONLY with the comparison message.',
    'biz_name,sender_name,products_data',
    'Side-by-side product comparison formatter.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;





-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 014: Migrate all remaining hardcoded prompts to prompt_templates
-- Covers: product_followup.py (13 prompts), invoice_handler.py (4 prompts),
--         router.py (1 prompt), graphrag_handler.py (1 prompt),
--         negotiator.py (1 prompt — parse_global_offer_tiers)
-- ══════════════════════════════════════════════════════════════════════════════

-- Ensure prompt_templates table exists (from migration 011)
CREATE TABLE IF NOT EXISTS prompt_templates (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT        NOT NULL,
    prompt_name   TEXT        NOT NULL,
    language      TEXT        NOT NULL DEFAULT 'en',
    version       INT         NOT NULL DEFAULT 1,
    status        TEXT        NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active', 'draft', 'archived')),
    prompt_text   TEXT        NOT NULL,
    variables     TEXT        DEFAULT NULL,
    description   TEXT        DEFAULT NULL,
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, prompt_name, language, version)
);

CREATE INDEX IF NOT EXISTS idx_prompt_templates_lookup
    ON prompt_templates (tenant_id, prompt_name, language, status);

-- ══════════════════════════════════════════════════════════════════════════════
-- NEGOTIATOR: parse_global_offer_tiers (line 34)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'parse_global_offer_tiers_prompt',
    'en', 1, 'active',
    'Extract value-based discount tiers from this store offers text.
Return ONLY a JSON array of [min_order_value, discount_pct] pairs.
Example: [[2500, 2], [7500, 5], [14500, 8]]
Sort ascending by min_order_value. Return [] if none found.',
    NULL,
    'Parses store offer text into structured tier JSON. Domain-independent utility.'
) ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- INVOICE HANDLER: 4 prompts
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
-- invoice_inquiry_check_prompt (line 41)
(
    'tenant_inventaa_led_001',
    'invoice_inquiry_check_prompt',
    'en', 1, 'active',
    'Determine if the user is explicitly asking for their invoice, receipt, bill, or payment document for their order (e.g. ''where is my invoice'', ''send invoice'', ''invoice please'', ''show bill'').
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer asking for their invoice. Domain-independent utility classifier.'
),
-- order_confirmation_reply_check_prompt (line 72)
(
    'tenant_inventaa_led_001',
    'order_confirmation_reply_check_prompt',
    'en', 1, 'active',
    'Determine if the assistant''s reply text indicates that an order has been confirmed, placed, processed, or scheduled (e.g., ''Thank you for confirming'', ''Your order is now being processed'', ''order confirmed'', ''will now be processed'').
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: did the bot reply indicate order was confirmed. Utility classifier.'
),
-- generate_invoice_cta_prompt (line 103)
(
    'tenant_inventaa_led_001',
    'generate_invoice_cta_prompt',
    'en', 1, 'active',
    'You are a WhatsApp assistant for {biz_name}.
The customer''s order has just been confirmed. Write a short line (max 1 line) asking them to reply with ''Confirm'' or ''Proceed'' so we can automatically generate and send their tax invoice.
Make it natural and warm, and use emojis if appropriate.
Example: Reply ''Proceed'' or ''Confirm'' to get your invoice right away! 📄',
    'biz_name',
    'CTA line asking customer to confirm to receive their invoice.'
),
-- invoice_confirmation_request_check_prompt (line 150)
(
    'tenant_inventaa_led_001',
    'invoice_confirmation_request_check_prompt',
    'en', 1, 'active',
    'Determine if the user is replying with confirmation (like ''Proceed'', ''Confirm'', ''Yes proceed'', ''do it'', ''sure'') to the assistant''s previous message asking them to confirm or proceed to generate/receive their invoice.
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer confirming to generate invoice. Utility classifier.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- ROUTER: _check_fast_confirm (line 329)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'fast_order_confirm_check_prompt',
    'en', 1, 'active',
    'Bot showed order summary and asked customer to confirm.
Is the customer confirming to place the order?
YES: confirm, proceed, yes, ok, sure, do it, go ahead
NO: quantity change (add more, increase to N, make it 7)
NO: any unrelated question or new request
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer confirming order after seeing summary. Utility classifier.'
) ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- GRAPHRAG HANDLER: category matcher (line 302)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'category_matcher_prompt',
    'en', 1, 'active',
    'The customer is choosing from this category list:
{cat_list}

Reply with ONLY the exact category name from the list that best matches the customer''s message. Reply ''NONE'' if it matches nothing.',
    'cat_list',
    'Matches customer text to a product category. cat_list is injected at runtime.'
) ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- PRODUCT FOLLOWUP: 13 prompts (the largest migration)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
-- Line 67: Data extraction assistant
(
    'tenant_inventaa_led_001',
    'pf_data_extraction_prompt',
    'en', 1, 'active',
    'You are a data extraction assistant for {biz_name}.
Extract structured information from the customer message about product selection and ordering intent.

PRODUCT CATALOG (reference only):
{product_catalog}

Extract and return JSON with these fields:
- selected_product_name: exact product name if customer selected one, else null
- parsed_order_quantity: integer if customer mentioned a quantity, else null
- is_comparison: true if customer wants to compare products
- is_offer_inquiry: true if customer is asking about offers/discounts
- is_installation_inquiry: true if customer wants installation guide or images

Reply ONLY with valid JSON. No explanation.',
    'biz_name,product_catalog',
    'Extracts product selection, quantity, and intent from customer message.'
),
-- Line 181: Product discussion history resolver
(
    'tenant_inventaa_led_001',
    'pf_history_resolver_prompt',
    'en', 1, 'active',
    'You are analyzing a WhatsApp conversation to identify which products were recently discussed.

AVAILABLE PRODUCTS:
{product_list}

RECENT CONVERSATION:
{session_history}

Which products from the list were discussed in this conversation? List only the product names that were explicitly mentioned or discussed. Reply with a JSON array of product names, or [] if none.',
    'product_list,session_history',
    'Identifies which products from catalog were discussed in session history.'
),
-- Line 458: Offer inquiry check Layer-1
(
    'tenant_inventaa_led_001',
    'pf_offer_inquiry_check_prompt',
    'en', 1, 'active',
    'Is the customer asking about available offers, discounts, deals, or promotions?
Examples that are YES: "any offers?", "is there a discount?", "what deals do you have?", "any sale?"
Examples that are NO: product questions, quantity changes, confirmations
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer asking about store offers/discounts. Layer-1 classifier.'
),
-- Line 521: Negotiation product change check
(
    'tenant_inventaa_led_001',
    'pf_neg_product_change_check_prompt',
    'en', 1, 'active',
    'The customer is currently negotiating price for: {current_product}

Did the customer switch to a different product in their latest message?
Reply ONLY ''YES'' or ''NO''.',
    'current_product',
    'YES/NO: did customer switch products during negotiation.'
),
-- Line 842: Vague reference checker
(
    'tenant_inventaa_led_001',
    'pf_vague_reference_check_prompt',
    'en', 1, 'active',
    'Does this customer message use vague pronouns or references (like "it", "this", "that", "the product", "this one") that refer to a product without naming it explicitly?
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: does message contain vague product references. Utility classifier.'
),
-- Line 863: Vague reference rewriter
(
    'tenant_inventaa_led_001',
    'pf_vague_reference_rewriter_prompt',
    'en', 1, 'active',
    'Rewrite the customer message by replacing all vague pronouns (it, this, that, the product) with the actual product name: {product_name}

Customer message: {customer_message}

Reply ONLY with the rewritten message. Keep everything else exactly the same.',
    'product_name,customer_message',
    'Rewrites customer message replacing vague pronouns with actual product name.'
),
-- Line 906: Offer inquiry check Layer-2
(
    'tenant_inventaa_led_001',
    'pf_offer_inquiry_check_l2_prompt',
    'en', 1, 'active',
    'The customer asked: {customer_message}

Is this specifically asking about named offers, promotions, or discount tiers available for a product?
Reply ONLY ''YES'' or ''NO''.',
    'customer_message',
    'YES/NO: Layer-2 check for named offer/tier inquiry.'
),
-- Line 959: Named product extractor
(
    'tenant_inventaa_led_001',
    'pf_named_product_extractor_prompt',
    'en', 1, 'active',
    'Extract all specific product names mentioned in this message.

Available products:
{product_list}

Customer message: {customer_message}

Reply ONLY with a JSON array of matched product names from the available list, or [] if none match.',
    'product_list,customer_message',
    'Extracts specific product names from customer message against catalog.'
),
-- Line 1034: Sales assistant offers formatter
(
    'tenant_inventaa_led_001',
    'pf_offers_formatter_prompt',
    'en', 1, 'active',
    'You are a helpful WhatsApp sales assistant for {biz_name}.

Format the following store offers information clearly for the customer.
Include discount tiers, minimum order values, and free shipping conditions if available.
Use emojis and WhatsApp formatting (*bold*). Keep it concise — max 8 lines.
Address the customer as {sender_name}.

STORE OFFERS DATA:
{offers_data}

PRODUCT CONTEXT (if applicable):
{product_context}',
    'biz_name,sender_name,offers_data,product_context',
    'Formats store tier offers into a WhatsApp-friendly message.'
),
-- Line 1149: Vague pronoun resolver Level-2
(
    'tenant_inventaa_led_001',
    'pf_vague_pronoun_resolver_l2_prompt',
    'en', 1, 'active',
    'Context: The customer has been discussing {product_name}.

Their latest message uses unclear references. Determine the most likely meaning:
- If they are asking about {product_name} specifically, reply: SAME_PRODUCT
- If they are asking about a different product, reply: DIFFERENT_PRODUCT
- If you cannot determine, reply: UNCLEAR

Customer message: {customer_message}

Reply ONLY with one of: SAME_PRODUCT, DIFFERENT_PRODUCT, UNCLEAR',
    'product_name,customer_message',
    'Level-2 vague reference resolution — determines if customer means same product.'
),
-- Line 1389: Side-by-side comparison prompt
(
    'tenant_inventaa_led_001',
    'pf_comparison_prompt',
    'en', 1, 'active',
    'You are a helpful product comparison assistant for {biz_name}.

Compare the following products side-by-side for the customer {sender_name}.

PRODUCTS TO COMPARE:
{products_data}

PRICE REFERENCE (use EXACT prices from here — do not estimate):
{price_reference}

FORMAT:
- Start with a brief intro line
- Use bullet points per product: name, price, key specs, best for
- End with a 1-line recommendation
- WhatsApp formatting (*bold* for product names and prices)
- Max 15 lines total
- Temperature must be 0 — never hallucinate prices

Reply ONLY with the comparison message.',
    'biz_name,sender_name,products_data,price_reference',
    'Side-by-side product comparison. Uses exact prices from price_reference.'
),
-- Line 1480: Image/installation intent classifier
(
    'tenant_inventaa_led_001',
    'pf_image_installation_intent_prompt',
    'en', 1, 'active',
    'The customer is asking about: {product_name}
Customer message: {customer_message}

Should the assistant send:
A) An installation guide / how-to instructions
B) Product photos / images
C) Neither (text answer is sufficient)

Reply ONLY with ''A'', ''B'', or ''C''.',
    'product_name,customer_message',
    'Determines whether to send installation guide, product image, or text reply.'
),
-- Line 1655: Main product follow-up prompt (the large core prompt)
(
    'tenant_inventaa_led_001',
    'pf_main_followup_prompt',
    'en', 1, 'active',
    'You are a helpful WhatsApp sales assistant for {biz_name}.
You are helping customer {sender_name} with their product inquiry.

ACTIVE PRODUCT CONTEXT:
{product_context}

CUSTOMER MEMORY:
{customer_preferences}

SESSION WORKFLOW:
{workflow_context}

CURRENT CATALOG DATA:
{catalog_data}

INTENT CLASSIFICATION:
{parsed_intent}

INSTRUCTIONS BY INTENT:

INTENT A1 — Customer ordering (quantity specified):
→ Generate order summary with product, quantity, unit price, store discount, subtotal, GST, total payable
→ Show upsell tier hint if next tier exists
→ End with "Please confirm and we''ll process your order! 🎉"

INTENT A2 — Customer ordering (no quantity yet):
→ Ask clearly: "How many units of {product_name} would you like?"

INTENT B — Product question / FAQ:
→ Answer from product context above
→ Be concise, warm, use *bold* for key specs
→ If installation guide requested, mention it is available

INTENT C — Comparison request:
→ Route to comparison handler (do not answer here)

RULES:
- Address as {sender_name}
- Use *bold* for prices and product names
- Never hallucinate prices — use only the data provided
- Max 12 lines
- WhatsApp formatting only (no markdown tables)',
    'biz_name,sender_name,product_context,customer_preferences,workflow_context,catalog_data,parsed_intent,product_name',
    'Main product follow-up prompt. Handles A1/A2/B/C intents. Core of product_followup.py.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- Register all new keys in PROMPT_KEYS (add to prompt_store.py)
-- ══════════════════════════════════════════════════════════════════════════════
-- New keys to add to PROMPT_KEYS dict in db/prompt_store.py:
--   "parse_global_offer_tiers_prompt"
--   "invoice_inquiry_check_prompt"
--   "order_confirmation_reply_check_prompt"
--   "generate_invoice_cta_prompt"
--   "invoice_confirmation_request_check_prompt"
--   "fast_order_confirm_check_prompt"
--   "category_matcher_prompt"
--   "pf_data_extraction_prompt"
--   "pf_history_resolver_prompt"
--   "pf_offer_inquiry_check_prompt"
--   "pf_neg_product_change_check_prompt"
--   "pf_vague_reference_check_prompt"
--   "pf_vague_reference_rewriter_prompt"
--   "pf_offer_inquiry_check_l2_prompt"
--   "pf_named_product_extractor_prompt"
--   "pf_offers_formatter_prompt"
--   "pf_vague_pronoun_resolver_l2_prompt"
--   "pf_comparison_prompt"
--   "pf_image_installation_intent_prompt"
--   "pf_main_followup_prompt"

-- ══════════════════════════════════════════════════════════════════════════════
-- VERIFY
-- ══════════════════════════════════════════════════════════════════════════════
SELECT
    prompt_name,
    LEFT(prompt_text, 60) AS preview,
    variables,
    status
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
ORDER BY prompt_name;

-- Additional keys added during product_followup.py migration
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
(
    'tenant_inventaa_led_001',
    'pf_new_search_followup_classifier_prompt',
    'en', 1, 'active',
    'You classify a customer message as NEW_SEARCH or FOLLOW_UP.

NEW_SEARCH — customer is asking about a different product or category not previously discussed.
FOLLOW_UP — customer is asking about the same product or products already shown.

Reply ONLY with NEW_SEARCH or FOLLOW_UP.',
    NULL,
    'Classifies whether customer wants a new search or follow-up on existing product.'
),
(
    'tenant_inventaa_led_001',
    'pf_comparison_prompt',
    'en', 1, 'active',
    'You are a helpful product comparison assistant for {biz_name}.

Compare the following products side-by-side for {sender_name}.

PRODUCTS:
{products_data}

FORMAT:
- Brief intro line
- Bullet points per product: name, price, key specs, best for
- 1-line recommendation at end
- WhatsApp formatting (*bold* for names and prices)
- Max 15 lines total
- Never estimate prices — use only the data provided

Reply ONLY with the comparison message.',
    'biz_name,sender_name,products_data',
    'Side-by-side product comparison formatter.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- Item 3: Customer-facing reply templates (fallback strings in negotiator.py)
-- These replace the hardcoded f-strings that fire when DB prompt calls fail.
-- Moving them to DB means tenants can customize tone, language, and currency.
-- ══════════════════════════════════════════════════════════════════════════════

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
-- Fallback when neg_no_discount_prompt DB call fails
(
    'tenant_inventaa_led_001',
    'neg_no_discount_fallback',
    'en', 1, 'active',
    '{sender_name}, price is *Rs.{price_num}/unit* (Total: *Rs.{total}*). Buy {min_units}+ units for extra discounts!',
    'sender_name,price_num,total,min_units',
    'Fallback reply when no discount is available. Customer-facing tone, customizable per tenant.'
),
-- Fallback when neg_first_offer_prompt DB call fails
(
    'tenant_inventaa_led_001',
    'neg_first_offer_fallback',
    'en', 1, 'active',
    'Great news, {sender_name}! 🎉 For *{quantity} units* of *{product_name}*: *Rs.{offer_price}/unit* (Total: *Rs.{offer_total}*). Shall we proceed?',
    'sender_name,quantity,product_name,offer_price,offer_total',
    'Fallback first offer reply. Use emojis and tone matching tenant brand voice.'
),
-- Fallback when neg_counter_offer_prompt DB call fails
(
    'tenant_inventaa_led_001',
    'neg_counter_offer_fallback',
    'en', 1, 'active',
    '{sender_name}, we can do *Rs.{new_offer}/unit* (Total: *Rs.{new_total}* for {quantity} units). {closing_line}',
    'sender_name,new_offer,new_total,quantity,closing_line',
    'Fallback counter-offer reply. closing_line = "This is our best price." or "Shall we proceed?"'
),
-- Fallback when neg_final_price_prompt DB call fails
(
    'tenant_inventaa_led_001',
    'neg_final_price_fallback',
    'en', 1, 'active',
    '{sender_name}, *Rs.{last_offer}/unit* is our absolute best price (Total: *Rs.{total}*). 🙏 Would you like to proceed?',
    'sender_name,last_offer,total',
    'Fallback final price reply. Customizable for different tone/language per tenant.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;








-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 014: Migrate all remaining hardcoded prompts to prompt_templates
-- Covers: product_followup.py (13 prompts), invoice_handler.py (4 prompts),
--         router.py (1 prompt), graphrag_handler.py (1 prompt),
--         negotiator.py (1 prompt — parse_global_offer_tiers)
-- ══════════════════════════════════════════════════════════════════════════════

-- Ensure prompt_templates table exists (from migration 011)
CREATE TABLE IF NOT EXISTS prompt_templates (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT        NOT NULL,
    prompt_name   TEXT        NOT NULL,
    language      TEXT        NOT NULL DEFAULT 'en',
    version       INT         NOT NULL DEFAULT 1,
    status        TEXT        NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active', 'draft', 'archived')),
    prompt_text   TEXT        NOT NULL,
    variables     TEXT        DEFAULT NULL,
    description   TEXT        DEFAULT NULL,
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, prompt_name, language, version)
);

CREATE INDEX IF NOT EXISTS idx_prompt_templates_lookup
    ON prompt_templates (tenant_id, prompt_name, language, status);

-- ══════════════════════════════════════════════════════════════════════════════
-- NEGOTIATOR: parse_global_offer_tiers (line 34)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'parse_global_offer_tiers_prompt',
    'en', 1, 'active',
    'Extract value-based discount tiers from this store offers text.
Return ONLY a JSON array of [min_order_value, discount_pct] pairs.
Example: [[2500, 2], [7500, 5], [14500, 8]]
Sort ascending by min_order_value. Return [] if none found.',
    NULL,
    'Parses store offer text into structured tier JSON. Domain-independent utility.'
) ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- INVOICE HANDLER: 4 prompts
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
-- invoice_inquiry_check_prompt (line 41)
(
    'tenant_inventaa_led_001',
    'invoice_inquiry_check_prompt',
    'en', 1, 'active',
    'Determine if the user is explicitly asking for their invoice, receipt, bill, or payment document for their order (e.g. ''where is my invoice'', ''send invoice'', ''invoice please'', ''show bill'').
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer asking for their invoice. Domain-independent utility classifier.'
),
-- order_confirmation_reply_check_prompt (line 72)
(
    'tenant_inventaa_led_001',
    'order_confirmation_reply_check_prompt',
    'en', 1, 'active',
    'Determine if the assistant''s reply text indicates that an order has been confirmed, placed, processed, or scheduled (e.g., ''Thank you for confirming'', ''Your order is now being processed'', ''order confirmed'', ''will now be processed'').
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: did the bot reply indicate order was confirmed. Utility classifier.'
),
-- generate_invoice_cta_prompt (line 103)
(
    'tenant_inventaa_led_001',
    'generate_invoice_cta_prompt',
    'en', 1, 'active',
    'You are a WhatsApp assistant for {biz_name}.
The customer''s order has just been confirmed. Write a short line (max 1 line) asking them to reply with ''Confirm'' or ''Proceed'' so we can automatically generate and send their tax invoice.
Make it natural and warm, and use emojis if appropriate.
Example: Reply ''Proceed'' or ''Confirm'' to get your invoice right away! 📄',
    'biz_name',
    'CTA line asking customer to confirm to receive their invoice.'
),
-- invoice_confirmation_request_check_prompt (line 150)
(
    'tenant_inventaa_led_001',
    'invoice_confirmation_request_check_prompt',
    'en', 1, 'active',
    'Determine if the user is replying with confirmation (like ''Proceed'', ''Confirm'', ''Yes proceed'', ''do it'', ''sure'') to the assistant''s previous message asking them to confirm or proceed to generate/receive their invoice.
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer confirming to generate invoice. Utility classifier.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- ROUTER: _check_fast_confirm (line 329)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'fast_order_confirm_check_prompt',
    'en', 1, 'active',
    'Bot showed order summary and asked customer to confirm.
Is the customer confirming to place the order?
YES: confirm, proceed, yes, ok, sure, do it, go ahead
NO: quantity change (add more, increase to N, make it 7)
NO: any unrelated question or new request
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer confirming order after seeing summary. Utility classifier.'
) ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- GRAPHRAG HANDLER: category matcher (line 302)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'category_matcher_prompt',
    'en', 1, 'active',
    'The customer is choosing from this category list:
{cat_list}

Reply with ONLY the exact category name from the list that best matches the customer''s message. Reply ''NONE'' if it matches nothing.',
    'cat_list',
    'Matches customer text to a product category. cat_list is injected at runtime.'
) ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- PRODUCT FOLLOWUP: 13 prompts (the largest migration)
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
-- Line 67: Data extraction assistant
(
    'tenant_inventaa_led_001',
    'pf_data_extraction_prompt',
    'en', 1, 'active',
    'You are a data extraction assistant for {biz_name}.
Extract structured information from the customer message about product selection and ordering intent.

PRODUCT CATALOG (reference only):
{product_catalog}

Extract and return JSON with these fields:
- selected_product_name: exact product name if customer selected one, else null
- parsed_order_quantity: integer if customer mentioned a quantity, else null
- is_comparison: true if customer wants to compare products
- is_offer_inquiry: true if customer is asking about offers/discounts
- is_installation_inquiry: true if customer wants installation guide or images

Reply ONLY with valid JSON. No explanation.',
    'biz_name,product_catalog',
    'Extracts product selection, quantity, and intent from customer message.'
),
-- Line 181: Product discussion history resolver
(
    'tenant_inventaa_led_001',
    'pf_history_resolver_prompt',
    'en', 1, 'active',
    'You are analyzing a WhatsApp conversation to identify which products were recently discussed.

AVAILABLE PRODUCTS:
{product_list}

RECENT CONVERSATION:
{session_history}

Which products from the list were discussed in this conversation? List only the product names that were explicitly mentioned or discussed. Reply with a JSON array of product names, or [] if none.',
    'product_list,session_history',
    'Identifies which products from catalog were discussed in session history.'
),
-- Line 458: Offer inquiry check Layer-1
(
    'tenant_inventaa_led_001',
    'pf_offer_inquiry_check_prompt',
    'en', 1, 'active',
    'Is the customer asking about available offers, discounts, deals, or promotions?
Examples that are YES: "any offers?", "is there a discount?", "what deals do you have?", "any sale?"
Examples that are NO: product questions, quantity changes, confirmations
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: customer asking about store offers/discounts. Layer-1 classifier.'
),
-- Line 521: Negotiation product change check
(
    'tenant_inventaa_led_001',
    'pf_neg_product_change_check_prompt',
    'en', 1, 'active',
    'The customer is currently negotiating price for: {current_product}

Did the customer switch to a different product in their latest message?
Reply ONLY ''YES'' or ''NO''.',
    'current_product',
    'YES/NO: did customer switch products during negotiation.'
),
-- Line 842: Vague reference checker
(
    'tenant_inventaa_led_001',
    'pf_vague_reference_check_prompt',
    'en', 1, 'active',
    'Does this customer message use vague pronouns or references (like "it", "this", "that", "the product", "this one") that refer to a product without naming it explicitly?
Reply ONLY ''YES'' or ''NO''.',
    NULL,
    'YES/NO: does message contain vague product references. Utility classifier.'
),
-- Line 863: Vague reference rewriter
(
    'tenant_inventaa_led_001',
    'pf_vague_reference_rewriter_prompt',
    'en', 1, 'active',
    'Rewrite the customer message by replacing all vague pronouns (it, this, that, the product) with the actual product name: {product_name}

Customer message: {customer_message}

Reply ONLY with the rewritten message. Keep everything else exactly the same.',
    'product_name,customer_message',
    'Rewrites customer message replacing vague pronouns with actual product name.'
),
-- Line 906: Offer inquiry check Layer-2
(
    'tenant_inventaa_led_001',
    'pf_offer_inquiry_check_l2_prompt',
    'en', 1, 'active',
    'The customer asked: {customer_message}

Is this specifically asking about named offers, promotions, or discount tiers available for a product?
Reply ONLY ''YES'' or ''NO''.',
    'customer_message',
    'YES/NO: Layer-2 check for named offer/tier inquiry.'
),
-- Line 959: Named product extractor
(
    'tenant_inventaa_led_001',
    'pf_named_product_extractor_prompt',
    'en', 1, 'active',
    'Extract all specific product names mentioned in this message.

Available products:
{product_list}

Customer message: {customer_message}

Reply ONLY with a JSON array of matched product names from the available list, or [] if none match.',
    'product_list,customer_message',
    'Extracts specific product names from customer message against catalog.'
),
-- Line 1034: Sales assistant offers formatter
(
    'tenant_inventaa_led_001',
    'pf_offers_formatter_prompt',
    'en', 1, 'active',
    'You are a helpful WhatsApp sales assistant for {biz_name}.

Format the following store offers information clearly for the customer.
Include discount tiers, minimum order values, and free shipping conditions if available.
Use emojis and WhatsApp formatting (*bold*). Keep it concise — max 8 lines.
Address the customer as {sender_name}.

STORE OFFERS DATA:
{offers_data}

PRODUCT CONTEXT (if applicable):
{product_context}',
    'biz_name,sender_name,offers_data,product_context',
    'Formats store tier offers into a WhatsApp-friendly message.'
),
-- Line 1149: Vague pronoun resolver Level-2
(
    'tenant_inventaa_led_001',
    'pf_vague_pronoun_resolver_l2_prompt',
    'en', 1, 'active',
    'Context: The customer has been discussing {product_name}.

Their latest message uses unclear references. Determine the most likely meaning:
- If they are asking about {product_name} specifically, reply: SAME_PRODUCT
- If they are asking about a different product, reply: DIFFERENT_PRODUCT
- If you cannot determine, reply: UNCLEAR

Customer message: {customer_message}

Reply ONLY with one of: SAME_PRODUCT, DIFFERENT_PRODUCT, UNCLEAR',
    'product_name,customer_message',
    'Level-2 vague reference resolution — determines if customer means same product.'
),
-- Line 1389: Side-by-side comparison prompt
(
    'tenant_inventaa_led_001',
    'pf_comparison_prompt',
    'en', 1, 'active',
    'You are a helpful product comparison assistant for {biz_name}.

Compare the following products side-by-side for the customer {sender_name}.

PRODUCTS TO COMPARE:
{products_data}

PRICE REFERENCE (use EXACT prices from here — do not estimate):
{price_reference}

FORMAT:
- Start with a brief intro line
- Use bullet points per product: name, price, key specs, best for
- End with a 1-line recommendation
- WhatsApp formatting (*bold* for product names and prices)
- Max 15 lines total
- Temperature must be 0 — never hallucinate prices

Reply ONLY with the comparison message.',
    'biz_name,sender_name,products_data,price_reference',
    'Side-by-side product comparison. Uses exact prices from price_reference.'
),
-- Line 1480: Image/installation intent classifier
(
    'tenant_inventaa_led_001',
    'pf_image_installation_intent_prompt',
    'en', 1, 'active',
    'The customer is asking about: {product_name}
Customer message: {customer_message}

Should the assistant send:
A) An installation guide / how-to instructions
B) Product photos / images
C) Neither (text answer is sufficient)

Reply ONLY with ''A'', ''B'', or ''C''.',
    'product_name,customer_message',
    'Determines whether to send installation guide, product image, or text reply.'
),
-- Line 1655: Main product follow-up prompt (the large core prompt)
(
    'tenant_inventaa_led_001',
    'pf_main_followup_prompt',
    'en', 1, 'active',
    'You are a helpful WhatsApp sales assistant for {biz_name}.
You are helping customer {sender_name} with their product inquiry.

ACTIVE PRODUCT CONTEXT:
{product_context}

CUSTOMER MEMORY:
{customer_preferences}

SESSION WORKFLOW:
{workflow_context}

CURRENT CATALOG DATA:
{catalog_data}

INTENT CLASSIFICATION:
{parsed_intent}

INSTRUCTIONS BY INTENT:

INTENT A1 — Customer ordering (quantity specified):
→ Generate order summary with product, quantity, unit price, store discount, subtotal, GST, total payable
→ Show upsell tier hint if next tier exists
→ End with "Please confirm and we''ll process your order! 🎉"

INTENT A2 — Customer ordering (no quantity yet):
→ Ask clearly: "How many units of {product_name} would you like?"

INTENT B — Product question / FAQ:
→ Answer from product context above
→ Be concise, warm, use *bold* for key specs
→ If installation guide requested, mention it is available

INTENT C — Comparison request:
→ Route to comparison handler (do not answer here)

RULES:
- Address as {sender_name}
- Use *bold* for prices and product names
- Never hallucinate prices — use only the data provided
- Max 12 lines
- WhatsApp formatting only (no markdown tables)',
    'biz_name,sender_name,product_context,customer_preferences,workflow_context,catalog_data,parsed_intent,product_name',
    'Main product follow-up prompt. Handles A1/A2/B/C intents. Core of product_followup.py.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- Register all new keys in PROMPT_KEYS (add to prompt_store.py)
-- ══════════════════════════════════════════════════════════════════════════════
-- New keys to add to PROMPT_KEYS dict in db/prompt_store.py:
--   "parse_global_offer_tiers_prompt"
--   "invoice_inquiry_check_prompt"
--   "order_confirmation_reply_check_prompt"
--   "generate_invoice_cta_prompt"
--   "invoice_confirmation_request_check_prompt"
--   "fast_order_confirm_check_prompt"
--   "category_matcher_prompt"
--   "pf_data_extraction_prompt"
--   "pf_history_resolver_prompt"
--   "pf_offer_inquiry_check_prompt"
--   "pf_neg_product_change_check_prompt"
--   "pf_vague_reference_check_prompt"
--   "pf_vague_reference_rewriter_prompt"
--   "pf_offer_inquiry_check_l2_prompt"
--   "pf_named_product_extractor_prompt"
--   "pf_offers_formatter_prompt"
--   "pf_vague_pronoun_resolver_l2_prompt"
--   "pf_comparison_prompt"
--   "pf_image_installation_intent_prompt"
--   "pf_main_followup_prompt"

-- ══════════════════════════════════════════════════════════════════════════════
-- VERIFY
-- ══════════════════════════════════════════════════════════════════════════════
SELECT
    prompt_name,
    LEFT(prompt_text, 60) AS preview,
    variables,
    status
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
ORDER BY prompt_name;

-- Additional keys added during product_followup.py migration
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
(
    'tenant_inventaa_led_001',
    'pf_new_search_followup_classifier_prompt',
    'en', 1, 'active',
    'You classify a customer message as NEW_SEARCH or FOLLOW_UP.

NEW_SEARCH — customer is asking about a different product or category not previously discussed.
FOLLOW_UP — customer is asking about the same product or products already shown.

Reply ONLY with NEW_SEARCH or FOLLOW_UP.',
    NULL,
    'Classifies whether customer wants a new search or follow-up on existing product.'
),
(
    'tenant_inventaa_led_001',
    'pf_comparison_prompt',
    'en', 1, 'active',
    'You are a helpful product comparison assistant for {biz_name}.

Compare the following products side-by-side for {sender_name}.

PRODUCTS:
{products_data}

FORMAT:
- Brief intro line
- Bullet points per product: name, price, key specs, best for
- 1-line recommendation at end
- WhatsApp formatting (*bold* for names and prices)
- Max 15 lines total
- Never estimate prices — use only the data provided

Reply ONLY with the comparison message.',
    'biz_name,sender_name,products_data',
    'Side-by-side product comparison formatter.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- Item 3: Customer-facing reply templates (fallback strings in negotiator.py)
-- These replace the hardcoded f-strings that fire when DB prompt calls fail.
-- Moving them to DB means tenants can customize tone, language, and currency.
-- ══════════════════════════════════════════════════════════════════════════════

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
-- Fallback when neg_no_discount_prompt DB call fails
(
    'tenant_inventaa_led_001',
    'neg_no_discount_fallback',
    'en', 1, 'active',
    '{sender_name}, price is *Rs.{price_num}/unit* (Total: *Rs.{total}*). Buy {min_units}+ units for extra discounts!',
    'sender_name,price_num,total,min_units',
    'Fallback reply when no discount is available. Customer-facing tone, customizable per tenant.'
),
-- Fallback when neg_first_offer_prompt DB call fails
(
    'tenant_inventaa_led_001',
    'neg_first_offer_fallback',
    'en', 1, 'active',
    'Great news, {sender_name}! 🎉 For *{quantity} units* of *{product_name}*: *Rs.{offer_price}/unit* (Total: *Rs.{offer_total}*). Shall we proceed?',
    'sender_name,quantity,product_name,offer_price,offer_total',
    'Fallback first offer reply. Use emojis and tone matching tenant brand voice.'
),
-- Fallback when neg_counter_offer_prompt DB call fails
(
    'tenant_inventaa_led_001',
    'neg_counter_offer_fallback',
    'en', 1, 'active',
    '{sender_name}, we can do *Rs.{new_offer}/unit* (Total: *Rs.{new_total}* for {quantity} units). {closing_line}',
    'sender_name,new_offer,new_total,quantity,closing_line',
    'Fallback counter-offer reply. closing_line = "This is our best price." or "Shall we proceed?"'
),
-- Fallback when neg_final_price_prompt DB call fails
(
    'tenant_inventaa_led_001',
    'neg_final_price_fallback',
    'en', 1, 'active',
    '{sender_name}, *Rs.{last_offer}/unit* is our absolute best price (Total: *Rs.{total}*). 🙏 Would you like to proceed?',
    'sender_name,last_offer,total',
    'Fallback final price reply. Customizable for different tone/language per tenant.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- Phase 2 optimization: single structured extraction prompt
-- Replaces 4 separate LLM detection calls with one structured JSON call.
-- ══════════════════════════════════════════════════════════════════════════════
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'neg_extract_negotiation_intent_prompt',
    'en', 1, 'active',
    'You are analyzing a customer message during a price negotiation for {product_name}.

Current offer: Rs.{current_price}/unit for {current_qty} units.

Classify the customer message and return ONLY a JSON object with these fields:
{
  "accepted": true/false,        -- customer agreed to the current offer
  "quantity_change": null or N,  -- new total quantity if customer changed it (integer)
  "counter_offer": null or N,    -- customer proposed price per unit (number)
  "more_discount": true/false    -- customer asked for more discount without specific price
}

PRECEDENCE RULES (apply in this order):
1. If customer clearly accepts current offer → accepted=true, set others to null/false
2. If customer changes quantity → quantity_change=N, counter_offer=null
3. If customer proposes specific price → counter_offer=N
4. If customer vaguely asks for discount → more_discount=true

Reply ONLY with the JSON object. No explanation.',
    'product_name,current_price,current_qty',
    'Single structured extraction replacing 4 detection calls. Returns JSON with accepted/quantity_change/counter_offer/more_discount.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- Update the extraction prompt with confidence scores, intent field, few-shot examples
-- (Replace the previous version inserted above)
UPDATE prompt_templates
SET
    prompt_text = 'You analyze a customer message during a price negotiation.

Product: {product_name}
Current offer: Rs.{current_price}/unit for {current_qty} units.

Return ONLY a JSON object in this exact schema:
{
  "intent": "ACCEPTED" | "QTY_CHANGE" | "COUNTER_OFFER" | "MORE_DISCOUNT" | "NONE",
  "matched_phrase": "the exact phrase that triggered your classification",
  "accepted":        {"value": true/false,   "confidence": 0.0-1.0},
  "quantity_change": {"value": N or null,     "confidence": 0.0-1.0},
  "counter_offer":   {"value": N or null,     "confidence": 0.0-1.0},
  "more_discount":   {"value": true/false,    "confidence": 0.0-1.0}
}

EXAMPLES:
Message: "Proceed"
→ {"intent":"ACCEPTED","matched_phrase":"Proceed","accepted":{"value":true,"confidence":0.99},"quantity_change":{"value":null,"confidence":0.99},"counter_offer":{"value":null,"confidence":0.99},"more_discount":{"value":false,"confidence":0.99}}

Message: "Make it 6 units"
→ {"intent":"QTY_CHANGE","matched_phrase":"Make it 6 units","accepted":{"value":false,"confidence":0.97},"quantity_change":{"value":6,"confidence":0.97},"counter_offer":{"value":null,"confidence":0.97},"more_discount":{"value":false,"confidence":0.97}}

Message: "1800 is my budget"
→ {"intent":"COUNTER_OFFER","matched_phrase":"1800 is my budget","accepted":{"value":false,"confidence":0.98},"quantity_change":{"value":null,"confidence":0.98},"counter_offer":{"value":1800,"confidence":0.98},"more_discount":{"value":false,"confidence":0.98}}

Message: "Can you do any better?"
→ {"intent":"MORE_DISCOUNT","matched_phrase":"Can you do any better","accepted":{"value":false,"confidence":0.95},"quantity_change":{"value":null,"confidence":0.95},"counter_offer":{"value":null,"confidence":0.95},"more_discount":{"value":true,"confidence":0.95}}

Message: "Proceed with 6 units"
→ {"intent":"ACCEPTED","matched_phrase":"Proceed with 6 units","accepted":{"value":true,"confidence":0.91},"quantity_change":{"value":6,"confidence":0.85},"counter_offer":{"value":null,"confidence":0.97},"more_discount":{"value":false,"confidence":0.97}}

Note: "Proceed with 6 units" → intent=ACCEPTED because acceptance takes precedence.
The caller enforces: ACCEPTED > QTY_CHANGE > COUNTER_OFFER > MORE_DISCOUNT.

Set confidence to reflect how certain you are. If ambiguous, set confidence below 0.75.
Reply ONLY with the JSON object.',
    version = 2,
    status  = 'active',
    variables = 'product_name,current_price,current_qty'
WHERE tenant_id    = 'tenant_inventaa_led_001'
  AND prompt_name  = 'neg_extract_negotiation_intent_prompt'
  AND language     = 'en'
  AND version      = 1;

-- Also archive v1
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
SELECT tenant_id, prompt_name, language, 2, 'active', prompt_text, variables,
       'v2: added confidence scores, intent field, matched_phrase, few-shot examples'
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'neg_extract_negotiation_intent_prompt'
  AND language = 'en' AND version = 1
ON CONFLICT DO NOTHING;

-- Add extraction telemetry columns to ai_metrics (Migration 013 extension)
ALTER TABLE ai_metrics
    ADD COLUMN IF NOT EXISTS neg_intent_latency_ms   INT     DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_intent_confidence   FLOAT   DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_intent_result       TEXT    DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_intent_fallback     BOOLEAN DEFAULT NULL;

-- View: extraction quality monitoring
CREATE OR REPLACE VIEW v_neg_extraction_quality AS
SELECT
    DATE_TRUNC('hour', created_at)            AS hour,
    COUNT(*)                                  AS total_negotiations,
    AVG(neg_intent_latency_ms)                AS avg_extraction_ms,
    AVG(neg_intent_confidence)                AS avg_confidence,
    SUM(CASE WHEN neg_intent_fallback THEN 1 ELSE 0 END) AS fallback_count,
    ROUND(100.0 * SUM(CASE WHEN neg_intent_fallback THEN 1 ELSE 0 END)
          / NULLIF(COUNT(*), 0), 1)           AS fallback_pct,
    MODE() WITHIN GROUP (ORDER BY neg_intent_result) AS most_common_intent
FROM ai_metrics
WHERE neg_intent_result IS NOT NULL
GROUP BY 1
ORDER BY 1 DESC;


SELECT * FROM v_neg_extraction_quality ORDER BY hour DESC LIMIT 24;





-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 015: Mem0 Architecture — Messages TTL + Semantic Summary Prompt
-- ══════════════════════════════════════════════════════════════════════════════

-- Step 4: Messages TTL — 20-minute retention policy
-- PostgreSQL doesn't support auto-delete, but you can run this on a schedule
-- (pg_cron, Supabase scheduled functions, or a nightly job).
-- This keeps the messages table lean while Mem0 holds semantic summaries.

-- Option A: Run manually or via cron job
-- DELETE FROM messages WHERE created_at < NOW() - INTERVAL '20 minutes';

-- Option B: Create a Postgres function for Supabase to call on schedule
CREATE OR REPLACE FUNCTION cleanup_old_messages()
RETURNS void AS $$
BEGIN
    DELETE FROM messages
    WHERE created_at < NOW() - INTERVAL '20 minutes'
      AND role = 'user';  -- keep bot replies slightly longer for debugging
    
    DELETE FROM messages
    WHERE created_at < NOW() - INTERVAL '60 minutes';  -- all messages after 1 hour
END;
$$ LANGUAGE plpgsql;

-- Step 5: Add conversation_summary_prompt to prompt_templates
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'conversation_summary_prompt',
    'en', 1, 'active',
    'Summarize this customer conversation in 3-5 concise sentences for future AI memory.

Focus ONLY on:
- Products discussed (include exact names)
- Purchase decisions (product, quantity, final price)
- Negotiation outcome (if any)
- Customer questions/concerns
- Preferences expressed

Do NOT include: greetings, small talk, filler phrases.
Be specific with numbers, product names, and prices.

Conversation:
{conversation}

Write the summary in past tense, third person ("The customer..."):',
    'conversation',
    'Generates semantic summary for Mem0 storage after conversation ends.'
) ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- Add purchase_history memory type to memory_strategy
INSERT INTO memory_strategy (tenant_id, intent, workflow, memory_types, max_results, priority, description)
VALUES
    ('tenant_inventaa_led_001', 'WORKFLOW_ACTION', 'BROWSING', 
     'product_context,customer_preference,purchase_summary,conversation_summary', 3, 1,
     'Browsing: enrich with purchase history and preferences'),
    ('tenant_inventaa_led_001', 'GREETING', '*',
     'customer_preference,purchase_summary,conversation_summary', 3, 1,
     'Greeting: personalize with purchase history and preferences')
ON CONFLICT DO NOTHING;

-- Verify
SELECT prompt_name, LEFT(prompt_text, 60) AS preview
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'conversation_summary_prompt';






  -- Run this in Supabase to see which prompts are missing
-- Every row showing NULL needs to be populated by running 014_migrate_hardcoded_prompts.sql

SELECT
    key.prompt_name,
    CASE WHEN pt.prompt_text IS NOT NULL THEN '✅ EXISTS' ELSE '❌ MISSING' END AS status,
    LEFT(pt.prompt_text, 60) AS preview
FROM (VALUES
    ('intent_system_prompt'),
    ('greeting_system_prompt'),
    ('entity_system_prompt'),
    ('escalation_prompt'),
    ('unknown_prompt'),
    ('invoice_inquiry_check_prompt'),
    ('order_confirmation_reply_check_prompt'),
    ('generate_invoice_cta_prompt'),
    ('invoice_confirmation_request_check_prompt'),
    ('fast_order_confirm_check_prompt'),
    ('category_matcher_prompt'),
    ('neg_is_request_prompt'),
    ('neg_extract_qty_prompt'),
    ('neg_detect_qty_change_prompt'),
    ('neg_detect_counter_prompt'),
    ('neg_more_discount_prompt'),
    ('neg_detect_accept_prompt'),
    ('neg_no_discount_prompt'),
    ('neg_first_offer_prompt'),
    ('neg_counter_offer_prompt'),
    ('neg_final_price_prompt'),
    ('neg_extract_negotiation_intent_prompt'),
    ('fast_confirm_prompt'),
    ('product_summary_recommendation_prompt'),
    ('parse_global_offer_tiers_prompt'),
    ('pf_data_extraction_prompt'),
    ('pf_history_resolver_prompt'),
    ('pf_offer_inquiry_check_prompt'),
    ('pf_offer_inquiry_check_l2_prompt'),
    ('pf_vague_reference_check_prompt'),
    ('pf_vague_reference_rewriter_prompt'),
    ('pf_neg_product_change_check_prompt'),
    ('pf_named_product_extractor_prompt'),
    ('pf_offers_formatter_prompt'),
    ('pf_vague_pronoun_resolver_l2_prompt'),
    ('pf_new_search_followup_classifier_prompt'),
    ('pf_comparison_prompt'),
    ('pf_image_installation_intent_prompt'),
    ('pf_main_followup_prompt'),
    ('conversation_summary_prompt')
) AS key(prompt_name)
LEFT JOIN prompt_templates pt
    ON pt.prompt_name = key.prompt_name
    AND pt.tenant_id  = 'tenant_inventaa_led_001'
    AND pt.language   = 'en'
    AND pt.status     = 'active'
ORDER BY status, key.prompt_name;





-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 016: Consolidated follow-up classifier + Mem0 worthiness prompt
--
-- WHY:
--   product_followup.py previously made ~5 sequential LLM calls per message:
--     1. pf_offer_inquiry_check_prompt   (standalone offer-inquiry pre-check)
--     2. pf_data_extraction_prompt       (main parse: product, quantity, etc.)
--     3. neg_is_request_prompt           (is this a negotiation ask?)
--     4. pf_offer_inquiry_check_l2_prompt (second offer-inquiry pass)
--     5. pf_main_followup_prompt         (the actual answer)
--
--   Calls 1, 3, and 4 are now folded into call 2's schema — one LLM call
--   determines product/quantity/comparison/offer-inquiry/negotiation-intent/
--   memory-worthiness all at once, the same "Phase 2" consolidation pattern
--   already used by neg_extract_negotiation_intent_prompt for ongoing
--   negotiation turns.
--
--   This is fully backward compatible: the application code checks for the
--   two new fields (is_negotiation_request, needs_long_term_memory) and
--   only falls back to separate LLM calls when they're missing (None) —
--   i.e. for any tenant who hasn't run this migration yet. Nothing breaks
--   for tenants on the old prompt; they just don't get the latency win
--   until they're updated.
--
--   NOTE ON MULTI-TENANCY: this migration is written for
--   tenant_inventaa_led_001 as a concrete example, matching the pattern of
--   every prior migration in this codebase. Run the equivalent INSERT/
--   UPDATE for each tenant, or adapt into a loop over all active tenants.
-- ══════════════════════════════════════════════════════════════════════════════

-- ── Updated pf_data_extraction_prompt — adds is_negotiation_request and
--    needs_long_term_memory to the existing schema ───────────────────────────

UPDATE prompt_templates
SET
    prompt_text = 'You are a data extraction assistant for {biz_name}.
Extract structured information from the customer message about product selection,
ordering intent, negotiation intent, and whether this message needs long-term
customer memory.

PRODUCT CATALOG (reference only):
{product_catalog}

Extract and return JSON with these fields:
- selected_product_name: exact product name if customer selected one, else null
- parsed_order_quantity: integer if customer mentioned a quantity, else null
- is_comparison: true if customer wants to compare products
- is_offer_inquiry: true if customer is asking about GENERAL store offers/discounts/tiers
  (e.g. "any offers?", "what deals do you have?") — NOT a specific price they are proposing.
- is_installation_inquiry: true if customer wants installation guide or images
- is_negotiation_request: true if the customer is proposing a SPECIFIC price,
  asking for a discount on a specific product, or otherwise trying to negotiate
  price (e.g. "can I get this for 900", "my budget is 1500", "any discount?",
  "can we settle at X"). A specific price offer is ALWAYS is_negotiation_request=true
  and is_offer_inquiry=false, even if it also mentions the word "offer" — a customer
  naming their own price is never asking about your general store offers.
- needs_long_term_memory: true ONLY if the customer is asking something that can
  only be answered using information from a PAST conversation or PAST order —
  e.g. "recommend something for me", "what did I buy last time", "do you remember
  my last order", "same as before", "I usually prefer X". False for anything
  answerable from the current conversation and the product catalog above
  (product questions, quantity changes, comparisons, installation guides,
  confirmations, and negotiation) — those should always be false.

Reply ONLY with valid JSON. No explanation.',
    variables = 'biz_name,product_catalog',
    version = version + 1,
    updated_at = NOW()
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'pf_data_extraction_prompt'
  AND language = 'en'
  AND status = 'active';

-- If the UPDATE above matched 0 rows (no active row exists yet), insert one.
INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
SELECT
    'tenant_inventaa_led_001', 'pf_data_extraction_prompt', 'en', 1, 'active',
    'You are a data extraction assistant for {biz_name}.
Extract structured information from the customer message about product selection,
ordering intent, negotiation intent, and whether this message needs long-term
customer memory.

PRODUCT CATALOG (reference only):
{product_catalog}

Extract and return JSON with these fields:
- selected_product_name: exact product name if customer selected one, else null
- parsed_order_quantity: integer if customer mentioned a quantity, else null
- is_comparison: true if customer wants to compare products
- is_offer_inquiry: true if customer is asking about GENERAL store offers/discounts/tiers
  (e.g. "any offers?", "what deals do you have?") — NOT a specific price they are proposing.
- is_installation_inquiry: true if customer wants installation guide or images
- is_negotiation_request: true if the customer is proposing a SPECIFIC price,
  asking for a discount on a specific product, or otherwise trying to negotiate
  price (e.g. "can I get this for 900", "my budget is 1500", "any discount?",
  "can we settle at X"). A specific price offer is ALWAYS is_negotiation_request=true
  and is_offer_inquiry=false, even if it also mentions the word "offer" — a customer
  naming their own price is never asking about your general store offers.
- needs_long_term_memory: true ONLY if the customer is asking something that can
  only be answered using information from a PAST conversation or PAST order —
  e.g. "recommend something for me", "what did I buy last time", "do you remember
  my last order", "same as before", "I usually prefer X". False for anything
  answerable from the current conversation and the product catalog above
  (product questions, quantity changes, comparisons, installation guides,
  confirmations, and negotiation) — those should always be false.

Reply ONLY with valid JSON. No explanation.',
    'biz_name,product_catalog',
    'Consolidated follow-up classifier — folds in negotiation-request and Mem0-worthiness detection (migration 016).'
WHERE NOT EXISTS (
    SELECT 1 FROM prompt_templates
    WHERE tenant_id = 'tenant_inventaa_led_001' AND prompt_name = 'pf_data_extraction_prompt'
      AND language = 'en' AND status = 'active'
);

-- ── New: mem0_worthy_check_prompt — used ONLY as MemoryPolicy's fallback
--    (ai/memory_policy.py LLMDetector) for tenants who haven't run this
--    migration yet, i.e. whose pf_data_extraction_prompt doesn't return
--    needs_long_term_memory. Once a tenant's prompt is updated above, this
--    fallback is never invoked for them. ─────────────────────────────────────

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES (
    'tenant_inventaa_led_001',
    'mem0_worthy_check_prompt',
    'en', 1, 'active',
    'Does answering this customer message require recalling information from
a PAST conversation or PAST order (not the current conversation)?

Examples that are YES:
- "recommend something for me"
- "what did I buy last time?"
- "do you remember my last order?"
- "same as before"
- "I usually prefer warm white lights"

Examples that are NO (answerable from the current conversation alone):
- Any product question, comparison, quantity change, installation question,
  price negotiation, or order confirmation

Reply ONLY "YES" or "NO".',
    NULL,
    'Fallback Mem0-worthiness classifier for MemoryPolicy.LLMDetector — only used when pf_data_extraction_prompt does not yet return needs_long_term_memory.'
) ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, version, LEFT(prompt_text, 80) AS preview
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name IN ('pf_data_extraction_prompt', 'mem0_worthy_check_prompt')
  AND status = 'active'
ORDER BY prompt_name;








-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 017: SET vs ADD vs REMOVE quantity semantics
--
-- BUG FIXED:
--   "add 10 more units" with current quantity=2 was becoming quantity=10
--   instead of quantity=12. Root cause: both quantity-change prompts asked
--   the LLM to compute and return the FINAL total quantity directly. LLM
--   arithmetic on this is not reliably correct — the same phrasing pattern
--   sometimes summed correctly and sometimes didn't (confirmed in
--   production logs: "add 4 more units" at qty=2 correctly became qty=6 in
--   one conversation, but "add 10 more units" at qty=2 became qty=10, not
--   12, in another).
--
-- FIX:
--   Both prompts now return the RAW number the customer mentioned plus an
--   explicit operation (SET/ADD/REMOVE) — the arithmetic (current_qty + N,
--   current_qty - N, or just N) now happens in Python, deterministically,
--   in ai/negotiator.py. This removes LLM arithmetic reliability from the
--   equation entirely; the LLM's only job is classification (which it's
--   good at), not computation (which it isn't reliable at).
--
-- BACKWARD COMPATIBLE:
--   The application code checks for the new JSON/operation schema and
--   falls back to the old plain-integer schema automatically per tenant —
--   nothing breaks for a tenant who hasn't run this migration yet; they
--   just keep their old (occasionally-wrong) behavior until they do.
--
-- MULTI-TENANT NOTE:
--   Written for tenant_inventaa_led_001 as a concrete example, matching
--   every prior migration in this codebase. Run the equivalent UPDATE for
--   each tenant, or adapt into a loop over all active tenants.
-- ══════════════════════════════════════════════════════════════════════════════

-- ── neg_detect_qty_change_prompt (Phase 1 fallback path) ──────────────────────

UPDATE prompt_templates
SET
    prompt_text = 'Customer currently has {current_qty} units of {product_name} in their order.

Determine if they are changing the QUANTITY (number of units), and if so, HOW.

Reply ONLY with a JSON object in this exact schema:
{"operation": "SET" | "ADD" | "REMOVE" | "NONE", "value": <integer or null>}

"value" is the RAW NUMBER the customer mentioned — do NOT compute a new
total yourself. The application will do that arithmetic.

CRITICAL — reply {"operation": "NONE", "value": null} for these cases
(these are PRICE negotiations, not quantity changes):
- "can i get it for 2150" → price offer → NONE
- "can we move with 2150" → price offer → NONE
- "how about 2000" → price counter-offer → NONE
- "my budget is 1800" → price statement → NONE
- "final price 2200" → price → NONE
- Any message asking for a specific Rs. price per unit → NONE

OPERATION RULES:
- "add 3 more units", "add another 5", "increase by 3", "give me 4 more"
  → {"operation": "ADD", "value": <the number mentioned>}
- "remove 2 units", "reduce by 2", "take away 3", "decrease by 2"
  → {"operation": "REMOVE", "value": <the number mentioned>}
- "change to 5 units", "make it 10", "update to 8", "I want 8 units total",
  "set quantity to 6" (an ABSOLUTE final quantity, not a relative change)
  → {"operation": "SET", "value": <the number mentioned>}

EXAMPLES:
"add 3 more units" (current_qty=2) → {"operation":"ADD","value":3}
"add 10 more units" (current_qty=2) → {"operation":"ADD","value":10}
"remove 2 units" (current_qty=5) → {"operation":"REMOVE","value":2}
"change to 5 units" → {"operation":"SET","value":5}
"make it 10" → {"operation":"SET","value":10}
"can i get it for 2150" → {"operation":"NONE","value":null}

Reply ONLY with the JSON object.',
    variables = 'current_qty,product_name',
    version = version + 1,
    updated_at = NOW()
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'neg_detect_qty_change_prompt'
  AND language = 'en'
  AND status = 'active';

-- ── neg_extract_negotiation_intent_prompt (Phase 2 fast path) ─────────────────
-- Adds "operation" alongside the existing quantity_change.value field.

UPDATE prompt_templates
SET
    prompt_text = 'You analyze a customer message during a price negotiation.

Product: {product_name}
Current offer: Rs.{current_price}/unit for {current_qty} units.

Return ONLY a JSON object in this exact schema:
{
  "intent": "ACCEPTED" | "QTY_CHANGE" | "COUNTER_OFFER" | "MORE_DISCOUNT" | "NONE",
  "matched_phrase": "the exact phrase that triggered your classification",
  "accepted":        {"value": true/false,   "confidence": 0.0-1.0},
  "quantity_change": {
    "value": N or null,
    "operation": "SET" | "ADD" | "REMOVE" | null,
    "confidence": 0.0-1.0
  },
  "counter_offer":   {"value": N or null,     "confidence": 0.0-1.0},
  "more_discount":   {"value": true/false,    "confidence": 0.0-1.0}
}

CRITICAL — quantity_change.value is the RAW NUMBER the customer mentioned.
Do NOT compute a new total yourself (e.g. do not add it to the current
quantity) — the application does that arithmetic using the operation field.

OPERATION RULES for quantity_change:
- "add N more units", "add another N", "increase by N", "give me N more"
  → operation="ADD", value=N (the number mentioned, NOT current_qty+N)
- "remove N units", "reduce by N", "take away N"
  → operation="REMOVE", value=N
- "change to N units", "make it N", "update to N", "I want N units total"
  (an ABSOLUTE final quantity, not phrased as relative to the current one)
  → operation="SET", value=N

EXAMPLES:
Message: "Proceed"
→ {"intent":"ACCEPTED","matched_phrase":"Proceed","accepted":{"value":true,"confidence":0.99},"quantity_change":{"value":null,"operation":null,"confidence":0.99},"counter_offer":{"value":null,"confidence":0.99},"more_discount":{"value":false,"confidence":0.99}}

Message: "Make it 6 units" (current_qty=2)
→ {"intent":"QTY_CHANGE","matched_phrase":"Make it 6 units","accepted":{"value":false,"confidence":0.97},"quantity_change":{"value":6,"operation":"SET","confidence":0.97},"counter_offer":{"value":null,"confidence":0.97},"more_discount":{"value":false,"confidence":0.97}}

Message: "add 10 more units" (current_qty=2)
→ {"intent":"QTY_CHANGE","matched_phrase":"add 10 more units","accepted":{"value":false,"confidence":0.97},"quantity_change":{"value":10,"operation":"ADD","confidence":0.97},"counter_offer":{"value":null,"confidence":0.97},"more_discount":{"value":false,"confidence":0.97}}
(Note: value is 10 — the number the customer said — NOT 12. The application computes current_qty + value = 12 itself.)

Message: "remove 2 units" (current_qty=5)
→ {"intent":"QTY_CHANGE","matched_phrase":"remove 2 units","accepted":{"value":false,"confidence":0.96},"quantity_change":{"value":2,"operation":"REMOVE","confidence":0.96},"counter_offer":{"value":null,"confidence":0.96},"more_discount":{"value":false,"confidence":0.96}}

Message: "1800 is my budget"
→ {"intent":"COUNTER_OFFER","matched_phrase":"1800 is my budget","accepted":{"value":false,"confidence":0.98},"quantity_change":{"value":null,"operation":null,"confidence":0.98},"counter_offer":{"value":1800,"confidence":0.98},"more_discount":{"value":false,"confidence":0.98}}

Message: "Can you do any better?"
→ {"intent":"MORE_DISCOUNT","matched_phrase":"Can you do any better","accepted":{"value":false,"confidence":0.95},"quantity_change":{"value":null,"operation":null,"confidence":0.95},"counter_offer":{"value":null,"confidence":0.95},"more_discount":{"value":true,"confidence":0.95}}

Message: "Proceed with 6 units" (current_qty=2)
→ {"intent":"ACCEPTED","matched_phrase":"Proceed with 6 units","accepted":{"value":true,"confidence":0.91},"quantity_change":{"value":6,"operation":"SET","confidence":0.85},"counter_offer":{"value":null,"confidence":0.97},"more_discount":{"value":false,"confidence":0.97}}

Note: "Proceed with 6 units" → intent=ACCEPTED because acceptance takes precedence.
The caller enforces: ACCEPTED > QTY_CHANGE > COUNTER_OFFER > MORE_DISCOUNT.

Set confidence to reflect how certain you are. If ambiguous, set confidence below 0.75.
Reply ONLY with the JSON object.',
    version = version + 1,
    status  = 'active',
    variables = 'product_name,current_price,current_qty',
    updated_at = NOW()
WHERE tenant_id    = 'tenant_inventaa_led_001'
  AND prompt_name  = 'neg_extract_negotiation_intent_prompt'
  AND language     = 'en'
  AND status       = 'active';

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, version, LEFT(prompt_text, 100) AS preview
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name IN ('neg_detect_qty_change_prompt', 'neg_extract_negotiation_intent_prompt')
  AND status = 'active'
ORDER BY prompt_name;





-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION: Add negotiation config columns to tenants
--
-- WHY:
--   pipeline/setup.py::_apply_tenant() and models/schemas.py::IncomingMessage
--   already read/declare these three fields (max_negotiation_rounds,
--   neg_floor_disc_pct, neg_floor_multiplier), but the columns were never
--   added to the tenants table — confirmed via:
--     ERROR: 42703: column "max_negotiation_rounds" does not exist
--   Until this runs, info.get(...) on these keys always returns None
--   regardless of what setup.py's fallback logic does, since select("*")
--   simply won't include a column that doesn't exist.
--
-- SEMANTICS (for whoever wires these into negotiator.py next):
--   max_negotiation_rounds — integer. 0 is a valid, meaningful value
--                            ("no negotiation allowed for this tenant"),
--                            distinct from NULL ("not configured, use
--                            whatever code-level default negotiator.py has").
--   neg_floor_disc_pct     — integer percent. 0 is valid ("no floor
--                            discount — never go below list price"),
--                            distinct from NULL ("not configured").
--   neg_floor_multiplier   — float. Same NULL-vs-0 distinction applies.
--
--   setup.py's _apply_tenant() already checks `is not None` (not truthiness)
--   when reading these, specifically so a tenant-configured 0 survives as 0
--   instead of being coerced to NULL. Any code in negotiator.py that reads
--   these fields later must preserve that same is-not-None discipline —
--   `if incoming.max_negotiation_rounds:` would reintroduce the same bug
--   this migration's column-level NULL default was designed to avoid.
-- ══════════════════════════════════════════════════════════════════════════════

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS max_negotiation_rounds INTEGER DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_floor_disc_pct     INTEGER DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS neg_floor_multiplier    NUMERIC DEFAULT NULL;

-- ── Verify columns exist (should return 3 rows) ───────────────────────────────
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'tenants'
  AND column_name IN ('max_negotiation_rounds', 'neg_floor_disc_pct', 'neg_floor_multiplier');

-- ── Confirm no tenant accidentally has 0 vs NULL confusion pre-existing ───────
-- (Should return 0 rows right after this migration, since the columns are new
--  and default to NULL — this becomes meaningful once tenants start setting
--  real values.)
SELECT tenant_id, max_negotiation_rounds, neg_floor_disc_pct, neg_floor_multiplier
FROM tenants
WHERE max_negotiation_rounds = 0 OR neg_floor_disc_pct = 0 OR neg_floor_multiplier = 0;


SELECT tenant_id, max_negotiation_rounds, neg_floor_disc_pct, neg_floor_multiplier
FROM tenants
WHERE max_negotiation_rounds = 0 OR neg_floor_disc_pct = 0 OR neg_floor_multiplier = 0;



-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 020: Fix "product number" vs "quantity" confusion in pf_main_followup_prompt
--
-- BUG (verified against production logs, not just symptom-observed):
--   Customer said "i want to order 11" (11 = a product LIST INDEX, correctly
--   resolved by the NUMBER-SELECT resolver to a product name BEFORE the
--   unified parser ever ran — confirmed: parsed_order_quantity/quantity was
--   None in the structured log output for that turn AND every turn after).
--   Several turns later, after "is it waterproof", the bot's free-text reply
--   said "Would you like to proceed with your order for 11 units?" — as if
--   11 were a quantity.
--
-- ROOT CAUSE — NOT a Python state bug:
--   Checked the actual structured state at the exact turn that produced the
--   bad reply: quantity/parsed_order_quantity was None, exactly as it should
--   be. No field anywhere (negotiation_state, product_context,
--   workflow_context) ever held 11 as a quantity. The bug is in the LLM
--   call itself: pf_main_followup_prompt receives session_history, which
--   includes the RAW customer message "i want to order 11" (saved to the
--   messages table BEFORE the NUMBER-SELECT rewrite happens — that rewrite
--   only exists in memory on incoming.text for the current turn, never
--   written back to the DB row already saved). Several turns later, with
--   "11" still sitting in the recent conversation history the LLM sees,
--   and no explicit instruction telling it NOT to treat that as a
--   quantity, it free-associated the two.
--
-- FIX: instructs the LLM explicitly to ignore bare numbers from past
-- conversation turns as quantity signals — quantity comes ONLY from the
-- workflow_context variable given in the CURRENT call. Paired with a code
-- fix (product_followup.py) that also stops embedding a literal "qty=None"
-- into that same variable, which was adding avoidable ambiguity.
-- ══════════════════════════════════════════════════════════════════════════════

UPDATE prompt_templates
SET
    prompt_text = 'You are a helpful WhatsApp sales assistant for {biz_name}.
You are helping customer {sender_name} with their product inquiry.

ACTIVE PRODUCT CONTEXT:
{product_context}

CUSTOMER MEMORY:
{customer_preferences}

SESSION WORKFLOW (this is your ONLY source of truth for quantity):
{workflow_context}

CURRENT CATALOG DATA:
{catalog_data}

INTENT CLASSIFICATION:
{parsed_intent}

CRITICAL — QUANTITY vs PRODUCT NUMBER:
The conversation history below may contain earlier messages where the
customer replied with a bare number (e.g. "11", "order 3") to pick a
product from a NUMBERED LIST — that number was a product selection, not
a quantity, and has already been resolved to the product named above.
NEVER treat any number appearing in past conversation turns as an
implied quantity. The ONLY place quantity information exists is the
SESSION WORKFLOW field above — if it says "quantity not yet specified",
you do not know the quantity yet, regardless of what numbers appear
earlier in the conversation. When asking the customer for a quantity,
never reference a number from history as if it were their order size.

INSTRUCTIONS BY INTENT:

INTENT A1 — Customer ordering (quantity specified):
→ Generate order summary with product, quantity, unit price, store discount, subtotal, GST, total payable
→ Show upsell tier hint if next tier exists
→ End with "Please confirm and we''ll process your order! 🎉"

INTENT A2 — Customer ordering (no quantity yet):
→ Ask clearly: "How many units of {product_name} would you like?"
→ Do NOT suggest, guess, or reference any specific number as their quantity.

INTENT B — Product question / FAQ:
→ Answer from product context above
→ Be concise, warm, use *bold* for key specs
→ If installation guide requested, mention it is available
→ Do NOT append an order-progress question referencing a quantity unless
  SESSION WORKFLOW explicitly shows one already set.

INTENT C — Comparison request:
→ Route to comparison handler (do not answer here)

RULES:
- Address as {sender_name}
- Use *bold* for prices and product names
- Never hallucinate prices — use only the data provided
- Max 12 lines
- WhatsApp formatting only (no markdown tables)',
    variables = 'biz_name,sender_name,product_context,customer_preferences,workflow_context,catalog_data,parsed_intent,product_name',
    version = version + 1,
    updated_at = NOW()
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'pf_main_followup_prompt'
  AND language = 'en'
  AND status = 'active';

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, version, LEFT(prompt_text, 120) AS preview
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'pf_main_followup_prompt'
  AND status = 'active';



  -- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 021: Order summary → DB-driven prompts
--
-- Converts pipeline/router.py::_build_order_summary() from a fully hardcoded
-- Python function into 5 DB-configurable prompts. The conditional STRUCTURE
-- (which of the 3 scenarios applies, whether to append a savings line)
-- stays in Python — that's a data-availability decision, not wording. Only
-- the actual message text is now tenant/language-configurable.
--
-- Text below is IDENTICAL to the previous hardcoded strings, so behavior is
-- unchanged until a tenant edits these rows.
-- ══════════════════════════════════════════════════════════════════════════════

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
(
    'tenant_inventaa_led_001', 'order_summary_full_discount_prompt', 'en', 1, 'active',
    'Here''s your updated order summary, {sender_name}! 🎉

• *Product:* {product}
• *Quantity:* {qty} units
• *Regular price:* Rs.{price_raw}/unit
• *Store offer {auto_pct}% OFF:* Rs.{auto_unit}/unit
• *Negotiated price:* Rs.{agreed_price}/unit
• *Subtotal:* Rs.{sub}
• *GST ({gst_pct}%):* Rs.{gst}
• *Total Payable:* Rs.{tot}',
    'sender_name,product,qty,price_raw,auto_pct,auto_unit,agreed_price,sub,gst_pct,gst,tot',
    'Order summary when both a store discount AND a further negotiated reduction apply.'
),
(
    'tenant_inventaa_led_001', 'order_summary_store_discount_only_prompt', 'en', 1, 'active',
    'Here''s your updated order summary, {sender_name}! 🎉

• *Product:* {product}
• *Quantity:* {qty} units
• *Regular price:* Rs.{price_raw}/unit
• *Store offer {auto_pct}% OFF:* Rs.{auto_unit}/unit
• *Subtotal:* Rs.{sub}
• *GST ({gst_pct}%):* Rs.{gst}
• *Total Payable:* Rs.{tot}',
    'sender_name,product,qty,price_raw,auto_pct,auto_unit,sub,gst_pct,gst,tot',
    'Order summary when only a store discount applies (no further negotiation reduction).'
),
(
    'tenant_inventaa_led_001', 'order_summary_plain_price_prompt', 'en', 1, 'active',
    'Here''s your updated order summary, {sender_name}! 🎉

• *Product:* {product}
• *Quantity:* {qty} units
• *Price per unit:* Rs.{agreed_price}
• *Subtotal:* Rs.{sub}
• *GST ({gst_pct}%):* Rs.{gst}
• *Total Payable:* Rs.{tot}',
    'sender_name,product,qty,agreed_price,sub,gst_pct,gst,tot',
    'Order summary when no discount breakdown applies.'
),
(
    'tenant_inventaa_led_001', 'order_summary_savings_line_prompt', 'en', 1, 'active',
    '🎁 *You save Rs.{tot_save} on this order!*',
    'tot_save',
    'Appended to any order summary variant when total savings > 0.'
),
(
    'tenant_inventaa_led_001', 'order_summary_footer_prompt', 'en', 1, 'active',
    'Reply *Confirm* to place your order and receive your invoice! 🎉',
    NULL,
    'Appended to every order summary variant.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, LEFT(prompt_text, 60) AS preview, variables
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name LIKE 'order_summary_%'
ORDER BY prompt_name;



-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 023: Fix intent_system_prompt HUMAN_ESCALATION gap
--
-- BUG (found by regression_test.py, scenario 14):
--   "I want to speak to a real human agent, this is urgent" was classified
--   as WORKFLOW_ACTION instead of HUMAN_ESCALATION.
--
-- ROOT CAUSE:
--   Every existing HUMAN_ESCALATION example is framed around a complaint
--   ("damaged", "refund", "nobody is responding") — none cover a neutral
--   "wants to talk to a human" request with no complaint context. Meanwhile
--   the WORKFLOW_ACTION examples are dominated by the sentence pattern
--   "I want to [verb]..." ("I want to order...", "I want to buy this",
--   "I want to reorder") — and "I want to speak to..." matches that pattern
--   structurally, pulling the classification the wrong way.
--
-- FIX: adds two escalation examples covering the neutral "wants a human"
-- case, using the same "I want to..." sentence structure so the model has
-- a directly comparable escalation example to weigh against the
-- WORKFLOW_ACTION ones. Nothing else in the prompt changes — same
-- categories, same rules, same confidence threshold, same output format.
-- ══════════════════════════════════════════════════════════════════════════════

UPDATE prompt_templates
SET
    prompt_text = 'You are an intent classification AI for Inventaa LED Lights — an Indian LED lighting manufacturer based in Chennai.
Classify the customer message into EXACTLY ONE intent:
WORKFLOW_ACTION — Customer wants to DO something:
  place order, track order, check status, request invoice, confirm payment,
  enquire about specific product by name/SKU/number, say "I want to buy".
  Examples:
  → "I want to order 5 flood lights"
  → "I want to order mini elena 10w outdoor gate light"
  → "send me invoice"
  → "where is my order INV#A3F21"
  → "I want to buy this"
  → "order 2 of these"
  → "I want to order product number 3"
  → "confirm my order"
  → "I want to reorder"
FAQ_KNOWLEDGE — Customer is browsing/researching, NOT ready to buy:
  asking about products, pricing, features, availability, comparisons, categories.
  Examples:
  → "what outdoor lights do you have?"
  → "show me gate lights"
  → "tell me about Reva LED"
  → "what is the price of 9W bulb?"
  → "compare Aeris and Villa gate light"
  → "what wattage options are available?"
  → "any offers or discounts?"
  → "is the Zenia SKY waterproof?"
HUMAN_ESCALATION — Upset, complaint, refund, wants human/manager — including a
  plain, neutral request to speak with a human agent even with no complaint
  stated (do not require complaint framing; wanting a human is enough on its own):
  Examples:
  → "I want to talk to a manager"
  → "my product arrived damaged"
  → "I need a refund"
  → "nobody is responding to my complaint"
  → "I want to speak to a real human agent"
  → "connect me to customer support"
GREETING — Greeting, thanks, acknowledgement, goodbye:
  Examples:
  → "Hi", "Hello", "Good morning", "Thank you", "Ok", "Noted", "Bye", "👍"
UNKNOWN — Gibberish, completely unrelated, makes no sense.
RULES:
1. Reply ONLY with valid JSON. No explanation, no markdown.
2. Keys: "intent" and "confidence_score" (float 0.0-1.0).
3. confidence < 0.50 → set intent to "UNKNOWN".
4. Never invent a new intent name.
5. Customer mentions product + quantity → WORKFLOW_ACTION.
6. Customer says "I want to order/buy" → WORKFLOW_ACTION even without quantity.
7. Customer says "I want to speak to/talk to a human/agent/person/manager/support"
   → HUMAN_ESCALATION, even without a stated complaint and even though it
   also starts with "I want to" like the WORKFLOW_ACTION examples — the
   verb (speak/talk to a human) decides this, not the sentence opener.
Output ONLY: {"intent": "WORKFLOW_ACTION", "confidence_score": 0.97}',
    version = version + 1,
    updated_at = NOW()
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'intent_system_prompt'
  AND language = 'en'
  AND status = 'active';

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, version, updated_at
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'intent_system_prompt'
  AND status = 'active';





  -- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 023: Remaining hardcoded strings (H13-H17) + config hardcodes (C7,C8,C12)
-- ══════════════════════════════════════════════════════════════════════════════

-- ── New tenant config columns ──────────────────────────────────────────────────
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS intent_min_confidence NUMERIC DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS max_image_products     INT     DEFAULT NULL;

UPDATE tenants SET
    intent_min_confidence = 0.50,
    max_image_products    = 3
WHERE tenant_id = 'tenant_inventaa_led_001';

-- ── New DB prompts (H13-H17) ──────────────────────────────────────────────────

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
(
    'tenant_inventaa_led_001', 'neg_upsell_hint_prompt', 'en', 1, 'active',
    '

💡 Add Rs.{value_gap} more to your order value (approx. {units_needed} more unit(s)) to reach Rs.{next_min_val} and unlock *{next_disc_pct}% off*!',
    'value_gap,units_needed,next_min_val,next_disc_pct',
    'Upsell hint shown when customer is close to the next discount tier. Was duplicated as a hardcoded f-string in 4 places.'
),
(
    'tenant_inventaa_led_001', 'neg_max_discount_unlocked_prompt', 'en', 1, 'active',
    '

🎉 You''ve {qualifier}unlocked our *maximum store discount of {max_disc}% OFF*!',
    'max_disc,qualifier',
    'Shown when customer reaches the top discount tier. qualifier is "just " or "" depending on call site.'
),
(
    'tenant_inventaa_led_001', 'neg_already_confirmed_prompt', 'en', 1, 'active',
    'You''ve already confirmed Rs.{old_agreed}/unit, {sender_name}. Please reply *Confirm* to place your order! 🎉',
    'old_agreed,sender_name',
    'Shown when customer re-triggers a summary for a price they already accepted.'
),
(
    'tenant_inventaa_led_001', 'pf_invalid_number_prompt', 'en', 1, 'active',
    'Hi {sender_name}! That number isn''t in the list (1-{list_size}). Could you please reply with the *product name* instead? 😊',
    'sender_name,list_size',
    'Shown when a customer picks a number outside the range of the shown product list.'
),
(
    'tenant_inventaa_led_001', 'pf_no_product_name_prompt', 'en', 1, 'active',
    'Hi {sender_name}! Could you please reply with the *product name* you''d like to know more about or order? 😊',
    'sender_name',
    'Shown when a bare number is ambiguous — no product list shown, no quantity expected.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, LEFT(prompt_text, 60) AS preview, variables
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name IN (
    'neg_upsell_hint_prompt', 'neg_max_discount_unlocked_prompt',
    'neg_already_confirmed_prompt', 'pf_invalid_number_prompt', 'pf_no_product_name_prompt'
  )
ORDER BY prompt_name;

SELECT tenant_id, intent_min_confidence, max_image_products
FROM tenants WHERE tenant_id = 'tenant_inventaa_led_001';




-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 024: pricing.py::to_whatsapp_summary() → DB prompts
-- The last remaining Category-2 item — text is identical to the previous
-- hardcoded strings, so behavior is unchanged until a tenant edits these rows.
-- ══════════════════════════════════════════════════════════════════════════════

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
(
    'tenant_inventaa_led_001', 'pricing_order_summary_full_discount_prompt', 'en', 1, 'active',
    'Here''s your order summary, {sender_name}! Please review:

• *Product:* {product_name}
• *Quantity:* {quantity} units
• *Regular price:* Rs.{regular_unit_price}/unit
• *Store offer {store_disc_pct}% OFF:* Rs.{store_unit_price}/unit
• *Negotiated price:* Rs.{negotiated_unit_price}/unit
• *Subtotal:* Rs.{subtotal}
• *GST ({gst_pct}%):* Rs.{gst_amount}
• *Total Payable:* Rs.{total_payable}',
    'sender_name,product_name,quantity,regular_unit_price,store_disc_pct,store_unit_price,negotiated_unit_price,subtotal,gst_pct,gst_amount,total_payable',
    'pricing.py pre-confirm summary — negotiated price scenario.'
),
(
    'tenant_inventaa_led_001', 'pricing_order_summary_store_discount_only_prompt', 'en', 1, 'active',
    'Here''s your order summary, {sender_name}! Please review:

• *Product:* {product_name}
• *Quantity:* {quantity} units
• *Regular price:* Rs.{regular_unit_price}/unit
• *Store offer {store_disc_pct}% OFF:* Rs.{negotiated_unit_price}/unit
• *Subtotal:* Rs.{subtotal}
• *GST ({gst_pct}%):* Rs.{gst_amount}
• *Total Payable:* Rs.{total_payable}',
    'sender_name,product_name,quantity,regular_unit_price,store_disc_pct,negotiated_unit_price,subtotal,gst_pct,gst_amount,total_payable',
    'pricing.py pre-confirm summary — store discount only, no negotiation.'
),
(
    'tenant_inventaa_led_001', 'pricing_order_summary_plain_price_prompt', 'en', 1, 'active',
    'Here''s your order summary, {sender_name}! Please review:

• *Product:* {product_name}
• *Quantity:* {quantity} units
• *Price per unit:* Rs.{negotiated_unit_price}
• *Subtotal:* Rs.{subtotal}
• *GST ({gst_pct}%):* Rs.{gst_amount}
• *Total Payable:* Rs.{total_payable}',
    'sender_name,product_name,quantity,negotiated_unit_price,subtotal,gst_pct,gst_amount,total_payable',
    'pricing.py pre-confirm summary — no discount at all.'
),
(
    'tenant_inventaa_led_001', 'pricing_order_summary_savings_breakdown_prompt', 'en', 1, 'active',
    '🎁 *Total savings: Rs.{total_discount_amount}*
   • Store offer: Rs.{store_discount_amount}
   • Negotiation: Rs.{negotiation_discount_amount}',
    'total_discount_amount,store_discount_amount,negotiation_discount_amount',
    'Shown when both a store discount and a negotiation discount contributed to savings.'
),
(
    'tenant_inventaa_led_001', 'pricing_order_summary_savings_prompt', 'en', 1, 'active',
    '🎁 *You save Rs.{total_discount_amount} on this order!*',
    'total_discount_amount',
    'Shown when only one type of discount contributed to savings.'
),
(
    'tenant_inventaa_led_001', 'pricing_order_summary_footer_prompt', 'en', 1, 'active',
    'Reply *Confirm* to place your order and receive your invoice! 🎉',
    NULL,
    'Footer for pricing.py''s pre-confirm summary.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, LEFT(prompt_text, 60) AS preview, variables
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name LIKE 'pricing_order_summary_%'
ORDER BY prompt_name;




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




  -- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 004 — handlers, graphrag_handler, invoice_handler, negotiator dynamic settings
-- ══════════════════════════════════════════════════════════════════════════════

-- ── STEP 1: Add new configuration columns to tenants (safe — IF NOT EXISTS) ───
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS neg_min_units INTEGER DEFAULT 5;

-- ── STEP 2: Update configuration for Inventaa LED Lights ─────────────────────
UPDATE tenants SET
    neg_min_units = 5
WHERE tenant_id = 'tenant_inventaa_led_001';

-- ── STEP 3: Register prompts in prompt_templates (normalised prompt store) ───
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

    ('graphrag_product_card_caption',
     '{idx}. {name}\nRs.{price}{save_line}{rating_line}',
     'idx,name,price,save_line,rating_line',
     'Caption for single product image card returned by GraphRAG'),

    ('graphrag_product_list_header_prompt',
     'Here are the options for you, {sender_name}! 💡\n',
     'sender_name',
     'Header for product summary list text returned by GraphRAG'),

    ('graphrag_product_list_item_image_prompt',
     '\n*{idx}.* {name} — Rs.{price}{save_line}',
     'idx,name,price,save_line',
     'Format for product list items that have images sent as cards'),

    ('graphrag_product_list_item_no_image_prompt',
     '\n*{idx}.* {name} — Rs.{price}',
     'idx,name,price',
     'Format for product list items that do not have images'),

    ('graphrag_product_list_footer_prompt',
     '\n\nReply with the product *number* or *name* to know more or place an order.',
     '',
     'Call-to-action footer for GraphRAG product list text response'),

    ('graphrag_no_url_configured_prompt',
     'I''m not able to look up products right now, {sender_name}. Please contact *{support}* for assistance.',
     'sender_name,support',
     'Fallback message when GraphRAG API URL is not configured'),

    ('graphrag_403_error_prompt',
     'Thanks for your interest, {sender_name}! 😊\n\nI''m having trouble fetching product information right now.\nPlease contact *{support}* for assistance.',
     'sender_name,support',
     'Response when GraphRAG server returns a 403 Forbidden error'),

    ('graphrag_http_error_prompt',
     'I''m having trouble fetching product information right now, {sender_name}. 🔧\n\nPlease try again shortly or contact *{support}*',
     'sender_name,support',
     'Response when GraphRAG server returns non-200 HTTP error'),

    ('graphrag_category_clarify_default',
     'Could you let me know which category you''re interested in?',
     'sender_name',
     'Default category clarification text if GraphRAG doesn''t supply a message'),

    ('graphrag_category_clarify_greeting_prompt',
     'Hi {sender_name}! {clarify_msg}',
     'sender_name,clarify_msg',
     'Greeting line for category clarification list'),

    ('graphrag_category_clarify_footer_prompt',
     'Just reply with the collection name and I''ll show you the options! 💡',
     '',
     'Instructions line at the bottom of category clarification list'),

    ('graphrag_empty_results_prompt',
     'Sorry {sender_name}, I couldn''t find any products matching that. Could you try describing it differently, or browse all products at {website_or_biz}? 💡',
     'sender_name,website_or_biz',
     'Message sent when GraphRAG search yields no product results'),

    ('graphrag_retry_failed_prompt',
     'Sorry {sender_name}, I''m having trouble finding that right now. Could you try rephrasing, or browse all products at {website_or_biz}? 💡',
     'sender_name,website_or_biz',
     'Message sent when both original query and simplified retry query fail'),

    ('graphrag_exception_fallback_prompt',
     'Thanks for your interest in our products, {sender_name}! 💡\n\nOur product search is temporarily unavailable. Meanwhile:\n\n{bullet_website}\nNeed help? Contact *{support}*',
     'sender_name,bullet_website,support',
     'Emergency fallback text when GraphRAG call throws Python exception'),

    ('neg_ask_quantity_prompt',
     'I''d be happy to work on pricing for *{product_name}*, {sender_name}! How many units are you looking for?',
     'product_name,sender_name',
     'Asks customer for order quantity before presenting first price offer'),

    ('neg_ask_quantity_retry_prompt',
     'I didn''t catch that, {sender_name}. How many units of *{product_name}* would you like?',
     'product_name,sender_name',
     'Retry prompt when quantity extraction yields empty/invalid value'),

    ('neg_accepted_confirmation_prompt',
     'Wonderful! 🎉 Confirming *{quantity} units* of *{product_name}* at *Rs.{last_offer}/unit*. Reply *Confirm* to place your order!',
     'quantity,product_name,last_offer',
     'Response when customer accepts negotiator offer, asking for Confirm reply'),

    ('neg_stalemate_reply_prompt',
     'Our current offer is *Rs.{last_offer}/unit* for *{quantity} units* (Total: *Rs.{total}*). Would you like to proceed?',
     'last_offer,quantity,total',
     'Politely holds current offer price when customer does not bargain or accept'),

    ('neg_upsell_hint_prompt',
     '\n\n💡 Add Rs.{value_gap} more to your order value (approx. {units_needed} more unit(s)) to reach Rs.{next_min_val} and unlock *{next_disc_pct}% off*!',
     'value_gap,units_needed,next_min_val,next_disc_pct',
     'Dynamic upsell message to prompt customer to buy more to reach next discount tier'),

    ('neg_max_discount_unlocked_prompt',
     '\n\n🎉 You''ve unlocked our *maximum store discount of {max_disc}% OFF*!',
     'max_disc',
     'Greets customer when they qualify for the maximum store discount tier'),

    ('neg_qty_update_with_discount_prompt',
     '✅ Updated your order from *{prev_qty}* to *{quantity} units*, {sender_name}!\n\n• *Product:* {product_name}\n• *Quantity:* {quantity} units\n• *Regular price:* Rs.{price_num}/unit\n• *Store offer {current_tier_disc}% OFF applied:* Rs.{tier_price}/unit\n• *Subtotal:* Rs.{sub_price}\n• *GST ({gst_pct}%):* Rs.{gst_amount}\n• *Total Payable:* Rs.{total_pay}',
     'prev_qty,quantity,sender_name,product_name,price_num,current_tier_disc,tier_price,sub_price,gst_pct,gst_amount,total_pay',
     'Quantity update summary response when store discount applies'),

    ('neg_qty_update_no_discount_prompt',
     '✅ Updated your order from *{prev_qty}* to *{quantity} units*, {sender_name}!\n\n• *Product:* {product_name}\n• *Quantity:* {quantity} units\n• *Regular price:* Rs.{price_num}/unit\n• *Unit price:* Rs.{tier_price}/unit\n• *Subtotal:* Rs.{sub_price}\n• *GST ({gst_pct}%):* Rs.{gst_amount}\n• *Total Payable:* Rs.{total_pay}',
     'prev_qty,quantity,sender_name,product_name,price_num,tier_price,sub_price,gst_pct,gst_amount,total_pay',
     'Quantity update summary response when no store discount applies'),

    ('neg_qty_update_footer_prompt',
     'Reply *Confirm* to place your order! 🎉',
     '',
     'CTA line at the end of updated quantity message'),

    ('invoice_no_orders_found_prompt',
     'I couldn''t find any recent orders for you, {sender_name}. 🤔\n\nIf you''d like to place a new order, just let me know what you need!',
     'sender_name',
     'Reply when customer requests invoice but has no orders in system'),

    ('invoice_success_reply_prompt',
     'Here is your tax invoice for order *{order_id}*, {sender_name}! 📄\n\n🔗 *Download Invoice PDF*:\n{invoice_url}\n\nThank you for doing business with *{biz_name}*! 🙏',
     'order_id,sender_name,invoice_url,biz_name',
     'Response enclosing downloadable tax invoice URL for confirmed orders'),

    ('invoice_pdf_failed_prompt',
     'I had trouble generating your invoice PDF right now, {sender_name}. 🔧\n\nPlease contact our team at *{support}* to get your invoice.',
     'sender_name,support',
     'Fallback message when invoice PDF generator encounters an error')

) AS col(prompt_name, prompt_text, variables, description)
WHERE t.tenant_id = 'tenant_inventaa_led_001'
  AND col.prompt_text IS NOT NULL
  AND col.prompt_text <> ''
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;




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




-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 025: Seed neg_ask_quantity_prompt / neg_ask_quantity_retry_prompt
--
-- BUG OBSERVED:
--   [PROMPT] Unknown key: 'neg_ask_quantity_retry_prompt' crashed
--   handle_negotiation()'s Step 1 (awaiting_quantity retry branch), which
--   was caught by a broad exception handler and silently fell through to
--   GraphRAG's generic fallback reply instead of asking the customer for
--   quantity again. The key IS present in PROMPT_KEYS (db/prompt_store.py)
--   as of this session — this migration guarantees the DB side is seeded
--   too, independent of whether that was the actual point of failure.
--
-- Safe to re-run — ON CONFLICT DO NOTHING.
-- ══════════════════════════════════════════════════════════════════════════════

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
(
    'tenant_inventaa_led_001', 'neg_ask_quantity_prompt', 'en', 1, 'active',
    'I''d be happy to work on pricing for *{product_name}*, {sender_name}! How many units are you looking for?',
    'product_name,sender_name',
    'Asks customer for order quantity before presenting first price offer.'
),
(
    'tenant_inventaa_led_001', 'neg_ask_quantity_retry_prompt', 'en', 1, 'active',
    'I didn''t catch that, {sender_name}. How many units of *{product_name}* would you like?',
    'product_name,sender_name',
    'Retry prompt when quantity extraction yields empty/invalid value.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, LEFT(prompt_text, 80) AS preview, variables
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name IN ('neg_ask_quantity_prompt', 'neg_ask_quantity_retry_prompt')
ORDER BY prompt_name;






-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 026: pf_data_extraction_prompt — stop inferring quantity from history
--
-- BUG (confirmed against live server log):
--   Sequence: "i want to order 1" (parsed_order_quantity: None, correct —
--   "1" here is a LIST POSITION already resolved by NUMBER-SELECT, not a
--   quantity) → "is it waterproof" (parsed_order_quantity: 1 — WRONG, this
--   message contains no quantity at all). The LLM call passes the last 4
--   turns of conversation history alongside the current message, and
--   without explicit instruction otherwise, pulled "1" from the earlier
--   turn still visible in that history.
--
--   That stray "1" then caused _try_resolve_product_followup() to create a
--   premature, unrequested negotiation_state (qty=1) from an "is it
--   waterproof" question. Every subsequent quantity message ("i want to
--   order 2 units", "add 3 more units") then found neg_state already
--   populated and routed into handle_negotiation()'s "ongoing negotiation"
--   path instead of fresh order creation — which doesn't recognize either
--   phrase as a quantity change, so it fell through to the stalemate reply
--   and kept repeating "1 units" instead of updating to 2, then 5.
--
--   Same class of bug as migration 020 (a stray number from history
--   bleeding into a structured field), resurfacing at this newer
--   consolidated-extraction layer instead of the old free-text layer.
--
-- FIX: adds an explicit instruction that parsed_order_quantity must come
-- ONLY from the customer's CURRENT message, never inferred or carried
-- forward from earlier turns even if a number was mentioned before.
-- Nothing else in the prompt changes.
-- ══════════════════════════════════════════════════════════════════════════════

UPDATE prompt_templates
SET
    prompt_text = 'You are a data extraction assistant for {biz_name}.
Extract structured information from the customer''s CURRENT message about product
selection, ordering intent, negotiation intent, and whether this message needs
long-term customer memory.

PRODUCT CATALOG (reference only):
{product_catalog}

Extract and return JSON with these fields:
- selected_product_name: exact product name if customer selected one, else null
- parsed_order_quantity: integer ONLY if the CUSTOMER''S CURRENT MESSAGE itself
  explicitly states a quantity (e.g. "2 units", "add 3 more", "I want 5"). Do NOT
  infer this from earlier turns in the conversation history, even if a number
  was mentioned in a previous customer message — a quantity does not carry
  forward automatically, and a number from an earlier turn (e.g. a product
  LIST SELECTION like "order 1") is never a quantity for a later, unrelated
  message. If the current message contains no quantity itself, this MUST be
  null, regardless of what numbers appear elsewhere in the conversation.
- is_comparison: true if customer wants to compare products
- is_offer_inquiry: true if customer is asking about GENERAL store offers/discounts/tiers
  (e.g. "any offers?", "what deals do you have?") — NOT a specific price they are proposing.
- is_installation_inquiry: true if customer wants installation guide or images
- is_negotiation_request: true if the customer is proposing a SPECIFIC price,
  asking for a discount on a specific product, or otherwise trying to negotiate
  price (e.g. "can I get this for 900", "my budget is 1500", "any discount?",
  "can we settle at X"). A specific price offer is ALWAYS is_negotiation_request=true
  and is_offer_inquiry=false, even if it also mentions the word "offer" — a customer
  naming their own price is never asking about your general store offers.
- needs_long_term_memory: true ONLY if the customer is asking something that can
  only be answered using information from a PAST conversation or PAST order —
  e.g. "recommend something for me", "what did I buy last time", "do you remember
  my last order", "same as before", "I usually prefer X". False for anything
  answerable from the current conversation and the product catalog above
  (product questions, quantity changes, comparisons, installation guides,
  confirmations, and negotiation) — those should always be false.

Reply ONLY with valid JSON. No explanation.',
    variables = 'biz_name,product_catalog',
    version = version + 1,
    updated_at = NOW()
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'pf_data_extraction_prompt'
  AND language = 'en'
  AND status = 'active';

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, version, updated_at, LEFT(prompt_text, 200) AS preview
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'pf_data_extraction_prompt'
  AND status = 'active';




  -- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 025: Seed neg_ask_quantity_prompt / neg_ask_quantity_retry_prompt
--
-- BUG OBSERVED:
--   [PROMPT] Unknown key: 'neg_ask_quantity_retry_prompt' crashed
--   handle_negotiation()'s Step 1 (awaiting_quantity retry branch), which
--   was caught by a broad exception handler and silently fell through to
--   GraphRAG's generic fallback reply instead of asking the customer for
--   quantity again. The key IS present in PROMPT_KEYS (db/prompt_store.py)
--   as of this session — this migration guarantees the DB side is seeded
--   too, independent of whether that was the actual point of failure.
--
-- Safe to re-run — ON CONFLICT DO NOTHING.
-- ══════════════════════════════════════════════════════════════════════════════

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
(
    'tenant_inventaa_led_001', 'neg_ask_quantity_prompt', 'en', 1, 'active',
    'I''d be happy to work on pricing for *{product_name}*, {sender_name}! How many units are you looking for?',
    'product_name,sender_name',
    'Asks customer for order quantity before presenting first price offer.'
),
(
    'tenant_inventaa_led_001', 'neg_ask_quantity_retry_prompt', 'en', 1, 'active',
    'I didn''t catch that, {sender_name}. How many units of *{product_name}* would you like?',
    'product_name,sender_name',
    'Retry prompt when quantity extraction yields empty/invalid value.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, LEFT(prompt_text, 80) AS preview, variables
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name IN ('neg_ask_quantity_prompt', 'neg_ask_quantity_retry_prompt')
ORDER BY prompt_name;





-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 027: neg_extract_negotiation_intent_prompt — add UNIT/TOTAL
-- disambiguation for counter_offer, closing the one real gap in the
-- consolidation of 4 negotiation detectors into 1.
--
-- WHY THIS IS NEEDED:
--   The four detectors being replaced were NOT purely redundant — the old
--   detect_counter_offer() (neg_detect_counter_prompt) disambiguated whether
--   a customer's number was a PER-UNIT price or a TOTAL order price:
--     "1800 per unit"           -> UNIT
--     "8500 total"              -> TOTAL
--     "can we move with 2150"   -> ambiguous, judged against current price/qty
--   The consolidated schema (migration 017) never carried this distinction —
--   counter_offer.value was always treated as per-unit. Without this fix,
--   consolidating the four calls into one would silently regress this
--   disambiguation (a customer quoting a TOTAL price would be misread as an
--   impossibly-low per-unit price).
--
-- FIX: adds price_type: "UNIT" | "TOTAL" to the counter_offer object, using
-- the exact same disambiguation rules the old neg_detect_counter_prompt
-- used. Python (ai/negotiator.py) divides by quantity when price_type is
-- TOTAL — the LLM classifies, it never computes.
-- ══════════════════════════════════════════════════════════════════════════════

UPDATE prompt_templates
SET
    prompt_text = 'You analyze a customer message during a price negotiation.

Product: {product_name}
Current offer: Rs.{current_price}/unit for {current_qty} units.
Current total at that price: Rs.{current_total}.

Return ONLY a JSON object in this exact schema:
{
  "intent": "ACCEPTED" | "QTY_CHANGE" | "COUNTER_OFFER" | "MORE_DISCOUNT" | "NONE",
  "matched_phrase": "the exact phrase that triggered your classification",
  "accepted":        {"value": true/false,   "confidence": 0.0-1.0},
  "quantity_change": {
    "value": N or null,
    "operation": "SET" | "ADD" | "REMOVE" | null,
    "confidence": 0.0-1.0
  },
  "counter_offer": {
    "value": N or null,
    "price_type": "UNIT" | "TOTAL" | null,
    "confidence": 0.0-1.0
  },
  "more_discount":   {"value": true/false,    "confidence": 0.0-1.0}
}

CRITICAL — quantity_change.value is the RAW NUMBER the customer mentioned.
Do NOT compute a new total yourself (e.g. do not add it to the current
quantity) — the application does that arithmetic using the operation field.

OPERATION RULES for quantity_change:
- "add N more units", "add another N", "increase by N", "give me N more"
  → operation="ADD", value=N (the number mentioned, NOT current_qty+N)
- "remove N units", "reduce by N", "take away N"
  → operation="REMOVE", value=N
- "change to N units", "make it N", "update to N", "I want N units total"
  (an ABSOLUTE final quantity, not phrased as relative to the current one)
  → operation="SET", value=N

CRITICAL — counter_offer.price_type disambiguates whether the customer''s
number is a PER-UNIT price or a TOTAL order price. The application divides
TOTAL by quantity itself — never do that arithmetic yourself, just classify.
RULES for price_type:
- Explicit words "each", "per unit", "per piece" → price_type="UNIT"
- Explicit words "total", "overall", "for all", "for everything" → price_type="TOTAL"
- Number clearly LESS than the current per-unit price (Rs.{current_price}) → price_type="UNIT"
  (a customer negotiating always asks for a lower per-unit price, never a
  higher one, so a number below the current unit price is a unit price)
- Number between the current per-unit price and the current total
  (Rs.{current_total}) with no explicit word → price_type="TOTAL"
  (a number in that range only makes sense as a total for {current_qty} units)
- Ambiguous with no other signal → default price_type="TOTAL" if the number
  is close to the current total, otherwise "UNIT"

EXAMPLES:
Message: "Proceed"
→ {"intent":"ACCEPTED","matched_phrase":"Proceed","accepted":{"value":true,"confidence":0.99},"quantity_change":{"value":null,"operation":null,"confidence":0.99},"counter_offer":{"value":null,"price_type":null,"confidence":0.99},"more_discount":{"value":false,"confidence":0.99}}

Message: "Make it 6 units" (current_qty=2)
→ {"intent":"QTY_CHANGE","matched_phrase":"Make it 6 units","accepted":{"value":false,"confidence":0.97},"quantity_change":{"value":6,"operation":"SET","confidence":0.97},"counter_offer":{"value":null,"price_type":null,"confidence":0.97},"more_discount":{"value":false,"confidence":0.97}}

Message: "add 10 more units" (current_qty=2)
→ {"intent":"QTY_CHANGE","matched_phrase":"add 10 more units","accepted":{"value":false,"confidence":0.97},"quantity_change":{"value":10,"operation":"ADD","confidence":0.97},"counter_offer":{"value":null,"price_type":null,"confidence":0.97},"more_discount":{"value":false,"confidence":0.97}}
(Note: value is 10 — the number the customer said — NOT 12. The application computes current_qty + value = 12 itself.)

Message: "remove 2 units" (current_qty=5)
→ {"intent":"QTY_CHANGE","matched_phrase":"remove 2 units","accepted":{"value":false,"confidence":0.96},"quantity_change":{"value":2,"operation":"REMOVE","confidence":0.96},"counter_offer":{"value":null,"price_type":null,"confidence":0.96},"more_discount":{"value":false,"confidence":0.96}}

Message: "1800 is my budget" (current_price=2200, current_qty=1, current_total=2200)
→ {"intent":"COUNTER_OFFER","matched_phrase":"1800 is my budget","accepted":{"value":false,"confidence":0.98},"quantity_change":{"value":null,"operation":null,"confidence":0.98},"counter_offer":{"value":1800,"price_type":"UNIT","confidence":0.9},"more_discount":{"value":false,"confidence":0.98}}
(1800 < current per-unit price 2200, so it is read as a per-unit ask.)

Message: "can you do 8500 total for the 5 units" (current_price=1900, current_qty=5, current_total=9500)
→ {"intent":"COUNTER_OFFER","matched_phrase":"can you do 8500 total for the 5 units","accepted":{"value":false,"confidence":0.97},"quantity_change":{"value":null,"operation":null,"confidence":0.97},"counter_offer":{"value":8500,"price_type":"TOTAL","confidence":0.98},"more_discount":{"value":false,"confidence":0.97}}
(explicit word "total", and 8500 is close to current_total 9500 — clearly a total ask, not a per-unit price of 8500.)

Message: "Can you do any better?"
→ {"intent":"MORE_DISCOUNT","matched_phrase":"Can you do any better","accepted":{"value":false,"confidence":0.95},"quantity_change":{"value":null,"operation":null,"confidence":0.95},"counter_offer":{"value":null,"price_type":null,"confidence":0.95},"more_discount":{"value":true,"confidence":0.95}}

Message: "Proceed with 6 units" (current_qty=2)
→ {"intent":"ACCEPTED","matched_phrase":"Proceed with 6 units","accepted":{"value":true,"confidence":0.91},"quantity_change":{"value":6,"operation":"SET","confidence":0.85},"counter_offer":{"value":null,"price_type":null,"confidence":0.97},"more_discount":{"value":false,"confidence":0.97}}

Note: "Proceed with 6 units" → intent=ACCEPTED because acceptance takes precedence.
The caller enforces: ACCEPTED > QTY_CHANGE > COUNTER_OFFER > MORE_DISCOUNT.

Set confidence to reflect how certain you are. If ambiguous, set confidence below 0.75.
Reply ONLY with the JSON object.',
    version = version + 1,
    status  = 'active',
    variables = 'product_name,current_price,current_qty,current_total',
    updated_at = NOW()
WHERE tenant_id    = 'tenant_inventaa_led_001'
  AND prompt_name  = 'neg_extract_negotiation_intent_prompt'
  AND language     = 'en'
  AND status       = 'active';

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, version, updated_at, variables
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'neg_extract_negotiation_intent_prompt'
  AND status = 'active';










  -- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 028: Memory-aware GraphRAG queries
--
-- WHY:
--   Confirmed by tracing the live code: needs_long_term_memory has always
--   been correctly computed by pf_data_extraction_prompt, but nothing ever
--   consumed it — MemoryManager.search() (retrieval) was never called
--   anywhere in the live pipeline, only save_negotiation_outcome() (a
--   write). A customer asking "recommend something for me based on what
--   I usually buy" got sent to GraphRAG as the raw, unenriched string —
--   GraphRAG had no way to know what "usually buy" means for this customer.
--
--   Additionally: that specific query never even reaches
--   pf_data_extraction_prompt at all when there's no active product-list
--   session context (confirmed in logs — it goes straight from intent
--   classification to the raw GraphRAG call). mem0_worthy_check_prompt
--   (seeded in migration 016, never called anywhere until now) is the
--   standalone fallback classifier for exactly this case.
--
-- THIS MIGRATION seeds graphrag_query_builder_prompt — takes the raw
-- customer message plus flattened retrieved-memory text, returns ONE
-- enriched query string for GraphRAG. The customer never sees this output;
-- only GraphRAG does. Per the recommendation this replaced: query
-- construction stays DB-driven/tenant-configurable, not Python string
-- concatenation.
-- ══════════════════════════════════════════════════════════════════════════════

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
(
    'tenant_inventaa_led_001', 'graphrag_query_builder_prompt', 'en', 1, 'active',
    'You rewrite a customer''s message into a single, specific search query for
a product-search engine, using relevant context retrieved from past
conversations. The customer never sees your output — only the search
engine does.

Customer message:
{customer_message}

Relevant context from past conversations/orders:
{memory_context}

Rewrite the customer''s message into ONE specific search query that
incorporates the relevant context above — e.g. if the customer asks for a
recommendation and the context shows they previously bought or discussed
specific products or categories, mention those specific products/categories
in the rewritten query so the search engine can find genuinely similar or
complementary items, instead of guessing from a vague request.

Do not invent products or categories that are not in the context above. If
the context above is not actually relevant to the customer''s message, just
return the customer''s original message unchanged.

Reply ONLY with the rewritten query text. No explanation, no quotes.',
    'customer_message,memory_context',
    'Builds a memory-enriched GraphRAG search query. Output is never shown to the customer.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, version, LEFT(prompt_text, 100) AS preview, variables
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name IN ('graphrag_query_builder_prompt', 'mem0_worthy_check_prompt')
ORDER BY prompt_name;







-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 029: intent_system_prompt — add needs_long_term_memory
--
-- REPLACES the approach from migration 028 (a dedicated mem0_worthy_check_prompt
-- classifier called from graphrag_handler.py). Per review: that added a new
-- LLM call specifically to decide "should I retrieve memory?" when
-- classify_intent() ALREADY runs on every single message before dispatch.
-- Folding this signal into that existing call costs nothing extra — same
-- consolidation principle as migration 017 (negotiation intent) and
-- migration 016 (pf_data_extraction_prompt's own consolidation).
--
-- mem0_worthy_check_prompt (migration 016) is left in the DB unused, not
-- deleted — no harm in the row existing, but nothing calls it after this.
--
-- ONLY the JSON schema and one new rule are added. Every existing category,
-- example, and rule from migration 023 is unchanged.
-- ══════════════════════════════════════════════════════════════════════════════

UPDATE prompt_templates
SET
    prompt_text = 'You are an intent classification AI for Inventaa LED Lights — an Indian LED lighting manufacturer based in Chennai.
Classify the customer message into EXACTLY ONE intent:
WORKFLOW_ACTION — Customer wants to DO something:
  place order, track order, check status, request invoice, confirm payment,
  enquire about specific product by name/SKU/number, say "I want to buy".
  Examples:
  → "I want to order 5 flood lights"
  → "I want to order mini elena 10w outdoor gate light"
  → "send me invoice"
  → "where is my order INV#A3F21"
  → "I want to buy this"
  → "order 2 of these"
  → "I want to order product number 3"
  → "confirm my order"
  → "I want to reorder"
FAQ_KNOWLEDGE — Customer is browsing/researching, NOT ready to buy:
  asking about products, pricing, features, availability, comparisons, categories.
  Examples:
  → "what outdoor lights do you have?"
  → "show me gate lights"
  → "tell me about Reva LED"
  → "what is the price of 9W bulb?"
  → "compare Aeris and Villa gate light"
  → "what wattage options are available?"
  → "any offers or discounts?"
  → "is the Zenia SKY waterproof?"
HUMAN_ESCALATION — Upset, complaint, refund, wants human/manager — including a
  plain, neutral request to speak with a human agent even with no complaint
  stated (do not require complaint framing; wanting a human is enough on its own):
  Examples:
  → "I want to talk to a manager"
  → "my product arrived damaged"
  → "I need a refund"
  → "nobody is responding to my complaint"
  → "I want to speak to a real human agent"
  → "connect me to customer support"
GREETING — Greeting, thanks, acknowledgement, goodbye:
  Examples:
  → "Hi", "Hello", "Good morning", "Thank you", "Ok", "Noted", "Bye", "👍"
UNKNOWN — Gibberish, completely unrelated, makes no sense.

Additionally, determine needs_long_term_memory: true ONLY if answering this
message requires recalling something from a PAST conversation or PAST
order — not just anything product-related. This is independent of intent.
Examples that are true:
  → "recommend something for me"
  → "what do I usually buy"
  → "show similar products to what I bought before"
  → "same as my previous order"
  → "buy again"
  → "based on my history, what should I get"
Examples that are FALSE — a specific product/category mentioned by name is
answerable from the current message and catalog alone, never needs memory:
  → "garden lights" / "show me wall lights" (browsing a category)
  → "compare product 2 and 3" (comparing named items)
  → "price of product 5" (asking about a named item)
  → any message naming a specific product, SKU, or category

RULES:
1. Reply ONLY with valid JSON. No explanation, no markdown.
2. Keys: "intent", "confidence_score" (float 0.0-1.0), "needs_long_term_memory" (true/false).
3. confidence < 0.50 → set intent to "UNKNOWN".
4. Never invent a new intent name.
5. Customer mentions product + quantity → WORKFLOW_ACTION.
6. Customer says "I want to order/buy" → WORKFLOW_ACTION even without quantity.
7. Customer says "I want to speak to/talk to a human/agent/person/manager/support"
   → HUMAN_ESCALATION, even without a stated complaint and even though it
   also starts with "I want to" like the WORKFLOW_ACTION examples — the
   verb (speak/talk to a human) decides this, not the sentence opener.
8. needs_long_term_memory is independent of intent — a WORKFLOW_ACTION or
   FAQ_KNOWLEDGE message can still need memory if it references the
   customer''s own past behavior rather than naming a specific product.
Output ONLY: {"intent": "WORKFLOW_ACTION", "confidence_score": 0.97, "needs_long_term_memory": false}',
    variables = NULL,
    version = version + 1,
    updated_at = NOW()
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'intent_system_prompt'
  AND language = 'en'
  AND status = 'active';

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, version, updated_at
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'intent_system_prompt'
  AND status = 'active';




  -- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 030: graphrag_query_builder_prompt — add current_product
--
-- Combines short-term context (this session's currently-discussed product,
-- from negotiation_state or last_discussed_product — cheap DB reads, no new
-- LLM call) with long-term context (Mem0, from migration 028). A customer
-- actively looking at one product asking "recommend something for me"
-- should get results related to what's in front of them right now, not
-- only what they bought previously.
-- ══════════════════════════════════════════════════════════════════════════════

UPDATE prompt_templates
SET
    prompt_text = 'You rewrite a customer''s message into a single, specific search query for
a product-search engine, using context from the current conversation and
from past conversations/orders. The customer never sees your output — only
the search engine does.

Customer message:
{customer_message}

Product currently being discussed in this conversation (if any):
{current_product}

Relevant context from past conversations/orders:
{memory_context}

Rewrite the customer''s message into ONE specific search query that
incorporates the context above. If a product is currently being discussed,
prioritize that as the anchor for the search (e.g. "recommend something
similar to {current_product}"). Otherwise use the past-conversation context
to anchor the search on previously discussed products or preferences.

Do not invent products or categories not mentioned in the context above. If
none of the context above is actually relevant to the customer''s message,
just return the customer''s original message unchanged.

Reply ONLY with the rewritten query text. No explanation, no quotes.',
    variables = 'customer_message,current_product,memory_context',
    version = version + 1,
    updated_at = NOW()
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'graphrag_query_builder_prompt'
  AND language = 'en'
  AND status = 'active';

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, version, updated_at, variables
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'graphrag_query_builder_prompt'
  AND status = 'active';






  -- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 032: no_active_workflow_prompt
--
-- Shown when a message classifies as operation=MODIFY_WORKFLOW (migration
-- 031) but no active order/negotiation state exists — e.g. "add 3 more
-- units" arriving in a fresh session with nothing to add to. Previously
-- this went to GraphRAG, which correctly had no way to answer it
-- ("Could you please specify which product...") since it's a
-- catalog-search engine being asked a workflow-state question.
-- ══════════════════════════════════════════════════════════════════════════════

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
(
    'tenant_inventaa_led_001', 'no_active_workflow_prompt', 'en', 1, 'active',
    'Hi {sender_name}! You don''t have an order in progress right now — what would you like to order? 😊',
    'sender_name',
    'Shown when a MODIFY_WORKFLOW message (e.g. "add 3 more units") arrives with no active order/negotiation to modify.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, version, prompt_text, variables
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'no_active_workflow_prompt';




  -- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 031: intent_system_prompt — RoutingDecision
--
-- REPLACES migration 029's standalone needs_long_term_memory field with a
-- small, tenant-agnostic RoutingDecision structure, per review:
--
--   The real problem wasn't "GraphRAG is being called" — it's "who decides
--   whether GraphRAG should be called." Today GraphRAG does two unrelated
--   jobs: product retrieval, and acting as a fallback for incomplete
--   workflow state. A message like "add 3 more units" arriving with no
--   active order gets routed to GraphRAG, which correctly has no idea what
--   to do with it — the router asked the wrong component.
--
-- FIX: intent_system_prompt now also outputs:
--   operation             — "NEW_SEARCH" | "MODIFY_WORKFLOW" | "OTHER"
--   needs_graphrag         — does this need product-catalog knowledge?
--   needs_memory           — does this need past-conversation/order recall?
--   needs_workflow_state   — does this need the current order/negotiation state?
--   needs_product_context  — does this need to know which product is active?
--
-- These are semantic classifications, not keyword matches — "add 3 more
-- units" (retail), "add one more MRI scan" (healthcare), "add two pizzas"
-- (restaurant), and "add spouse" (insurance) all classify identically as
-- operation=MODIFY_WORKFLOW, needs_graphrag=false — the same rule works
-- for every tenant/domain without any tenant-specific code.
-- ══════════════════════════════════════════════════════════════════════════════

UPDATE prompt_templates
SET
    prompt_text = 'You are an intent classification AI for Inventaa LED Lights — an Indian LED lighting manufacturer based in Chennai.
Classify the customer message into EXACTLY ONE intent:
WORKFLOW_ACTION — Customer wants to DO something:
  place order, track order, check status, request invoice, confirm payment,
  enquire about specific product by name/SKU/number, say "I want to buy".
  Examples:
  → "I want to order 5 flood lights"
  → "I want to order mini elena 10w outdoor gate light"
  → "send me invoice"
  → "where is my order INV#A3F21"
  → "I want to buy this"
  → "order 2 of these"
  → "I want to order product number 3"
  → "confirm my order"
  → "I want to reorder"
FAQ_KNOWLEDGE — Customer is browsing/researching, NOT ready to buy:
  asking about products, pricing, features, availability, comparisons, categories.
  Examples:
  → "what outdoor lights do you have?"
  → "show me gate lights"
  → "tell me about Reva LED"
  → "what is the price of 9W bulb?"
  → "compare Aeris and Villa gate light"
  → "what wattage options are available?"
  → "any offers or discounts?"
  → "is the Zenia SKY waterproof?"
HUMAN_ESCALATION — Upset, complaint, refund, wants human/manager — including a
  plain, neutral request to speak with a human agent even with no complaint
  stated (do not require complaint framing; wanting a human is enough on its own):
  Examples:
  → "I want to talk to a manager"
  → "my product arrived damaged"
  → "I need a refund"
  → "nobody is responding to my complaint"
  → "I want to speak to a real human agent"
  → "connect me to customer support"
GREETING — Greeting, thanks, acknowledgement, goodbye:
  Examples:
  → "Hi", "Hello", "Good morning", "Thank you", "Ok", "Noted", "Bye", "👍"
UNKNOWN — Gibberish, completely unrelated, makes no sense.

Additionally, determine a routing decision — which subsystems this message
actually needs. This is independent of intent.

operation — one of:
  "NEW_SEARCH"      — customer is looking for or asking about products/items
                       not already established in this conversation.
  "MODIFY_WORKFLOW"  — customer is changing something about an ALREADY
                       in-progress order/workflow (quantity, price
                       negotiation, confirmation) without naming a new
                       product. The specific product/order this refers to
                       is NOT stated in this message — it depends entirely
                       on conversation state.
  "OTHER"           — greeting, escalation, or anything not fitting the
                       above two.
  Examples of MODIFY_WORKFLOW: "add 3 more units", "make it 5 instead",
  "can you do better on price", "yes confirm", "remove 2 of them" — note
  none of these name a specific product; they only make sense in the
  context of something already being discussed.
  Examples of NEW_SEARCH: "garden lights", "show me wall lights",
  "compare product 2 and 3", "price of product 5", "recommend something",
  "what outdoor lights do you have" — even though some of these need
  memory or product history, they are still asking the search/knowledge
  system for something, not modifying an established workflow silently.

needs_graphrag — true if answering requires product-catalog knowledge
  (search, comparison, recommendation, FAQ about a product). false for
  MODIFY_WORKFLOW operations — those are answered from conversation state,
  never from a catalog search.

needs_memory — true ONLY if answering requires recalling a PAST
  conversation or PAST order, not just the current one. Examples: "recommend
  something for me", "what do I usually buy", "same as before". False for
  anything answerable from the current conversation and catalog alone.

needs_workflow_state — true if this message depends on knowing whether an
  order/negotiation is already in progress (this is almost always true when
  operation=MODIFY_WORKFLOW, and usually false for a fresh NEW_SEARCH).

needs_product_context — true if this message depends on knowing WHICH
  product is currently being discussed (true for most MODIFY_WORKFLOW
  cases and for follow-up questions like "is it waterproof").

RULES:
1. Reply ONLY with valid JSON. No explanation, no markdown.
2. Keys: "intent", "confidence_score" (float 0.0-1.0), "operation",
   "needs_graphrag", "needs_memory", "needs_workflow_state",
   "needs_product_context" (all four booleans).
3. confidence < 0.50 → set intent to "UNKNOWN".
4. Never invent a new intent name or operation value.
5. Customer mentions product + quantity → WORKFLOW_ACTION.
6. Customer says "I want to order/buy" → WORKFLOW_ACTION even without quantity.
7. Customer says "I want to speak to/talk to a human/agent/person/manager/support"
   → HUMAN_ESCALATION, even without a stated complaint and even though it
   also starts with "I want to" like the WORKFLOW_ACTION examples — the
   verb (speak/talk to a human) decides this, not the sentence opener.
8. The routing fields are independent of intent — a WORKFLOW_ACTION message
   can be either operation, and needs_memory can be true regardless of intent.
Output ONLY: {"intent": "WORKFLOW_ACTION", "confidence_score": 0.97, "operation": "NEW_SEARCH", "needs_graphrag": true, "needs_memory": false, "needs_workflow_state": false, "needs_product_context": false}',
    variables = NULL,
    version = version + 1,
    updated_at = NOW()
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'intent_system_prompt'
  AND language = 'en'
  AND status = 'active';

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, version, updated_at
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'intent_system_prompt'
  AND status = 'active';








  -- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 033: require_offer_disclosure — tenant-configurable offer gating
--
-- Default FALSE preserves exactly the current behavior for every existing
-- tenant (store discounts auto-apply once quantity is known, same as
-- before this migration). A tenant can opt IN to requiring the customer
-- to explicitly ask about offers before any store discount is applied —
-- this is a business-policy decision, not something this codebase should
-- decide once for every tenant/domain.
--
-- Also fixes a separately-confirmed bug: prompts authored with literal \n
-- notation (instead of an actual line break) inside a plain PostgreSQL
-- string literal store the literal two-character sequence, not a real
-- newline (only E'...' strings interpret \n). Fixed in code (prompt_store.py
-- now normalizes this on every prompt render, regardless of how it was
-- authored) — no DB change needed for that part, included here as a note
-- since it was diagnosed in the same review.
-- ══════════════════════════════════════════════════════════════════════════════

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS require_offer_disclosure BOOLEAN DEFAULT FALSE;

UPDATE tenants SET
    require_offer_disclosure = FALSE
WHERE tenant_id = 'tenant_inventaa_led_001';

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT tenant_id, require_offer_disclosure FROM tenants WHERE tenant_id = 'tenant_inventaa_led_001';






-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 034: Memory-only lookup prompts
--
-- Closes a real gap in the Phase A routing gate (migration 031): it only
-- checked operation=="MODIFY_WORKFLOW", so a message correctly classified
-- as needs_graphrag=False, needs_memory=True (e.g. "what did I order
-- before?") still fell through to GraphRAG unconditionally — a
-- catalog-search engine with no access to past-order history, which
-- correctly (from its own perspective) replied "I don't have access to
-- your past orders." The problem was never GraphRAG's answer — it should
-- never have been asked.
--
-- router.py's dispatch() gate is now broadened to check needs_graphrag
-- directly (not just for MODIFY_WORKFLOW), and a memory-only lookup is
-- answered from Mem0 directly via these two prompts, never reaching
-- GraphRAG at all.
-- ══════════════════════════════════════════════════════════════════════════════

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
(
    'tenant_inventaa_led_001', 'memory_lookup_answer_prompt', 'en', 1, 'active',
    'You are a helpful WhatsApp sales assistant for a lighting retailer.
The customer asked about their own past orders or preferences. Answer
using ONLY the information below — do not invent products or details not
present here.

Customer message:
{customer_message}

What we know from past conversations/orders:
{memory_context}

Address the customer as {sender_name}. Be warm and concise (max 4 lines).
Do not mention "memory", "database", or how this information was
retrieved — just answer naturally, as a shop assistant who remembers a
returning customer would.

Reply with the answer only. No explanation, no markdown headers.',
    'customer_message,memory_context,sender_name',
    'Answers a memory-only lookup (e.g. "what did I order before?") directly from retrieved Mem0 context — never reaches GraphRAG.'
),
(
    'tenant_inventaa_led_001', 'memory_lookup_empty_prompt', 'en', 1, 'active',
    'I don''t have any past order or preference history for you yet, {sender_name}. What would you like to look for today? 😊',
    'sender_name',
    'Shown when a memory-only lookup finds nothing in Mem0 — no past orders/preferences saved yet for this customer.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;


-- ══════════════════════════════════════════════════════════════════════════════
-- MIGRATION 035: Custom dynamic pipeline and memory prompts
-- ══════════════════════════════════════════════════════════════════════════════

INSERT INTO prompt_templates (tenant_id, prompt_name, language, version, status, prompt_text, variables, description)
VALUES
(
    'tenant_inventaa_led_001', 'followup_installation_footer_prompt', 'en', 1, 'active',
    '\nNeed help with anything else?\n• 💰 Pricing & offers\n• 📦 Place an order\n• 🔒 Warranty\n\nOr just tell me how many units you''d like and I''ll set it up for you!',
    '',
    'Footer text template shown after installation guides are displayed to the customer.'
),
(
    'tenant_inventaa_led_001', 'followup_installation_header_prompt', 'en', 1, 'active',
    'Here is the installation guide for *{product_name}*:\n',
    'product_name',
    'Header text template for displaying product installation guide.'
),
(
    'tenant_inventaa_led_001', 'followup_installation_pdf_prompt', 'en', 1, 'active',
    '📄 PDF Guide: {pdf_url}',
    'pdf_url',
    'Line format template for displaying installation guide PDF link.'
),
(
    'tenant_inventaa_led_001', 'followup_installation_video_prompt', 'en', 1, 'active',
    '▶ Video Tutorial: {video_url}',
    'video_url',
    'Line format template for displaying installation guide video link.'
),
(
    'tenant_inventaa_led_001', 'followup_installation_manual_prompt', 'en', 1, 'active',
    '🔗 Manual Link: {manual_url}',
    'manual_url',
    'Line format template for displaying installation guide manual link.'
),
(
    'tenant_inventaa_led_001', 'followup_installation_steps_header_prompt', 'en', 1, 'active',
    '\n*Quick Steps:*',
    '',
    'Header text preceding quick steps list in installation guides.'
),
(
    'tenant_inventaa_led_001', 'memory_query_classifier_prompt', 'en', 1, 'active',
    'You are an intent classifier for a store customer support bot.
Classify if the customer''s message is asking about:
- "order": Their past completed purchases, order history, what they bought, or invoice records.
- "offer": Past discounts, special pricing history, or active offers they previously accepted.
- "other": General questions, catalog browsing, greetings, product questions, or current pricing.

Return a JSON object with keys:
{
  "memory_type": "order" | "offer" | "other",
  "confidence": 0.0 to 1.0
}',
    '',
    'Classifies customer inquiries into order history vs offer history vs catalog queries.'
),
(
    'tenant_inventaa_led_001', 'memory_no_orders_found_prompt', 'en', 1, 'active',
    'Hi {sender_name}! I couldn''t find any previous orders for you under this number. Would you like to browse our catalog or place a new order? 😊',
    'sender_name',
    'Message shown when customer asks about past orders but has no transaction history.'
),
(
    'tenant_inventaa_led_001', 'memory_order_formatter_prompt', 'en', 1, 'active',
    'Hi {sender_name}! Here is a summary of your previous completed orders:

{order_details}

Let me know if you would like to re-order any of these or if you need another invoice! 📄',
    'sender_name,order_details',
    'Formats and displays the list of customer''s past orders.'
),
(
    'tenant_inventaa_led_001', 'memory_offers_formatter_prompt', 'en', 1, 'active',
    'Hi {sender_name}! On your previous orders, you unlocked:

{offer_details}

For your next order, here are our current active store offers:

{current_store_offers}

Let me know what you''d like to set up today! 🚀',
    'sender_name,offer_details,current_store_offers',
    'Formats and displays the list of customer''s past discounts and active offers.'
)
ON CONFLICT (tenant_id, prompt_name, language, version) DO NOTHING;

-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT prompt_name, version, LEFT(prompt_text, 80) AS preview, variables
FROM prompt_templates
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name IN ('followup_installation_footer_prompt', 'memory_query_classifier_prompt', 'memory_order_formatter_prompt', 'memory_offers_formatter_prompt')
ORDER BY prompt_name;