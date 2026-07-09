-- Insert statements for dynamic follow-up and memory prompts
-- Run this directly in your database to register and seed the new prompts.

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
