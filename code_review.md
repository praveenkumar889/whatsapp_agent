# Code Review: WhatsApp AI Operations Platform for SMBs

## Review Summary

| Metric | Count |
|--------|-------|
| Total checks | 20 |
| ✅ Pass | 7 |
| ⚠️ Concern | 10 |
| ❌ Fail | 3 |
| **Overall readiness for multi-business deployment** | **Needs significant changes** |

---

## Detailed Findings

---

### BUSINESS AGNOSTICISM

---

### 1. Hardcoded Business Type / Industry Assumptions
**Status: ❌ Fail**

| File | Lines | Finding |
|------|-------|---------|
| [intent_router.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/intent_router.py#L24-L85) | 24–85 | System prompt is **saturated with LED lighting references**: "gate lights", "solar lights", "outdoor lights", "bollard lights", "divine lights", SKU examples like "10C-2012", "ALT20C". The entire intent classification mental model is built around a lighting distributor. |
| [config.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/config.py#L90-L112) | 90–112 | Default `PRODUCTS_API_URL` and `GRAPHRAG_API_URL` point to `inventaa-products-api.vercel.app` and `inventaa-graphrag.vercel.app` — hardcoded to a single client's infrastructure. |
| [entity_extractor.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/entity_extractor.py#L108-L157) | 108–157 | Entity extraction prompt examples are all product-goods oriented: "flood lights", "gate lights", "garden lights". No service or appointment examples. |
| [models/schemas.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/models/schemas.py#L41-L46) | 41–46 | Comment examples all reference Inventaa: `"LED Lighting Solutions"`, `"support@inventaa.in"`, `"inventaa@upi"`, `"Inventaa LED Innovation Pvt Ltd"`. While comments aren't logic, they reveal the codebase was designed for one client. |
| [negotiator.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/negotiator.py#L79) | 79 | Comment says "typically 5% for Inventaa" — reveals single-client design assumptions. |
| [main.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/main.py#L198) | 198 | Default GST rate comment: `"default 18% for LED lighting / standard goods"` |

**Risk:** Any non-lighting business (clinic, restaurant, service provider) will get intent misclassification because the LLM's few-shot examples are all about "gate lights" and SKU codes. A grocery store customer saying "I want 5kg rice" will confuse the classifier trained on LED product patterns.

**Recommendation:** Move the intent system prompt and entity extraction examples to a per-tenant configuration table. Each business should define its own intent examples, product vocabulary, and entity patterns. The current prompts should become the template for "product retail" businesses, not the universal default.

---

### 2. Intent Set Flexibility
**Status: ❌ Fail**

| File | Lines | Finding |
|------|-------|---------|
| [intent_router.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/intent_router.py#L22) | 22 | `VALID_INTENTS` is a globally hardcoded Python set: `{"WORKFLOW_ACTION", "FAQ_KNOWLEDGE", "HUMAN_ESCALATION", "GREETING", "UNKNOWN"}`. No per-tenant configuration. |
| [intent_router.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/intent_router.py#L24-L85) | 24–85 | The `SYSTEM_PROMPT` is a module-level f-string computed once at import time. It can never change per tenant — even `BUSINESS_NAME` is frozen at startup. |

**Risk:** A clinic cannot add `APPOINTMENT_BOOKING`. A logistics company cannot add `SHIPMENT_TRACKING`. A salon cannot add `SERVICE_BOOKING`. The entire downstream routing in [main.py:511-539](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/main.py#L511-L539) depends on these hardcoded strings. Adding a new intent requires a code deployment.

**Recommendation:** Store the intent set, system prompt, and few-shot examples in a `tenant_config` table. Load them per `tenant_id` at message processing time (with caching). The intent router should accept the prompt as a parameter rather than using a module-level constant.

---

### 3. Invoice/Billing Module Flexibility
**Status: ⚠️ Concern**

| File | Lines | Finding |
|------|-------|---------|
| [db/product_store.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/db/product_store.py#L210) | 210 | `total_with_gst = round(total_price * 1.18, 2)` — **GST rate hardcoded at 18%** in the order creation function, ignoring the tenant's configurable `gst_rate`. |
| [main.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/main.py#L199) | 199 | `gst_rate` is loaded from tenant config with fallback to 18%, which is good. But the order-creation path in `create_order()` bypasses this entirely. |
| [utils/invoice.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/utils/invoice.py#L241) | 241 | Invoice PDF displays `"GST (18%):"` — also hardcoded, doesn't use the tenant's `gst_rate`. |
| Entire codebase | — | No GSTIN, HSN code, CGST/SGST/IGST split, or state_code support anywhere. The "GST-compliant invoicing" described in the brief does not exist in the code. |

**Risk:** 
- Service businesses with 12% or 5% GST rates will be overcharged.  
- Inter-state orders require IGST instead of CGST+SGST — not implemented.
- The invoice PDF will fail any GST audit because it lacks: GSTIN numbers (buyer/seller), HSN/SAC codes, state codes, CGST/SGST/IGST breakdown.
- Service businesses (SAC codes, not HSN) have no path at all.

**Recommendation:** 
1. Fix the hardcoded `1.18` in `create_order()` to use the tenant's `gst_rate`.
2. Add `gstin`, `state_code`, `hsn_code` to the tenant and product schemas.
3. Implement CGST/SGST vs IGST logic based on buyer/seller state codes.
4. Add these fields to the invoice PDF template.

---

### MULTI-TENANCY CORRECTNESS

---

### 4. Database Query Scoping by tenant_id
**Status: ⚠️ Concern**

| File | Lines | Finding |
|------|-------|---------|
| [db/session_store.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/db/session_store.py#L101-L112) | 101–112 | `is_duplicate()` queries by `message_id` only — **no `tenant_id` filter**. If two tenants coincidentally process a message with the same `message_id` (unlikely but possible with Meta webhook retries across WABAs), one will be dropped. |
| [db/session_store.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/db/session_store.py#L155-L166) | 155–166 | `update_intent()` queries by `message_id` only — no `tenant_id`. |
| [db/processing_lock.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/db/processing_lock.py#L59-L71) | 59–71 | `release_lock()` deletes by `session_id` only — no `tenant_id`. If two tenants have customers with the same phone number (possible in multi-region), one tenant's lock could release the other's. |
| All other queries | — | Most queries correctly scope by `tenant_id` ✅ |

**Risk:** Subtle cross-tenant data leaks or race conditions in a multi-tenant environment. The probability is low (message IDs are UUIDs from Meta) but the pattern is architecturally unsafe.

**Recommendation:** Add `tenant_id` to `is_duplicate()`, `update_intent()`, and `release_lock()` filter clauses. This is a low-effort, high-safety improvement.

---

### 5. Redis Key Namespacing
**Status: ✅ Pass**

| File | Lines | Finding |
|------|-------|---------|
| Entire codebase | — | **No Redis is used.** Despite the brief mentioning Upstash Redis, the codebase uses Supabase PostgreSQL for all session management, caching, and deduplication. There is no `redis`, `upstash`, or cache import anywhere. |

**Risk:** None from Redis. However, using PostgreSQL for session locks and caching (which Redis would handle better) may become a scalability concern — see Check #18.

---

### 6. Supabase Storage Path Scoping
**Status: ✅ Pass**

| File | Lines | Finding |
|------|-------|---------|
| [utils/invoice.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/utils/invoice.py#L302) | 302 | Invoice PDFs stored at `invoices/{tenant_id}/{safe_id}.pdf` — properly scoped ✅ |
| [adapter/whatsapp_adapter.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/adapter/whatsapp_adapter.py#L102-L117) | 102–117 | Media files stored at `images/{filename}` and `audio/{filename}` — **NOT scoped by tenant**. Files are named by `{phone_number}_{timestamp}`, which is unique enough to avoid collisions but doesn't provide tenant isolation. |

**Risk:** Media files (customer images/audio) are in a flat namespace. Not a data leak (filenames are unique), but violates the principle of tenant isolation. Difficult to bulk-delete one tenant's data for GDPR compliance.

**Recommendation:** Change media storage paths to `images/{tenant_id}/{filename}` and `audio/{tenant_id}/{filename}`.

---

### CONFIGURATION FLEXIBILITY

---

### 7. Per-Business Configuration
**Status: ⚠️ Concern**

| Feature | Configurable? | Evidence |
|---------|--------------|----------|
| Product catalog | ⚠️ Partially | Tied to a single GraphRAG API endpoint per deployment. The `GRAPHRAG_API_URL` is a global config, not per-tenant. Each new business would need its own Neo4j/GraphRAG deployment. |
| Pricing | ✅ Yes | Prices come from product API / cache per tenant. |
| Payment method (UPI) | ✅ Yes | `upi_id` from tenants table → invoice. |
| Business hours | ❌ No | Not implemented anywhere. No concept of business hours, after-hours auto-replies, or SLA windows. |
| Escalation contacts | ⚠️ Partially | `support_email` from tenants table. But no owner phone number, no WhatsApp escalation number, no escalation routing logic. |
| FAQ / Knowledge base | ❌ No | No per-tenant FAQ/knowledge base. The `pgvector` mentioned in the brief is not used — all knowledge comes from the single GraphRAG API. |
| WhatsApp templates | ❌ No | All messages are free-form text via `send_whatsapp_reply()`. No template management, no template name configuration, no pre-approved template usage. |
| GST rate | ✅ Yes | `gst_rate` from tenants table (but ignored in `create_order()` — see Check #3). |

**Risk:** Onboarding a second business requires deploying a separate GraphRAG instance, which defeats the purpose of a multi-tenant SaaS.

**Recommendation:** Make `GRAPHRAG_API_URL` and `PRODUCTS_API_URL` per-tenant fields in the tenants table. Add business_hours, escalation_phone, and faq_entries tables.

---

### 8. Onboarding Parameterization
**Status: ⚠️ Concern**

| File | Lines | Finding |
|------|-------|---------|
| [db/session_store.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/db/session_store.py#L19-L67) | 19–67 | `resolve_tenant_id()` fetches: `tenant_id`, `biz_name`, `tagline`, `city`, `support_email`, `website`, `upi_id`, `account_name`, `timezone`, `region`, `language`. This is a good parameterized design ✅ |
| [config.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/config.py#L128-L132) | 128–132 | `APP_NAME` and `BUSINESS_NAME` are global env vars — not per-tenant. `BUSINESS_NAME` is injected into the AI system prompt at module load time, so **all tenants get the same business name in their LLM prompts**. |
| [adapter/whatsapp_adapter.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/adapter/whatsapp_adapter.py#L252-L282) | 252–282 | `send_whatsapp_reply()` and `send_whatsapp_image()` use a global `PHONE_NUMBER_ID` and `ACCESS_TOKEN`. **All tenants share the same WhatsApp sender number.** You cannot onboard a business with its own WhatsApp number without a code change. |

**Risk:** 
- `BUSINESS_NAME` in intent router system prompt is frozen at startup — multi-tenant LLM context is broken.
- WhatsApp sender is a singleton — only one phone number for all businesses. This is the most critical multi-tenancy gap.

**Recommendation:** 
1. Move `ACCESS_TOKEN`, `PHONE_NUMBER_ID`, and `WABA_ID` to the tenants table.
2. Pass tenant's credentials to `send_whatsapp_reply()` / `send_whatsapp_image()` rather than using global constants.
3. Load `BUSINESS_NAME` from the resolved tenant, not from config (this is partially done via `incoming.biz_name` but the intent router still uses the frozen config value).

---

### LANGUAGE & COMMUNICATION

---

### 9. Language Detection & Multilingual Support
**Status: ⚠️ Concern**

| File | Lines | Finding |
|------|-------|---------|
| [models/schemas.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/models/schemas.py#L27) | 27 | `language` field exists on `IncomingMessage` — good ✅ |
| [main.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/main.py#L191) | 191 | `incoming.language` is set from tenant config, but **never actually used** downstream. No LLM prompt says "reply in {language}". |
| [ai/intent_router.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/intent_router.py#L24-L85) | 24–85 | Intent examples are English-only. No Hindi, Hinglish, Tamil, or Telugu examples. |
| [ai/response_handlers.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/response_handlers.py) | All | All LLM system prompts are English-only. No language-aware prompt construction. |

**Risk:** The platform works in English by default (LLMs handle multilingual input reasonably well), but there is no intentional multilingual support. Adding a new language is not "just a config change" — it requires modifying every system prompt in every handler.

**Recommendation:** Add a `reply_language` instruction to all LLM system prompts: `"Reply in {incoming.language} language."` This single change would leverage the LLM's built-in multilingual capabilities.

---

### 10. WhatsApp Template Management
**Status: ⚠️ Concern**

| File | Lines | Finding |
|------|-------|---------|
| Entire codebase | — | No WhatsApp templates are used anywhere. All messages are sent as free-form text via `send_whatsapp_reply()`. There is no template name, template language, or template variable management. |

**Risk:** 
- WhatsApp Business API requires pre-approved templates for proactive (outbound-first) messages.
- Free-form text only works within the 24-hour customer service window.
- Payment reminders, order confirmations sent after 24 hours will fail silently.
- No template-based messages means no structured buttons, lists, or interactive elements.

**Recommendation:** Add a `templates` table with `(tenant_id, template_name, language, variables)` and a `send_whatsapp_template()` function for proactive messages.

---

### WORKFLOW ENGINE

---

### 11. Workflow Engine Decoupling
**Status: ❌ Fail**

| File | Lines | Finding |
|------|-------|---------|
| Entire codebase | — | The workflow is **tightly coupled to physical-goods ordering**: browse products → select → negotiate price → set quantity → confirm → generate invoice. There is no abstraction layer for different workflow types. |
| [ai/product_followup.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/product_followup.py) | All 1515 lines | This file alone is 82KB — the entire product selection, negotiation, and ordering workflow is one monolithic function chain. |
| [ai/negotiator.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/negotiator.py) | All 1391 lines | Price negotiation engine is deeply embedded. A clinic doesn't negotiate appointment prices. |
| [db/product_store.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/db/product_store.py#L36-L62) | 36–62 | `_is_sku()` enforces a specific SKU format (alphanumeric + dash, 4-15 chars). Service businesses don't have SKUs. |

**Risk:** 
- A clinic cannot use this for appointment booking — there's no concept of dates, time slots, or service providers.
- A logistics company cannot track shipments — the order model is product+quantity+price, not shipment+route+status.
- A restaurant cannot handle food orders with customizations.
- The workflow is not pluggable — you cannot swap the product-ordering workflow for a different business flow without rewriting the core pipeline.

**Recommendation:** Introduce a `WorkflowEngine` abstraction with pluggable workflow types: `ProductOrderWorkflow`, `AppointmentWorkflow`, `ServiceRequestWorkflow`. Each tenant's config would specify which workflow type to use.

---

### 12. Escalation/Human-Handoff Module
**Status: ⚠️ Concern**

| File | Lines | Finding |
|------|-------|---------|
| [ai/response_handlers.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/response_handlers.py#L25-L63) | 25–63 | `handle_escalation()` is reasonably generic — uses `incoming.biz_name` and `incoming.support_email` ✅. However, there is no actual escalation mechanism (no notification to owner, no session lock, no handoff protocol). |
| [main.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/main.py#L529-L535) | 529–535 | If an active negotiation exists, `HUMAN_ESCALATION` is **silently redirected** to the product handler. The customer's explicit request for a human agent is overridden. |

**Risk:** 
- The "Human handoff ready" claim from the brief is not implemented. The bot sends an empathy message but never actually notifies the business owner.
- Escalation triggers are not configurable per business.
- A frustrated customer asking for a human during an active negotiation is redirected back to the bot.

**Recommendation:** 
1. Don't suppress escalation when negotiation is active — a customer explicitly requesting a human should always be honored.
2. Add owner notification (WhatsApp message to owner's phone from tenants table).
3. Add session lock mechanism to prevent bot from replying during human handoff.

---

### ERROR HANDLING & EDGE CASES

---

### 13. Error & Edge Case Handling
**Status: ⚠️ Concern**

| Scenario | Status | Evidence |
|----------|--------|----------|
| Unknown `biz_id` message | ✅ Pass | [main.py:183-185](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/main.py#L183-L185) — silently skipped with log. |
| Customer not opted-in | ⚠️ Not handled | No opt-in check exists. WhatsApp API will return errors for non-opted-in users, but no graceful handling. |
| Inventory at zero | ⚠️ Not handled | No inventory management exists. Orders are created regardless of stock levels. |
| Duplicate payment webhook | ⚠️ Not applicable | No Razorpay integration exists in the codebase despite the brief. |
| Malformed LLM JSON | ✅ Pass | Every LLM call has `try/except json.JSONDecodeError` with safe fallbacks. See [intent_router.py:152-154](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/intent_router.py#L152-L154), [entity_extractor.py:306-308](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/entity_extractor.py#L306-L308). |

**Risk:** No inventory management means orders can be placed for out-of-stock items. No opt-in management means potential Meta API penalties.

---

### 14. Idempotency Logic Consistency
**Status: ⚠️ Concern**

| File | Lines | Finding |
|------|-------|---------|
| [db/session_store.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/db/session_store.py#L101-L112) | 101–112 | Dedup is on `message_id` only (not `biz_id:message_id` as stated in the brief). No `tenant_id` scoping. |
| [main.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/main.py#L137-L155) | 137–155 | The POST `/webhook` endpoint returns 200 immediately and processes async — good pattern for Meta's timeout ✅. But there is no Razorpay webhook endpoint, so the "Razorpay webhook dedup" from the brief is not implemented. |

**Risk:** Dedup key collision is theoretically possible across tenants (Meta message IDs are unique per WABA, but multiple WABAs could theoretically generate colliding IDs). Low probability but architecturally incorrect.

**Recommendation:** Change dedup key to composite `(tenant_id, message_id)`.

---

### SECURITY

---

### 15. Webhook Signature Verification
**Status: ❌ Concern — effectively Fail**

| File | Lines | Finding |
|------|-------|---------|
| [main.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/main.py#L120-L134) | 120–134 | GET `/webhook` verifies `hub.verify_token` during setup ✅. |
| [main.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/main.py#L137-L155) | 137–155 | POST `/webhook` does **no signature verification**. Meta sends an `X-Hub-Signature-256` HMAC header on every webhook — this is not checked. |
| Entire codebase | — | No HMAC, no `hashlib`, no signature verification anywhere. |

**Risk:** **Anyone who knows the webhook URL can inject fake messages.** An attacker could impersonate any customer, place fake orders, or extract product/pricing data. This is a critical security vulnerability.

**Recommendation:** Verify `X-Hub-Signature-256` on every POST webhook using the app secret. This is a Meta Cloud API requirement for production apps.

---

### 16. PII Logging
**Status: ⚠️ Concern**

| File | Lines | Finding |
|------|-------|---------|
| [main.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/main.py#L202) | 202 | `print(f"[{incoming.trace_id}] {incoming.sender_name} ({incoming.sender_phone})")` — **customer name and phone number logged in plaintext** on every message. |
| [adapter/whatsapp_adapter.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/adapter/whatsapp_adapter.py#L264) | 264 | `print(f"[WHATSAPP] Reply sent to {to}")` — customer phone number logged. |
| [main.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/main.py#L184) | 184 | `phone_number_id` logged on unknown tenant. |
| All files | — | All logging uses `print()` — no structured logging, no log levels, no PII masking. |

**Risk:** PII in server logs violates GDPR/privacy best practices. If Render.com logs are compromised, all customer phone numbers and names are exposed. India's DPDP Act 2023 also has strict PII handling requirements.

**Recommendation:** Mask PII in logs (show last 4 digits of phone numbers). Replace `print()` with structured logging (`logging` module) with configurable log levels.

---

### 17. API Key / Secret Hardcoding
**Status: ✅ Pass**

| File | Lines | Finding |
|------|-------|---------|
| [config.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/config.py) | All | All secrets loaded from environment variables via `os.getenv()` ✅ |
| [.gitignore](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/.gitignore#L13) | 13 | `.env` is in `.gitignore` ✅ |

**Risk:** Low. Configuration pattern is correct.

---

### SCALABILITY READINESS

---

### 18. N+1 Query Patterns
**Status: ⚠️ Concern**

| File | Lines | Finding |
|------|-------|---------|
| [db/session_store.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/db/session_store.py#L622-L649) | 622–649 | `get_cached_product_by_name()` fetches **ALL cached products for the entire tenant** (`SELECT * WHERE tenant_id = X`), then loops through them in Python to find a name match. This is O(n) in the number of cached products. |
| [ai/graphrag_handler.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/graphrag_handler.py#L115-L156) | 115–156 | `_send_structured_product_list()` sends images sequentially — each image is a separate HTTP request to Meta API. For 3 products, that's 3 sequential HTTP round-trips. |
| [db/session_store.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/db/session_store.py#L500-L560) | 500–560 | `save_product_api_responses_batch()` is well-optimized with batch upsert ✅ and has a per-row fallback ✅. |

**Risk:** `get_cached_product_by_name()` will degrade linearly as the product catalog grows (100+ products per tenant × 50+ tenants = 5000+ rows scanned per call).

**Recommendation:** Add a `product_name` column to the `product_cache` table with a composite index `(tenant_id, product_name)` for direct lookup instead of the current scan-all-then-filter pattern.

---

### 19. Classifier Model Loading
**Status: ✅ Pass**

| File | Lines | Finding |
|------|-------|---------|
| Entire codebase | — | The platform uses **Azure OpenAI API calls** (not a local classifier model). There is no `sentence-transformers` or any local model loading. The `AzureOpenAI` client is initialized once per module as a module-level singleton (e.g., [intent_router.py:16-20](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/intent_router.py#L16-L20)). |

**Risk:** None for model loading. However, this means the "classifier-first at ₹0 LLM cost" claim from the brief is incorrect — **every single message makes at least one paid Azure OpenAI API call** for intent classification, and most messages make 3-8 LLM calls across the pipeline.

> [!WARNING]
> The architecture described in the brief (sentence-transformers for 80% at zero cost, Claude Haiku for 20%) does NOT match the implementation. Every message goes through Azure OpenAI GPT-4.1, which is a paid API. The per-message cost is significantly higher than described.

---

### 20. Synchronous Blocking Calls
**Status: ⚠️ Concern**

| File | Lines | Finding |
|------|-------|---------|
| All `_client.chat.completions.create()` calls | Throughout | The `AzureOpenAI` client uses the **synchronous SDK** (`openai.AzureOpenAI`, not `openai.AsyncAzureOpenAI`). Every LLM call blocks the event loop thread. |
| [main.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/main.py#L389) | 389 | One instance uses `run_in_executor()` to avoid blocking — but this is the exception, not the rule. The other ~30 LLM calls across the codebase block directly. |
| All Supabase calls | Throughout | The `supabase-py` client is synchronous. All database calls block the event loop despite being in `async` functions. |

**Risk:** FastAPI runs on an asyncio event loop. Synchronous blocking calls from the OpenAI and Supabase clients hold the event loop hostage during I/O. With the semaphore limited to 50 concurrent tasks, **50 simultaneous slow LLM calls (30s timeout) would freeze the entire server** — no new messages would be processed until one completes.

**Recommendation:** 
1. Switch to `openai.AsyncAzureOpenAI` for all LLM calls.
2. Use the async Supabase client (`supabase.create_async_client`).
3. Or wrap all synchronous calls in `run_in_executor()` consistently.

---

## Multiple Supabase Client Instances

> [!NOTE]
> Six separate Supabase client singletons exist across the codebase: one each in [session_store.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/db/session_store.py#L10-L16), [product_store.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/db/product_store.py#L20-L26), [processing_lock.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/db/processing_lock.py#L24-L30), [workflow_state.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/db/workflow_state.py#L26-L32), [whatsapp_adapter.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/adapter/whatsapp_adapter.py#L19-L25), and [invoice.py](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/utils/invoice.py#L30-L36). Similarly, five separate `AzureOpenAI` client singletons exist across the AI modules. This wastes connection pool slots and makes it harder to manage connection lifecycle.

---

## Top 3 Blockers for Multi-Business Readiness

### 🚫 Blocker 1: Single WhatsApp Sender Number (Architectural)
**Files:** [adapter/whatsapp_adapter.py:252-282](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/adapter/whatsapp_adapter.py#L252-L282), [config.py:29-43](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/config.py#L29-L43)

`PHONE_NUMBER_ID`, `ACCESS_TOKEN`, and `WABA_ID` are global singletons. Every outbound message goes through the same WhatsApp phone number. You **cannot onboard a second business** because messages from Business B's customers would be replied to from Business A's WhatsApp number. This requires moving WhatsApp credentials to the tenants table and passing them through the entire send pipeline.

### 🚫 Blocker 2: Hardcoded Intent System & Single-Client LLM Prompts (Functional)
**Files:** [ai/intent_router.py:22-85](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/ai/intent_router.py#L22-L85), [config.py:131-132](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/config.py#L131-L132)

The intent set is a global Python constant. The system prompt is a module-level f-string frozen at import time with Inventaa-specific examples (gate lights, solar lights, SKU codes). The `BUSINESS_NAME` in the prompt is a global env var, not per-tenant. A second business of any type will get misclassified intents because the classifier's mental model is "LED lighting distributor."

### 🚫 Blocker 3: No Webhook Signature Verification (Security)
**Files:** [main.py:137-155](file:///Users/apple/Documents/projects/AISMBs/whatsapp-bot/main.py#L137-L155)

POST webhooks accept any request without verifying Meta's `X-Hub-Signature-256` HMAC. Before onboarding any paying customer, this security hole must be closed — a forged webhook could create fake orders or extract pricing data.

---

## Business Types This Codebase Currently Supports Well

| Business Type | Why It Works |
|---|---|
| **LED lighting distributors / Inventaa specifically** | The codebase was built for this exact business. All prompts, SKU formats, pricing, negotiation, and product catalog flows are designed for it. |
| **Similar physical goods e-commerce** (electronics, home appliances) with a single product catalog API | If the GraphRAG/Products API can be pointed at a different catalog, the flow (browse → select → negotiate → order → invoice) would work. |

---

## Business Types That Require Configuration or Minor Code Changes

| Business Type | What Needs to Change |
|---|---|
| **Retail stores** (fashion, grocery, hardware) | Update intent examples to remove LED-specific language. Remove SKU format enforcement in `_is_sku()`. Adjust entity extraction examples. |
| **Wholesale distributors** (non-lighting) | Same as retail + ensure negotiation floor percentages are configurable per tenant (currently they are, via `global_offers`). |
| **E-commerce with existing product API** | Point `PRODUCTS_API_URL` and `GRAPHRAG_API_URL` to the new API. Ensure response format matches expected schema. |

---

## Business Types That Are Currently Incompatible

| Business Type | Why It's Incompatible |
|---|---|
| **Clinics / Healthcare** | No appointment booking, time slot management, doctor/provider model, or patient record support. The entire workflow assumes physical goods. |
| **Service businesses** (plumbing, salon, consulting) | No service catalog, no booking workflow, no service delivery tracking. SKU-based product model doesn't apply. Invoice model doesn't support SAC codes. |
| **Restaurants / Food delivery** | No menu customization (toppings, sizes), no delivery time estimation, no kitchen order management. |
| **Logistics / Shipping** | No shipment tracking, no route management, no status updates. Order model is product-quantity-price, not shipment-origin-destination. |
| **Subscription businesses** (SaaS, gym, classes) | No recurring billing, no membership management, no subscription lifecycle. |
| **Real estate / Property** | No property listing, no viewing scheduling, no EMI calculator. |

---

## Architecture vs. Brief Comparison

> [!IMPORTANT]
> Several features described in the brief are **not implemented** in the codebase:
> 
> | Brief Claim | Actual Status |
> |---|---|
> | sentence-transformers/all-MiniLM-L6-v2 classifier | ❌ Not present — uses Azure OpenAI GPT-4.1 for everything |
> | Claude Haiku for complex queries | ❌ Not present — uses Azure OpenAI only |
> | Upstash Redis for cache/session | ❌ Not present — uses Supabase PostgreSQL |
> | pgvector for FAQ/knowledge base | ❌ Not present — uses external GraphRAG API |
> | Razorpay payment integration | ❌ Not present — only UPI ID on invoice PDF |
> | Supabase RLS policies | ⚠️ Cannot verify from code (RLS is configured in Supabase dashboard) |
> | Classifier-first at ₹0 cost (80%) | ❌ Every message makes paid GPT-4.1 API calls |
> | CGST/SGST/IGST, GSTIN, HSN codes | ❌ Not implemented |
> | Idempotent webhooks on biz_id:message_id | ⚠️ Dedup on message_id only, no biz_id |
