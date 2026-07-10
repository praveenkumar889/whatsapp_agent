-- Migration 009: Update neg_ask_quantity_prompt to include standard price information
-- Run this in your database to update the prompt template.

UPDATE prompt_templates
SET prompt_text = 'I''d be happy to work on pricing for *{product_name}* (standard price is Rs.{regular_price}/unit), {sender_name}! How many units are you looking for?',
    variables = 'product_name,sender_name,regular_price'
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'neg_ask_quantity_prompt'
  AND language = 'en'
  AND version = 1;
