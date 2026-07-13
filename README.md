# WhatsApp AI Agent

A production-ready, multi-tenant WhatsApp AI sales agent built on FastAPI, Azure OpenAI, and Supabase. It handles product discovery via a GraphRAG (graph-based retrieval-augmented generation) catalog, end-to-end order flow, price negotiation, invoice generation, and customer escalation — all fully configurable per business tenant through a database, with zero hardcoded business logic.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Project Structure](#project-structure)
3. [Pipeline Flow](#pipeline-flow)
4. [File-by-File Reference](#file-by-file-reference)
   - [Entry Point](#entry-point--mainpy)
   - [Config](#config--configpy)
   - [Models](#models--modelsschemasspy)
   - [Adapter](#adapter--adapterwhatsapp_adapterpy)
   - [Pipeline](#pipeline)
   - [AI Handlers](#ai-handlers)
   - [Database Layer](#database-layer)
   - [Messaging](#messaging--messagingsenderpy)
   - [Utils](#utils)
   - [Migrations](#migrations)
   - [Interface](#interface-streamlit)
5. [Multi-Tenant System](#multi-tenant-system)
6. [Prompt System](#prompt-system)
7. [Negotiation State Machine](#negotiation-state-machine)
8. [Environment Variables](#environment-variables)
9. [Database Tables](#database-tables)
10. [Adding a New Client](#adding-a-new-client)
11. [Latency Optimisations](#latency-optimisations)

---

## Architecture Overview

```
WhatsApp / Meta Cloud API
         │
         ▼
   main.py (FastAPI)
         │
         ▼
  pipeline/setup.py          ← Tenant resolve, dedup, lock, history, neg_state (parallel)
         │
         ▼
  ai/handlers.py             ← Intent classification (Azure OpenAI GPT-4)
         │
         ▼
  pipeline/router.py         ← Negotiation guard → Invoice guard → Intent dispatch
         │
    ┌────┴──────────────────────────┐
    ▼                               ▼
ai/graphrag_handler.py       ai/handlers.py
(product search / orders)    (greeting / escalation / unknown)
    │
    ▼
GraphRAG API (Neo4j)          ← External; per-tenant endpoint
    │
    ▼
messaging/sender.py           ← Meta Graph API / mock channel
    │
    ▼
db/session_store.py           ← Supabase PostgreSQL (audit, history, state)
```

**Key design principles:**
- **Orchestrator pattern** — `main.py` contains zero business logic; all logic is in specialist modules
- **Multi-tenant** — every prompt, config value, and keyword list is stored in the database per tenant
- **No hardcoding** — business names, prices, negotiation floors, and reply text all come from DB
- **Async-first** — all I/O is async; setup DB calls are parallelised with `asyncio.gather`
- **Fire-and-forget** — non-critical DB writes (cache saves, audit logs) never block the reply path

---

## Project Structure

```
whatsapp-bot/
│
├── main.py                        ← FastAPI app, 8-step pipeline orchestrator
├── config.py                      ← Environment variable loader
├── requirements.txt
│
├── models/
│   └── schemas.py                 ← IncomingMessage, IntentResult, OrderItem, EntityResult, RoutingDecision
│
├── adapter/
│   └── whatsapp_adapter.py        ← Meta webhook → IncomingMessage; outbound API calls
│
├── pipeline/
│   ├── setup.py                   ← Steps 1-5: tenant, dedup, lock, history, save (parallel)
│   └── router.py                  ← Intent dispatch + negotiation/invoice guards
│
├── ai/
│   ├── handlers.py                ← classify_intent, handle_greeting, handle_escalation, handle_unknown
│   ├── graphrag_handler.py        ← GraphRAG API integration, product list rendering
│   ├── negotiator.py              ← Price negotiation state machine
│   ├── invoice_handler.py         ← Order confirmation, invoice generation, inquiry detection
│   ├── order_service.py           ← Order creation and DB persistence
│   ├── product_followup.py        ← Follow-up parsing (qty, comparison, new search)
│   ├── context_builder.py         ← PromptContext assembly from DB for each request
│   ├── customer_history_handler.py← Past order / offer history queries
│   ├── product_context_resolver.py← Waterfall product name resolution
│   ├── graphrag_request_builder.py← GraphRAG query enrichment with customer context
│   ├── knowledge_accessor.py      ← Structured knowledge (specs, installation URLs)
│   ├── customer_profile_updater.py← Customer profile update after orders
│   ├── request_context.py         ← AIRequestContext + PromptContext dataclasses
│   ├── pricing.py                 ← PricingResult — single source of truth for pricing
│   ├── prompt_builder.py          ← Prompt construction utilities
│   ├── orchestrator.py            ← Workflow orchestration helpers
│   └── perf_metrics.py            ← Performance tracking
│
├── db/
│   ├── session_store.py           ← All Supabase DB operations (messages, history, state)
│   ├── product_store.py           ← Product lookup, price fetch, order creation
│   ├── prompt_store.py            ← Prompt template cache (5-min TTL) + DB resolution
│   ├── processing_lock.py         ← Distributed session locks (one message at a time)
│   ├── workflow_state.py          ← Pending order state management
│   └── customer_data_service.py   ← Order history, preferences, negotiation outcomes
│
├── messaging/
│   └── sender.py                  ← send_reply / send_image (WhatsApp + mock channel)
│
├── utils/
│   ├── conversation_actions.py    ← Quick-action phrase matching (ORDER_CONFIRM, INVOICE_INQUIRY)
│   ├── alerting.py                ← Slack webhook alerts for pipeline errors
│   └── invoice.py                 ← PDF invoice generation
│
├── interface/                     ← Streamlit testing UI (optional)
│   ├── app.py
│   ├── api.py
│   ├── components/
│   └── styles/
│
└── migrations/                    ← SQL migrations (run in order)
    ├── 001_dynamic_prompts.sql
    ├── 002_quick_actions.sql
    ├── 005_custom_dynamic_prompts.sql
    ├── 006_structured_customer_data.sql
    ├── 007_update_intent_prompt.sql
    ├── 008_tenant_configurations.sql
    ├── 009_update_ask_quantity_prompt.sql
    └── 010_invoice_keyword_shortcuts.sql
```

---

## Pipeline Flow

Every customer message travels through these 8 steps in order:

```
Step 1    Parse webhook         adapter/whatsapp_adapter.py   → IncomingMessage
Step 2    Setup pipeline        pipeline/setup.py              → tenant, dedup, lock, history, neg_state
Step 3    Classify intent       ai/handlers.py                 → IntentResult + RoutingDecision
Step 4    Update intent in DB   db/session_store.py            → audit log
Step 4.5  Build AIRequestContext ai/request_context.py         → cached on incoming._cached_arc
Step 5    Dispatch              pipeline/router.py              → neg guard → invoice guard → handler
Step 6    Send reply            messaging/sender.py             → Meta API / mock
Step 7    Store reply           db/session_store.py             → update_reply + save_outbound (parallel)
Step 8    Release lock          db/processing_lock.py           → session unlocked
```

### Parallel optimisations in the setup phase

```
resolve_tenant_id()                       ← sequential (provides tenant_id)
    │
    ├── [asyncio.gather]
    │   ├── is_duplicate()
    │   ├── get_session_history()
    │   └── _resolve_quoted_caption()     ← all three run in parallel
    │
acquire_lock()                            ← sequential (after dedup passes)
    │
    ├── [asyncio.gather]
    │   ├── get_negotiation_state()
    │   └── save_message()                ← both run in parallel
```

---

## File-by-File Reference

---

### Entry Point — `main.py`

FastAPI application and pipeline orchestrator. Contains zero business logic — delegates everything to specialist modules.

**Endpoints:**

| Endpoint | Method | Purpose |
|---|---|---|
| `/webhook` | GET | Meta webhook verification (runs once during setup) |
| `/webhook` | POST | Receives live WhatsApp messages from Meta Cloud API |
| `/chat` | POST | Streamlit / web testing — constructs IncomingMessage directly |
| `/reset` | POST | Clears all session data for a phone number (dev/testing) |
| `/health` | GET | Uptime check — returns `{"status": "ok"}` |

**`run_pipeline(incoming)`** is the core 8-step orchestrator:
- Calls `setup_pipeline()` to receive `(ok, session_history, neg_state)`
- Checks for active negotiation → applies **NEG BYPASS** (skips intent LLM entirely, saves 2.5s)
- Calls `classify_intent()` for all other messages
- Calls `dispatch()` to route to the correct handler
- Sends reply via `_send_reply_chunked()` (splits messages over 4000 chars)
- Writes reply to DB using `asyncio.gather` (parallel writes)
- Always releases the processing lock in a `finally` block

**Concurrency guard:** `asyncio.Semaphore(50)` — maximum 50 simultaneous pipeline runs per instance prevents resource exhaustion.

---

### Config — `config.py`

Single-file environment variable loader using `python-dotenv`. All configuration is centralised here; no other file reads `os.getenv` directly.

| Group | Variables |
|---|---|
| WhatsApp / Meta | `PHONE_NUMBER_ID`, `WABA_ID`, `ACCESS_TOKEN`, `VERIFY_TOKEN`, `WEBHOOK_SECRET` |
| Azure OpenAI | `AZURE_AI_ENDPOINT`, `AZURE_AI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_AI_API_VERSION` |
| Supabase | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_STORAGE_BUCKET` |
| GraphRAG | `GRAPHRAG_API_URL`, `PRODUCTS_API_URL` |
| Application | `APP_NAME`, `BUSINESS_NAME`, `APP_ENV` |
| Alerting | `SLACK_WEBHOOK_URL` |

---

### Models — `models/schemas.py`

Pure data containers (Python dataclasses) with no business logic. Used across the entire pipeline.

**`IncomingMessage`** — the central data object that flows through every step. Fields are populated progressively:
- At parse time: `trace_id`, `message_id`, `session_id`, `channel`, `text`, `sender_name`, `sender_phone`
- After tenant resolution: `tenant_id`, `biz_name`, `gst_rate`, all prompt columns, `quick_actions`
- At runtime: `_cached_neg_state`, `_cached_arc`, `_routing`, `resolved_product`

**`RoutingDecision`** — computed by the intent classifier alongside the intent label. Tells the router exactly which subsystems a message needs (GraphRAG, workflow state, customer context, knowledge field) without any keyword matching. Keeps routing semantic and tenant-agnostic.

**`IntentResult`** — output of `classify_intent()`. Carries `intent`, `confidence_score`, and an optional `RoutingDecision`.

**`OrderItem`** / **`EntityResult`** — represent what the customer wants to order: product name, quantity, delivery date. `EntityResult` supports multi-product orders.

---

### Adapter — `adapter/whatsapp_adapter.py`

Translates raw Meta Cloud API webhook JSON into a clean, platform-neutral `IncomingMessage`. Also owns all outbound Meta API calls.

**Inbound — `parse_webhook(data)`:**

| Message type | Handling |
|---|---|
| `text` | Extracts body directly — no HTTP calls |
| `image` | Downloads binary from Meta (two-step signed URL), uploads to Supabase Storage, sets text marker |
| `audio` | Same flow as image |
| Delivery/read receipts | Returns `None` — skipped silently |
| Stickers, reactions, documents | Returns `None` — not in scope |

For image and audio, the text field is set to an internal marker (`__MEDIA_UNSUPPORTED_IMAGE__`) because `tenant_id` is not yet resolved at parse time. `pipeline/setup.py` replaces this marker with the tenant's own DB-driven response after resolving the tenant.

**Media storage:** Images and audio are uploaded to Supabase Storage under `{tenant_id}/{folder}/{filename}` — Meta's temporary download URLs expire, so permanent storage is required.

**Outbound:**
- `send_whatsapp_reply(to, message, phone_number_id, access_token)` — text via Meta Graph API v21.0; uses per-tenant credentials when available, falls back to global env vars
- `send_whatsapp_image(to, image_url, caption, ...)` — image card via Meta Graph API

---

### Pipeline

#### `pipeline/setup.py`

Runs the first five steps before any business logic. Returns `(True, session_history, neg_state)` on success or `(False, None, None)` to abort.

**Functions:**

| Function | Purpose |
|---|---|
| `setup_pipeline()` | Orchestrates steps 1-5; owns the lock invariant |
| `_apply_tenant()` | Copies every tenants-table column onto `incoming`; normalises `quick_actions` to `frozenset` at load time |
| `_resolve_media_unsupported_marker()` | Replaces internal media markers with the tenant's DB prompt |
| `_log_incoming()` | Logs masked sender info for observability |
| `_resolve_quoted_caption()` | Fetches quoted message text from DB, prepends to `incoming.text` |

**Lock invariant:** If `setup_pipeline` returns `(True, ...)`, the caller owns the processing lock and must release it in a `finally` block. If it returns `(False, ...)` or raises, no lock is held.

**`PROMPT_COLUMNS`** — list of legacy prompt column names that are loaded from the `tenants` table. Deprecating as tenants migrate to `prompt_templates`.

---

#### `pipeline/router.py`

Dispatches each message to the right handler after two pre-flight guards. Contains all routing logic including the three phases of the negotiation state machine.

**`dispatch(incoming, result, session_history)`:**

1. **Dynamic Knowledge Intercept** — if `RoutingDecision.requested_knowledge_field` is set (e.g. `"installation"`), fetches the URL/asset directly from product cache or GraphRAG without going through main routing
2. **Guard 1 — `_neg_guard()`** — handles all three negotiation phases
3. **Guard 2 — `_invoice_guard()`** — handles invoice requests and order confirmations
4. **Main routing** — switches on `result.intent` → GREETING, HUMAN_ESCALATION, FAQ_KNOWLEDGE/WORKFLOW_ACTION, UNKNOWN

**Negotiation phases (inside `_neg_guard`):**

| Phase | State flags | What happens |
|---|---|---|
| Negotiating | `rounds > 0`, `counter_offer_presented = False` | Normal negotiation re-entry via `handle_negotiation()` |
| Counter offer presented | `counter_offer_presented = True` | `detect_acceptance()` LLM on each reply; holds price if not accepted |
| Awaiting confirmation | `awaiting_invoice_confirmation = True` | Handles qty changes; confirms order on "Confirm" |

**`_invoice_guard()`** — uses a DB-driven keyword shortcut (`INVOICE_INQUIRY` list in `quick_actions`) to skip the LLM call on non-invoice messages. Only calls `_is_invoice_inquiry()` if the message contains at least one keyword. Saves ~2.5s on every product search and negotiation message.

**`_has_invoice_keyword(incoming)`** — reads `incoming.quick_actions["INVOICE_INQUIRY"]` (a `frozenset`) for an O(n) keyword scan. Returns `True` (run LLM) if the key is absent — safe default for unconfigured tenants.

**`_check_fast_confirm()`** — tries `is_quick_confirm()` (instant frozenset lookup) first; only falls back to an LLM call if the phrase is not in the tenant's `ORDER_CONFIRM` list.

---

### AI Handlers

#### `ai/handlers.py`

All LLM-backed intent handlers. Every prompt is fetched from the DB via `get_prompt()` — no prompt strings exist in this file.

| Function | LLM call | Prompt key | Notes |
|---|---|---|---|
| `classify_intent()` | Yes | `intent_system_prompt` | Returns `IntentResult` + `RoutingDecision`; parses JSON response |
| `handle_greeting()` | Yes | `greeting_system_prompt` | Time-aware (morning/afternoon/evening) via `ZoneInfo` |
| `handle_escalation()` | Yes | `escalation_prompt` | Connects customer to support contact |
| `handle_unknown()` | Yes | `unknown_prompt` | Fallback for low-confidence or unrecognised messages |

All LLM calls use `asyncio.get_event_loop().run_in_executor(None, lambda: ...)` to run the synchronous Azure OpenAI client without blocking the async event loop.

---

#### `ai/graphrag_handler.py`

Calls the external GraphRAG API (Neo4j-backed hybrid RAG) for all product catalog queries. The primary latency item in the pipeline — the external API takes 8-15 seconds.

**`call_graphrag_api(incoming, session_history, graphrag_url)`:**
1. Checks for product follow-up (`_try_resolve_product_followup`) — handles "tell me more about that" / "I want 3" without hitting GraphRAG
2. Resolves any saved category selection from a previous clarification
3. Enriches the query with customer context via `_build_enriched_graphrag_query()` (LLM call, only fires when context exists)
4. Builds the payload matching the messages table schema
5. Makes HTTP POST to the effective GraphRAG URL (per-tenant DB value takes priority over global env var)
6. Handles three response shapes: structured product list → `needs_clarification` dict → plain text
7. On short error reply (≤100 chars), retries once with simplified keywords

**`_send_structured_product_list(incoming, products)`:**
- Builds product cache items (sync, no I/O)
- Fires all three cache saves as `asyncio.create_task()` — non-blocking, for future requests
- Sends image cards in **parallel** via `asyncio.gather(_send_product_card(...) × N)`
- Returns numbered text summary

**`_send_product_card(incoming, position, p)`:** Sends one image/text card. Its `save_outbound_message` is also fire-and-forget.

**`_build_enriched_graphrag_query()`:** LLM call that rewrites the customer's raw query to include purchase history and currently discussed product. Only fires when context is available.

**`_resolve_category_from_message()`:** Three-tier matching — index pick → string match → LLM semantic fallback.

---

#### `ai/negotiator.py`

Price negotiation engine implementing the full state machine using DB-driven prompts and per-tenant floor prices.

Key functions:
- `handle_negotiation()` — main entry: decides whether to make first offer, counter, or hold final price based on `negotiation_state.rounds`
- `detect_acceptance()` — LLM check: did the customer accept? (prompt: `neg_detect_accept_prompt`)
- `detect_quantity_change()` — LLM check: did the customer change quantity? (prompt: `neg_extract_qty_prompt`)
- `parse_global_offer_tiers()` — parses the `global_offers` text into structured discount tiers; result cached in DB to avoid repeated LLM calls

**Floor price:** Calculated from `neg_floor_disc_pct` (max discount %) and `neg_floor_multiplier` (minimum price as multiple of list price) — both from the tenant's row in the `tenants` table. Never hardcoded.

---

#### `ai/invoice_handler.py`

Manages all invoice-related interactions.

- `handle_invoice_request(incoming, negotiated_order)` — looks up the latest confirmed order and generates a PDF
- `_is_invoice_inquiry(incoming)` — LLM check: is the customer asking for their invoice? (`invoice_inquiry_check_prompt`)
- `_is_invoice_confirmation_request(incoming, session_history)` — LLM check: is the customer confirming the order? (`invoice_confirmation_request_check_prompt`)

Invoice generation delegates to `utils/invoice.py`, uploads the PDF to Supabase Storage, and returns a download link.

---

#### `ai/product_followup.py`

Pre-GraphRAG check that resolves follow-up messages about previously shown products without making a new catalog API call — the biggest single latency win for repeat queries.

**`_try_resolve_product_followup(incoming, session_history)`:**
- Loads the previous product selection from `workflow_sessions`
- Runs `pf_data_extraction_prompt` LLM to extract: quantity, is comparison, is new search, selected product name
- If comparison → generates a side-by-side product summary
- If new search → falls through to GraphRAG
- If quantity + active negotiation → re-enters the negotiator

Returns `"__ALREADY_HANDLED__"` when an image or installation URL was sent directly to avoid double-sending.

---

#### `ai/context_builder.py`

Assembles `PromptContext` for each request by fetching structured data from the DB. Called once per message by `router.py::dispatch()` before handing context to downstream handlers.

Loads in parallel: customer order history, active negotiation context, last discussed product, tenant offers. Populates `arc.llm_context` which is read by `graphrag_handler.py` and `prompt_builder.py`.

---

#### `ai/request_context.py`

**`AIRequestContext`** — structured context object cached on `incoming._cached_arc`. Carries `incoming`, `result`, `session_history`, `neg_state`, `resolved_product`, `customer_context`, and `llm_context`. Passed by reference to all downstream handlers — eliminates repeated DB fetches across the request.

---

#### `ai/pricing.py`

**`PricingResult`** — single source of truth for all pricing calculations. Every handler that touches money (negotiator, order summary, invoice, DB writes) reads from here rather than recalculating independently.

Fields: `regular_unit_price`, `store_discount_pct`, `auto_offer_unit_price`, `negotiated_unit_price`, `quantity`, `subtotal`, `gst_amount`, `total_payable`, `store_savings`, `negotiation_savings`, `total_savings`.

`to_whatsapp_summary()` — renders the order summary message using DB prompts.
`to_invoice_fields()` — serialises pricing for the `orders` table row.

---

#### `ai/order_service.py`

**`complete_order(tenant_id, session_id, sender_name, items, gst_rate, extra_fields)`** — creates a confirmed order in `orders` + `order_items`. Called from `router.py::_confirm_negotiated_order()` after the customer confirms.

---

#### `ai/customer_history_handler.py`

Handles messages that need past order or offer context: "show my orders", "what did I buy last time".

Uses `memory_query_classifier_prompt` to classify the query type, then fetches structured data from `customer_data_service.py` and renders a reply with DB prompts.

---

#### `ai/product_context_resolver.py`

Waterfall resolution of the "currently relevant product" — needed when a customer says "I want 5 of those" without naming the product:

1. Active negotiation state → `neg_state["product_name"]`
2. Last discussed product from `workflow_sessions`
3. Latest ordered product from `orders`

---

#### `ai/graphrag_request_builder.py`

Builds the enriched payload for GraphRAG API calls. Adds customer context (past orders, preferences), resolves pronouns ("it", "that"), and determines query type (`NEW_SEARCH` vs `PRODUCT_SEARCH`).

---

#### `ai/knowledge_accessor.py`

Retrieves structured knowledge assets (installation URL, warranty PDF, spec sheet) from the product cache. Returns a typed `{"type": "document"|"image"|"text", "value": ...}` dict that `router.py` uses to deliver assets directly.

---

#### `ai/perf_metrics.py`

Lightweight per-step timing. Logs `[TIMING]` lines to stdout for profiling without adding external dependencies.

---

### Database Layer

#### `db/session_store.py`

The primary Supabase client and all database operations. Every function is `async`.

Key operations:

| Function | Table | Purpose |
|---|---|---|
| `resolve_tenant_id(phone_number_id)` | `tenants` | Loads full tenant profile for a phone number ID |
| `is_duplicate(message_id, tenant_id)` | `messages` | Idempotency check — prevents double-processing |
| `get_session_history(tenant_id, session_id, limit)` | `messages` | Last N conversation turns as LLM context |
| `save_message(incoming)` | `messages` | Persists inbound message before processing |
| `update_reply(message_id, reply, ...)` | `messages` | Stores the bot's reply text and timestamp |
| `save_outbound_message(...)` | `messages` | Logs each outbound message (text and images) |
| `update_intent(message_id, intent, confidence)` | `messages` | Audit: stores intent classification result |
| `get_negotiation_state(tenant_id, session_id)` | `workflow_sessions` | Loads active negotiation state |
| `save_negotiation_state(...)` | `workflow_sessions` | Persists updated negotiation state |
| `clear_negotiation_state(...)` | `workflow_sessions` | Clears state after order confirmed |
| `get_graphrag_product_selection(...)` | `workflow_sessions` | Loads previous product list for follow-up |
| `save_graphrag_product_selection(...)` | `workflow_sessions` | Caches product list for follow-up parsing |
| `save_product_api_responses_batch(...)` | `product_cache` | Bulk-caches products from GraphRAG response |
| `get_cached_product_by_name(...)` | `product_cache` | Looks up a product for context or pricing |
| `get_tenant_offers(tenant_id)` | `tenant_offers` | Loads global offers text for the negotiator |
| `save_tenant_offers(...)` | `tenant_offers` | Caches offers extracted from GraphRAG response |
| `get_last_discussed_product(...)` | `workflow_sessions` | Context for pronoun resolution |
| `save_last_discussed_product(...)` | `workflow_sessions` | Saves single-product context |
| `clear_post_order_context(...)` | `workflow_sessions` | Clears stale state after order placement |
| `get_latest_graphrag_response(...)` | `messages` | Fetches last raw GraphRAG response for asset lookups |

---

#### `db/prompt_store.py`

Three-layer prompt resolution system with 5-minute in-memory cache.

**Resolution order:**
1. **Cache** (`_CACHE` dict, 5-min TTL) — zero DB calls on warm cache
2. **DB** (`prompt_templates` table — primary source for all 80+ prompts)
3. **Legacy** (`IncomingMessage` attribute from `tenants` columns — deprecating)

**`get_prompt(incoming, key, **template_vars)`** — public API. Loads template via `_PromptLoader`, renders variables via `_PromptRenderer`.

**`_PromptRenderer.render()`** — uses `str.replace()` not `str.format_map()`. Intentional: prompt text contains literal JSON like `{"type": "X"}` which `format_map` would crash on. Only explicit kwargs are substituted.

**`update_prompt_template()`** — versioned prompt update: archives the current active record, inserts a new version, invalidates cache. Safe for live production use.

**`PROMPT_KEYS`** — the complete registry of 80+ prompt keys. Any key not in this dict causes a `RuntimeError` immediately — fail fast rather than silently produce wrong output.

---

#### `db/processing_lock.py`

Distributed mutex using the `processing_locks` table. The PRIMARY KEY constraint provides atomic lock acquisition — no separate SELECT + INSERT needed.

- `acquire_lock(session_id, tenant_id)` — INSERT with conflict handling; returns `False` if another worker already holds it
- `release_lock(session_id, tenant_id)` — DELETE
- `cleanup_stale_locks()` — removes locks older than 2 minutes (called every 60s at startup)

Guarantees exactly one message per session is processed at a time, even across multiple server instances.

---

#### `db/workflow_state.py`

Manages `workflow_sessions` records for multi-step flows. Wraps `session_store.py` with workflow-type-specific helpers for cleaner call sites.

---

#### `db/customer_data_service.py`

**`CustomerDataService(tenant_id, session_id)`** — service class for customer-specific data:
- `get_customer_summary()` — customer preferences, past categories, typical budget range
- `get_latest_ordered_product()` — last product ordered (used for pronoun resolution: "that one", "the same")
- `save_negotiation_outcome()` — writes to `negotiation_outcomes` table for analytics
- `get_order_history()` — structured past orders for history query replies

---

#### `db/product_store.py`

Product-specific DB operations:
- `get_product_by_sku(sku, tenant_id)` — exact SKU lookup from `product_cache`
- `create_order(...)` — inserts into `orders` and `order_items` tables

---

### Messaging — `messaging/sender.py`

Channel-agnostic sending layer. Appends every message to `incoming.captured_replies` (for the `/chat` JSON response) and also dispatches to the real channel.

- `send_reply(incoming, message)` → sends a text message
- `send_image(incoming, image_url, caption)` → sends an image card

For `channel == "whatsapp"`: calls `adapter/whatsapp_adapter.py` → Meta Graph API.
For all other channels (web, mock): logs to stdout and returns a mock `wamid`.

This abstraction means the entire pipeline is testable without a live WhatsApp connection.

---

### Utils

#### `utils/conversation_actions.py`

**`is_quick_confirm(incoming)`** — checks if the message is in the tenant's `ORDER_CONFIRM` frozenset. O(1) lookup. Called before any LLM for order confirmations — saves ~2.5s when the customer says "yes" or "confirm".

**`is_quick_action(incoming, action_key)`** — generic quick-action check for any key in `incoming.quick_actions`.

`quick_actions` is normalised to `frozenset` at tenant-load time by `setup.py::_apply_tenant()`. Membership tests at message time have zero allocation cost.

---

#### `utils/alerting.py`

Slack webhook integration for operational monitoring.

- `alert_pipeline_error(exception, incoming)` — fires when `run_pipeline()` raises an unhandled exception
- `alert_mem0_degraded(reason)` — fires when context retrieval falls back from primary to secondary source

Silent no-op when `SLACK_WEBHOOK_URL` is not set — safe to deploy without Slack configured.

---

#### `utils/invoice.py`

PDF invoice generation. Renders an HTML template with order details (customer name, items, unit prices, GST breakdown, UPI QR code), converts to PDF, uploads to Supabase Storage, and returns a permanent download URL.

---

### Migrations

Run in numeric order on a fresh database. Each file is designed to be safe to re-run.

| File | Purpose |
|---|---|
| `001_dynamic_prompts.sql` | Adds prompt columns to the `tenants` table |
| `002_quick_actions.sql` | Adds `quick_actions` JSONB column to `tenants` |
| `005_custom_dynamic_prompts.sql` | Extends `prompt_templates` schema |
| `006_structured_customer_data.sql` | Creates `negotiation_outcomes` and customer preference tables |
| `007_update_intent_prompt.sql` | Seeds updated intent prompt with `RoutingDecision` output fields |
| `008_tenant_configurations.sql` | Adds per-tenant config columns (floor price, image count, etc.) |
| `009_update_ask_quantity_prompt.sql` | Seeds the ask-quantity prompt |
| `010_invoice_keyword_shortcuts.sql` | Adds `INVOICE_INQUIRY` keyword list to `quick_actions` for all tenants |

---

### Interface (Streamlit)

#### `interface/app.py`

Streamlit web UI for testing without a real WhatsApp number. Sends messages to the `/chat` endpoint and renders text and image replies in a chat interface with pipeline debug info (intent, latency, route).

#### `interface/api.py`

HTTP client used by the Streamlit app to call the FastAPI backend (`/chat`, `/reset`).

---

## Multi-Tenant System

Every business that uses this agent gets a row in the `tenants` table. All configuration lives there — no code changes are needed for new clients.

**Key tenant columns:**

| Column | Type | Purpose |
|---|---|---|
| `tenant_id` | text PK | Unique identifier (e.g. `tenant_inventaa_led_001`) |
| `phone_number_id` | text | Meta phone number ID — routes inbound webhooks to this tenant |
| `waba_id` | text | WhatsApp Business Account ID |
| `biz_name`, `tagline`, `city` | text | Used in greetings and invoices |
| `support_email`, `support_phone`, `website` | text | Escalation replies and invoice footer |
| `upi_id`, `account_name`, `gstin`, `state_code` | text | Invoice and payment details |
| `gst_rate` | numeric | GST percentage applied to orders |
| `graphrag_api_url` | text | Per-tenant GraphRAG endpoint (each business has its own Neo4j) |
| `access_token` | text | Per-tenant Meta API token |
| `max_negotiation_rounds` | int | How many counter-offers before holding the final price |
| `neg_floor_disc_pct` | int | Maximum discount percentage the bot will offer |
| `neg_floor_multiplier` | float | Minimum price as a multiple of the list price |
| `intent_min_confidence` | float | Minimum confidence score to accept an intent label |
| `max_image_products` | int | How many product image cards to send per search result |
| `quick_actions` | jsonb | Phrase shortcuts: `ORDER_CONFIRM`, `ORDER_CANCEL`, `INVOICE_INQUIRY` |

---

## Prompt System

All customer-facing and LLM-facing text is stored in the `prompt_templates` table — never in code.

**Resolution order (per request, per message):**
1. In-memory cache (5-min TTL) — zero DB calls if warm
2. `prompt_templates` table — primary source; 80+ prompts
3. `tenants` table columns — legacy fallback; deprecating

**Template variables:** Use `{variable_name}` placeholders in prompt text. Rendered with `str.replace()`. Unreplaced variables are logged as warnings and left as-is.

**Updating a prompt at runtime (no restart needed):**
```python
from db.prompt_store import update_prompt_template
update_prompt_template(
    tenant_id   = "tenant_inventaa_led_001",
    prompt_name = "greeting_system_prompt",
    new_text    = "Your new prompt with {sender_name} and {biz_name} variables",
    language    = "en",
)
```
This creates a new versioned record, archives the old one, and invalidates the in-memory cache immediately.

---

## Negotiation State Machine

State is stored as a JSON document in `workflow_sessions` (type = `negotiation`) and loaded once per request into `incoming._cached_neg_state`.

```
[No negotiation]
      │ Customer: "can you give a discount?"
      ▼
[Phase 1: NEGOTIATING]
  rounds > 0, counter_offer_presented = False
  Bot makes counter-offers using handle_negotiation()
      │ Bot presents its absolute best price
      ▼
[Phase 2: COUNTER_OFFER_PRESENTED]
  counter_offer_presented = True
  detect_acceptance() LLM runs on each reply
  If still bargaining → bot holds price (neg_still_bargaining_prompt)
      │ Customer accepts
      ▼
[Phase 3: AWAITING_CONFIRMATION]
  awaiting_invoice_confirmation = True
  Bot shows full order summary (PricingResult)
  Waits for "Confirm" (quick-action frozenset or LLM fallback)
  Quantity changes handled here (compound intent support)
      │ Customer confirms
      ▼
[Order confirmed → complete_order() → invoice generated → state cleared]
```

---

## Environment Variables

Create a `.env` file in the project root:

```env
# Meta / WhatsApp
PHONE_NUMBER_ID=your_default_phone_number_id
WABA_ID=your_waba_id
ACCESS_TOKEN=your_meta_access_token
VERIFY_TOKEN=your_webhook_verify_token
WEBHOOK_SECRET=your_webhook_hmac_secret

# Azure OpenAI
AZURE_AI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_AI_API_KEY=your_azure_openai_key
AZURE_OPENAI_DEPLOYMENT=gpt-4.1
AZURE_AI_API_VERSION=2024-12-01-preview

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your_service_role_key
SUPABASE_STORAGE_BUCKET=whatsapp-media

# GraphRAG (global fallback — overridden per tenant in tenants table)
GRAPHRAG_API_URL=https://your-graphrag-service.com/query

# Application
APP_ENV=production
APP_NAME=WhatsApp AI Agent
BUSINESS_NAME=Your Business Name

# Alerting (optional — leave empty to disable)
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
```

---

## Database Tables

| Table | Purpose |
|---|---|
| `tenants` | One row per business. All config, credentials, and quick-action phrases |
| `messages` | Every inbound and outbound message. Primary audit trail |
| `processing_locks` | One row per active session. Prevents concurrent processing of the same session |
| `workflow_sessions` | Per-session state: negotiation, product selection, category choice, last discussed product |
| `orders` | Confirmed orders — financial record, never deleted |
| `order_items` | Line items for each order |
| `product_cache` | Cached GraphRAG product responses (sku → full product details) |
| `tenant_offers` | Cached global offer text + parsed discount tier JSON |
| `prompt_templates` | Versioned, per-tenant, per-language prompt templates |
| `negotiation_outcomes` | Analytics: opening price, final price, rounds taken, acceptance flag |

---

## Adding a New Client

Zero code changes required. Insert one row and seed prompts:

```sql
INSERT INTO tenants (
    tenant_id, phone_number_id, waba_id,
    biz_name, tagline, city,
    support_email, website, upi_id, account_name,
    gst_rate, support_phone, region, timezone, language,
    graphrag_api_url, access_token,
    max_negotiation_rounds, neg_floor_disc_pct, neg_floor_multiplier,
    intent_min_confidence, max_image_products,
    quick_actions
) VALUES (
    'tenant_new_client_001',
    'PHONE_NUMBER_ID_HERE',
    'WABA_ID_HERE',
    'Business Name', 'Business Tagline', 'City, State',
    'support@business.com', 'business.com', 'business@upi', 'Business Pvt Ltd',
    18, '+91 XXXXX XXXXX', 'india', 'Asia/Kolkata', 'en',
    'https://graphrag.business.com/query', 'META_ACCESS_TOKEN',
    3, 15, 0.80,
    0.60, 3,
    '{"ORDER_CONFIRM":    ["yes","ok","confirm","proceed","done"],
      "ORDER_CANCEL":     ["cancel","no","stop"],
      "INVOICE_INQUIRY":  ["invoice","bill","receipt","pdf","download"]}'
);
```

Then seed all 80+ prompt templates into `prompt_templates` for the new `tenant_id`. The system picks up the new tenant automatically on the next webhook arriving from that phone number.

---

## Latency Optimisations

| Optimisation | File | Saving per request |
|---|---|---|
| NEG BYPASS — skip intent LLM during active negotiation | `main.py` | ~2.5s |
| Invoice keyword shortcut — skip LLM on non-invoice messages | `pipeline/router.py` | ~2.5s |
| Parallel image sends via `asyncio.gather` | `ai/graphrag_handler.py` | ~1.5-2s |
| Cache saves as fire-and-forget `asyncio.create_task` | `ai/graphrag_handler.py` | ~500ms |
| Parallel dedup + history + quoted caption | `pipeline/setup.py` | ~150ms |
| Parallel neg_state + save_message | `pipeline/setup.py` | ~100ms |
| Parallel post-reply DB writes | `main.py` | ~150ms |
| Prompt template 5-min in-memory cache | `db/prompt_store.py` | ~100-300ms |
| Quick-action frozenset lookup before LLM confirm | `utils/conversation_actions.py` | ~2.5s |
| Negotiation state loaded once, cached on `incoming` | `main.py` / `pipeline/setup.py` | ~300ms |

The GraphRAG API itself (Neo4j query, 8-15s per request) is the remaining latency ceiling and requires improvements on the GraphRAG service side.
