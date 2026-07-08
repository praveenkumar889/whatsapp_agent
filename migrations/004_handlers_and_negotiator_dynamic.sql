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
