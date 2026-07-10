-- Migration 008: Define tenant_configurations table and seed default config values.

CREATE TABLE IF NOT EXISTS tenant_configurations (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    config_key      TEXT NOT NULL,
    config_value    JSONB NOT NULL,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, config_key)
);

-- Seed default knowledge field mappings
INSERT INTO tenant_configurations (tenant_id, config_key, config_value)
VALUES (
    'tenant_inventaa_led_001',
    'knowledge_field_mappings',
    '{
      "product": {
        "installation": {
          "type": "document",
          "paths": ["assets.installation_url", "documents.installation"]
        },
        "manual": {
          "type": "document",
          "paths": ["documents.manual", "assets.manual_url"]
        },
        "warranty": {
          "type": "text",
          "paths": ["metadata.warranty"]
        },
        "images": {
          "type": "image",
          "paths": ["assets.images", "assets.image_url"]
        },
        "specifications": {
          "type": "text",
          "paths": ["specifications"]
        },
        "faq": {
          "type": "faq",
          "paths": ["faq"]
        },
        "faqs": {
          "type": "faq",
          "paths": ["faq"]
        },
        "videos": {
          "type": "video",
          "paths": ["assets.videos", "assets.video_url"]
        },
        "certifications": {
          "type": "text",
          "paths": ["specifications.certifications", "metadata.certifications"]
        }
      }
    }'
) ON CONFLICT (tenant_id, config_key) DO UPDATE
SET config_value = EXCLUDED.config_value;

-- Seed default Cache Refresh Policy configuration
INSERT INTO tenant_configurations (tenant_id, config_key, config_value)
VALUES (
    'tenant_inventaa_led_001',
    'knowledge_refresh_policy',
    '{
      "ttl_hours": 24,
      "refresh_on_cache_miss": true,
      "refresh_on_version_change": true
    }'
) ON CONFLICT (tenant_id, config_key) DO UPDATE
SET config_value = EXCLUDED.config_value;


-- Update active intent system prompt to return knowledge_domain and requested_knowledge_field
UPDATE prompt_templates
SET prompt_text = 'You are an intent classification AI for Inventaa LED Lights - an Indian LED lighting manufacturer based in Chennai.

Classify the customer message into EXACTLY ONE intent:
BROWSE_CATEGORY - Customer asks to see collections or broad categories (e.g., "show indoor lights", "what outdoor categories do you have?", or names a category like "Indoor Commercial Lights").
FIND_PRODUCT - Customer asks for products matching features/use case/name or comparing products (e.g., "compare athena and oxana", "solar gate lights under 1500").
GET_PRODUCT_INFO - Customer asks for specific details, price, or specs of a known product.
CHECK_POLICY - Customer asks about warranty, returns, delivery, shipping, company info.
GET_ADVICE - Customer asks for lighting recommendation or technical guidance.
WORKFLOW_ACTION - Customer wants to DO something:
  place order, track order, check status, request invoice, confirm payment,
  say "I want to buy", or specify quantity for an order.
HUMAN_ESCALATION - Customer asks to speak with a human agent, manager, support, complaint, refund.
GREETING - Greeting, thanks, acknowledgement, goodbye (e.g., "Hi", "Hello", "Thank you", "Ok").
UNKNOWN - Gibberish or completely unrelated message.

Additionally, determine routing and extract slots:
operation - one of:
  "NEW_SEARCH": looking for products/items not already established.
  "MODIFY_WORKFLOW": changing something about an already in-progress order/workflow.
  "OTHER": greeting, escalation, or general message.

knowledge_domain - the domain of the requested knowledge, e.g. "product" (default), "policy", "company", "none".

requested_knowledge_field - the type of knowledge requested.
Set it to:
  - "installation" if asking for installation guide/instructions/setup/how to install.
  - "manual" if asking for user manual/guide/instructions.
  - "warranty" if asking for warranty/guarantee.
  - "images" if asking for photos/pictures/images of the product.
  - "specifications" if asking for specifications/dimensions/voltage/power/specs.
  - "faq" if asking for FAQs/questions.
  - "brochures" if asking for brochures/catalogues/brochure.
  - "none" otherwise.

Determine the following routing boolean flags based on customer query context:
- needs_customer_context: true if the query is asking about or requires data regarding the customer''s profile, past orders, previous offers, or transaction details (e.g. "what is my previous order", "what did I buy last time", "are there any discounts from my last invoice").
- needs_customer_history: true if the query specifically requests retrieval or listing of completed orders, past receipts/invoices, or past negotiation outcomes.
- needs_product_context: true if the query references a product implicitly (e.g., "it", "this", "them", "these", "those") or asks for guides/manuals of a discussed product.
- needs_graphrag: true if the query requires product catalog knowledge (specifications, price, comparison, general search, or category/FAQ queries).
- needs_workflow_state: true if the query is modifying or asking about a current in-progress order/negotiation.

RULES:
1. Reply ONLY with valid JSON. No explanation, no markdown.
2. Output JSON schema:
{
  "intent": "BROWSE_CATEGORY" | "FIND_PRODUCT" | "GET_PRODUCT_INFO" | "CHECK_POLICY" | "GET_ADVICE" | "WORKFLOW_ACTION" | "HUMAN_ESCALATION" | "GREETING" | "UNKNOWN",
  "confidence_score": 0.95,
  "operation": "NEW_SEARCH",
  "category": "",
  "product_name": "",
  "followup": "no",
  "knowledge_domain": "product",
  "requested_knowledge_field": "none",
  "needs_customer_context": false,
  "needs_customer_history": false,
  "needs_product_context": false,
  "needs_graphrag": false,
  "needs_workflow_state": false
}'
WHERE tenant_id = 'tenant_inventaa_led_001'
  AND prompt_name = 'intent_system_prompt'
  AND language = 'en'
  AND status = 'active';
