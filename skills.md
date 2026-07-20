# skills.md — WhatsApp AI Sales Agent: Full Codebase Reference

This document explains **every Python file in this repository**, function by function: why the file
exists, how its logic actually works, a concrete usage example, and how it connects to the rest of
the system. It was produced by reading every file in full (~13,500 lines across 44 files) and is
organized to follow the real lifecycle of one WhatsApp message: webhook → setup → intent
classification → routing → AI handlers → DB → reply.

Every "How it connects" section below was verified with repo-wide `grep` for actual callers/importers
— not assumed from naming. Where a file turned out to have **zero live callers**, that is stated
explicitly rather than glossed over.

---

## 0. System Overview

This is a multi-tenant WhatsApp sales/support bot ("Inventaa LED Lights" is the reference tenant).
A customer messages a WhatsApp Business number → Meta POSTs a webhook to `main.py` → the message is
resolved to a tenant, deduplicated, locked, classified by intent, routed to a handler (greeting,
escalation, product search via GraphRAG, negotiation, invoice), and a reply is sent back — all within
a few seconds.

Design principles enforced throughout the codebase (visible in nearly every file's header comments):
- **No hardcoded prompt text.** Every customer-facing string is fetched from a per-tenant Postgres
  table (`prompt_templates`) via `db/prompt_store.py`. Adding a new client is a SQL insert, not a
  code change (see the project's `CLAUDE.md`).
- **No hardcoded word/keyword lists for intent-like decisions.** Classification is LLM-driven and
  DB-prompt-driven; the few exceptions (exact-phrase "quick actions" like "yes"/"confirm") are
  explicit, tenant-configured, and documented as deliberate performance shortcuts.
- **Postgres/Supabase, not Mem0**, is the memory layer. An earlier Mem0 integration was removed;
  several comments and one docstring still reference it as legacy language.
- **"Save-First" auditing**: the raw inbound message is persisted to the `messages` table before any
  AI processing happens, so nothing is lost if the pipeline crashes mid-request.

### Request lifecycle (the path one message takes)

1. **`adapter/whatsapp_adapter.py`** — `parse_webhook()` translates Meta's JSON into an `IncomingMessage`.
2. **`main.py`** — `receive_message()` ACKs Meta immediately, then runs `run_pipeline()` in the background.
3. **`pipeline/setup.py`** — `setup_pipeline()` resolves the tenant, dedups, acquires a per-session lock, fetches history, persists the message, fetches negotiation state.
4. **`ai/handlers.py`** — `classify_intent()` (or a negotiation-bypass shortcut in `main.py`) determines intent + a `RoutingDecision`.
5. **`pipeline/router.py`** — `dispatch()` applies guards (dynamic-knowledge intercept, negotiation guard, invoice guard) and routes to a specialist: `ai/handlers.py` (greeting/escalation/unknown), `ai/customer_history_handler.py` (order/offer history), or `ai/graphrag_handler.py` (product search).
6. **`ai/graphrag_handler.py`** — first defers to **`ai/product_followup.py`** (is this a follow-up about a known product?) before ever calling the external GraphRAG API.
7. **`ai/negotiator.py`** / **`ai/pricing.py`** / **`ai/invoice_handler.py`** / **`ai/order_service.py`** — handle haggling, price math, order confirmation, and PDF invoice generation.
8. **`db/*.py`** — every read/write of tenant config, session state, product cache, and orders.
9. **`messaging/sender.py`** → **`adapter/whatsapp_adapter.py`** — the reply goes back out over the WhatsApp Cloud API (or is captured for the Streamlit test UI).

---

## Appendix: Confirmed Dead Code

Multiple independent research passes over this codebase (each verifying with repo-wide grep, not
assumption) found the same modules have **zero live callers** in the running pipeline. They are
still documented in full below (in case they're revived), but treat them as reference/scaffolding,
not the actual behavior of the bot:

| File / Function | Status | Superseded by |
|---|---|---|
| `ai/orchestrator.py` (`AIOrchestrator`) | Dead — confirmed dead by `docs/LATENCY_ANALYSIS.md` itself | `main.py` builds `AIRequestContext` inline |
| `ai/prompt_builder.py` (`PromptBuilder`) | Dead — no importer anywhere | `db/prompt_store.get_prompt`/`aget_prompt` called directly |
| `ai/graphrag_request_builder.py` (`GraphRAGRequestBuilder`) | Dead — never instantiated | `ai/graphrag_handler.call_graphrag_api` builds its own payload |
| `ai/customer_profile_updater.py` | Dead — no external callers (though some internal logic is real) | n/a |
| `ai/perf_metrics.py` | Defined, functional, but no confirmed call site invokes it | `ai/request_profiler.py` (which *is* wired in) |
| `db/workflow_state.py` (`get_pending_state`, `save_pending_state`, `merge_state`, `complete_state`, `expire_state`) | Dead — zero callers | `db/session_store.py`'s `save_pending_order`/`get_pending_order`/`delete_pending_order` |
| `db/session_store.get_last_order` / `get_last_n_orders` (messages-table variants) | Dead | `get_last_order_from_orders` / `get_last_n_orders_from_orders` (orders-table variants) |
| `db/product_store.get_order_by_id` | Dead — no callers found | n/a |
| `utils/alerting.py` (`alert_pipeline_error`, `alert_mem0_degraded`) | Dead — not imported/called anywhere, despite `CLAUDE.md` marking it "✅ Done" | n/a |

---

# Part 1 — Entry Point, Configuration, Data Contracts

## main.py

### Why this file exists
This is the FastAPI application entry point and the top-level orchestrator for the whole 8-step message pipeline (parse webhook → setup → classify → dispatch → send → persist → return debug). The file header explicitly states it holds no business logic — every step delegates to a specialist module (`pipeline/setup.py`, `pipeline/router.py`, `ai/handlers.py`, `db/session_store.py`). It exists as a single file so there's one obvious place to trace a request end-to-end, and to host the FastAPI route definitions (webhook verify/receive, `/chat` test endpoint, `/reset`, startup hook).

### How it works

Module-level: `app = FastAPI(title="WhatsApp AI Agent")`. `_pipeline_semaphore = asyncio.Semaphore(50)` caps concurrent pipeline runs at 50 to avoid unbounded resource usage under traffic spikes.

`@app.on_event("startup") async def startup()`
Kicks off `_periodic_lock_cleanup()` as a background `asyncio.create_task`, logs that it started.

`async def _periodic_lock_cleanup()`
Infinite loop: sleeps 60s, then calls `db.processing_lock.cleanup_stale_locks()`. Catches `asyncio.CancelledError` to break cleanly on shutdown; catches other exceptions and logs them without dying (so a single cleanup failure doesn't kill the whole background task permanently — though note if `cleanup_stale_locks` itself raises repeatedly, this loop just keeps logging and retrying every 60s).

`@app.get("/webhook") async def verify_webhook(request: Request)`
Meta's webhook verification handshake (`hub.mode=subscribe`, `hub.verify_token`, `hub.challenge`). Compares the token against `config.VERIFY_TOKEN`; returns the challenge as plain text on match, else HTTP 403 "Forbidden".

`@app.post("/webhook") async def receive_message(request: Request)`
The real inbound message endpoint. If `WEBHOOK_SECRET` is configured, verifies the `X-Hub-Signature-256` header via HMAC-SHA256 over the raw request body using `hmac.compare_digest` (timing-safe comparison); rejects with 403 on mismatch. If not configured, warns (in production only) and just parses the JSON body directly — meaning signature verification is opt-in, not enforced by default. Defines an inner `_guarded()` coroutine that acquires the semaphore, parses the webhook via `adapter.whatsapp_adapter.parse_webhook`, and if a message resulted, runs `run_pipeline(incoming)`. Schedules `_guarded()` as a background task via `asyncio.create_task` and returns `{"status": "ok"}` **immediately** — this is the standard WhatsApp Cloud API pattern of acking fast (Meta expects a quick 200) and doing the real work asynchronously, decoupled from the HTTP response.

`@app.post("/chat") async def chat_endpoint(payload: dict)`
Test/Streamlit interface — builds an `IncomingMessage` directly from a JSON payload (`phone`, `message`, optional `phone_number_id`/`sender_name`, with env-var defaults), skipping the WhatsApp webhook parsing entirely. `tenant_id` starts as `"UNRESOLVED"` (resolved later inside `run_pipeline`→`setup_pipeline`). Calls `run_pipeline(incoming)` and returns its result directly (synchronously, unlike the webhook path — the caller waits for the actual reply). On exception, prints a traceback and returns a 500 with a synthetic error-reply payload matching the normal response shape (`replies`/`debug` with `intent="ERROR"`).

`@app.post("/reset") async def reset_endpoint(payload: dict)`
Dev/testing utility — deletes all rows for a given `phone`+`tenant_id` from `messages`, `processing_locks`, `workflow_sessions`, and `orders` tables directly via the Supabase client (`db.session_store._get_client()`). Returns a status dict or a 500 with the error string.

`async def _verify_prompt_keys_on_startup(tenant_id: str) -> None`
Not wired into the `@app.on_event("startup")` hook (only `_periodic_lock_cleanup` is registered there) — this looks like a manually-invokable diagnostic: iterates `db.prompt_store.PROMPT_KEYS`, checks each via `db.prompt_store._load_from_db(tenant_id, key, "en")`, and prints which are missing versus a confirmation that all are present. Meant to catch a misconfigured tenant before it serves live traffic, but per the code as written it is **not automatically called anywhere in this file** (no call site in `startup()` or elsewhere).

`async def run_pipeline(incoming: IncomingMessage) -> dict`
The core 8-step pipeline, called from both `/webhook`'s background task and `/chat` directly.
- Starts request profiling (`ai.request_profiler.start()`) and a wall-clock timer.
- **Steps 1-5 (setup)**: `ok, session_history, _neg = await setup_pipeline(incoming)`. If `not ok`, returns `_empty_result()` immediately (no lock to release, per the setup.py invariant).
- Wraps the rest in `try/finally` so `release_lock(incoming.session_id, incoming.tenant_id)` always runs once setup succeeded.
- **Step 3 (classify intent) with NEG BYPASS**: if the negotiation state indicates an active negotiation (`rounds > 0` or `awaiting_quantity=True`, and not `awaiting_invoice_confirmation`), skips the real LLM intent classification and fabricates a `_BypassResult` stub with `intent="WORKFLOW_ACTION"`, `confidence_score=0.97` — saving ~2.5-3s per message during back-and-forth negotiation. The extensive inline comment explains why `awaiting_invoice_confirmation` is deliberately excluded (money-adjacent, needs full classification) and why the broader `PRODUCT_SELECTION` browsing state is NOT included (too open-ended; would break `requested_knowledge_field` routing for spec/installation/warranty questions). Otherwise calls `ai.handlers.classify_intent(customer_message=incoming.text, session_history=session_history, incoming=incoming)`. Stores `incoming._routing = result.routing`.
- **Step 4**: fire-and-forget `asyncio.create_task(update_intent(...))` — audit log write to `db.session_store`, doesn't block the reply.
- **Step 4.5**: builds `ai.request_context.AIRequestContext(incoming, result, session_history, neg_state=_neg)` and caches it as `incoming._cached_arc` for `pipeline/router.py` to use.
- **Step 5**: `reply = await dispatch(incoming, result, session_history)` (from `pipeline.router`), timed into the `"dispatch"` profiling bucket.
- **Step 6**: `_send_reply_chunked(incoming, reply)` — sends via `messaging.send_reply`, splitting on WhatsApp's message-length limits if needed.
- **Step 7**: if a WhatsApp message ID (`sent_wamid`) came back, persists the reply in parallel via `asyncio.gather`: `db.session_store.update_reply` (with `incoming._graphrag_raw` if set) and `db.session_store.save_outbound_message`.
- **Step 8**: computes latency, grabs the profiler snapshot (`ai.request_profiler.snapshot()`), prints timing/profile summary lines, and returns a dict with `replies` (from `incoming.captured_replies`) and a `debug` block (intent, confidence, latency, human-readable route, tenant_id, profile). Handles the case where the negotiator deferred mid-flow (`incoming._deferred_intent` set) by reporting the *actual* routing decision rather than the stale bypass-stub intent, via `_get_route_for_intent`.

`async def _send_reply_chunked(incoming: IncomingMessage, reply: str) -> Optional[str]`
Returns `None` immediately for an empty reply (e.g. the image-already-sent case in `router.py` that returns `""`). Splits `reply` into 4000-char chunks (below WhatsApp's actual 4096-char hard limit, leaving headroom) if it exceeds that length, sending each sequentially via `send_reply`, and returns the **last** chunk's wamid (used for the single DB "reply sent" record — earlier chunks' wamids are discarded).

`_get_route(result) -> str`
Debug-label mapper: `GREETING`→"Greeting Handler", `HUMAN_ESCALATION`→"Human Handoff Escalation", `FAQ_KNOWLEDGE`/`WORKFLOW_ACTION` or low-confidence→"GraphRAG / Catalog", else "Unknown Intent Handler". Mirrors (but doesn't literally call) the routing logic in `pipeline/router.py`.

`_get_route_for_intent(intent: str) -> str`
Same mapping, but takes just an intent string (no confidence score) for the deferred-intent debug case where only `incoming._deferred_intent` is known.

`_empty_result() -> dict`
Returns the standard empty-response shape (`replies=[]`, `debug.intent="SKIPPED"`) used when `setup_pipeline` aborts (duplicate, unknown tenant, lock contention).

Bottom-of-file imports (`ai.graphrag_handler.call_graphrag_api, _send_structured_product_list, _coerce_pythonic_dict` and `ai.product_followup._try_resolve_product_followup, _parse_followup_message`) are imported but not directly referenced anywhere else in this file — the trailing comment says this placement avoids circular import issues, implying these imports exist to pre-warm/force-resolve the import graph at module load time (side-effect import) rather than for direct use in `main.py` itself.

### Example
Meta POSTs a webhook payload for a text message "Hi" from a new customer. `receive_message` verifies the HMAC signature (if `WEBHOOK_SECRET` set), schedules `_guarded()` in the background, and immediately returns `{"status": "ok"}` to Meta. In the background, `parse_webhook` builds an `IncomingMessage`, `run_pipeline` runs: `setup_pipeline` resolves the tenant and acquires the lock; intent classification returns `GREETING` (not bypassed, since there's no negotiation state); `dispatch` (in `pipeline/router.py`) routes to `ai.handlers.handle_greeting`, returning a welcome message; `_send_reply_chunked` sends it via `messaging.send_reply` → `adapter.whatsapp_adapter.send_whatsapp_reply` → Meta Graph API; the reply is persisted; the lock is released in `finally`; the returned dict (not seen by Meta, since this ran in a background task) would show `debug.route="Greeting Handler"`.

### How it connects
- Imports from: `config` (`VERIFY_TOKEN`, `WEBHOOK_SECRET`, `APP_ENV`, `AZURE_OPENAI_DEPLOYMENT`), `adapter.whatsapp_adapter.parse_webhook`, `models.schemas.IncomingMessage`, `ai.handlers.classify_intent`, `db.session_store` (`update_intent`, `update_reply`, `save_outbound_message`), `db.processing_lock` (`release_lock`, `cleanup_stale_locks`), `pipeline.setup.setup_pipeline`, `pipeline.router.dispatch`, `messaging.send_reply`, and (bottom-of-file) `ai.graphrag_handler`, `ai.product_followup`, `ai.request_profiler`, `ai.request_context.AIRequestContext`, `db.prompt_store` (inside `_verify_prompt_keys_on_startup`).
- Nothing else in the repo imports from `main.py` (it's the entry point — confirmed no other file references `main.dispatch`/`main.run_pipeline`/etc. via grep, aside from being launched by `uvicorn main:app`).

---

## config.py

### Why this file exists
Single centralized place to load environment variables (via `python-dotenv`) into typed Python constants, so every other module imports configuration from one place (`from config import X`) instead of scattering `os.getenv()` calls throughout the codebase. This makes it trivial to see the full set of required environment variables and their defaults in one file, and avoids re-parsing `.env` in every module.

### How it works
No functions or classes — pure module-level constant loading, executed once at import time.
- `load_dotenv()` reads `.env` into `os.environ` before any `os.getenv()` calls below.
- **WhatsApp/Meta**: `PHONE_NUMBER_ID`, `WABA_ID`, `ACCESS_TOKEN`, `VERIFY_TOKEN` (no defaults — `None` if unset), `WEBHOOK_SECRET` (defaults to `""`, meaning HMAC verification is skipped if unset — see `main.py`).
- **Azure OpenAI**: `AZURE_AI_ENDPOINT`, `AZURE_AI_API_KEY` (default `""` via `or ""` guard against `None`), `AZURE_OPENAI_DEPLOYMENT` (default `"gpt-4.1"`), `AZURE_AI_API_VERSION` (default `"2024-12-01-preview"`).
- **Supabase**: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (both `or ""` guarded), `SUPABASE_STORAGE_BUCKET` (default `"whatsapp-media"`).
- **Products/GraphRAG APIs**: `PRODUCTS_API_URL`, `GRAPHRAG_API_URL` (both default `""` — these are apparently global fallbacks, since `pipeline/setup.py` shows per-tenant overrides `incoming.graphrag_api_url`/`incoming.products_api_url` are also supported).
- **Application**: `APP_NAME`, `BUSINESS_NAME` (both default to generic names), `APP_ENV` (default `"production"` — used in `main.py` to gate the webhook-secret warning, and in `utils/alerting.py` to tag Slack alerts).
- **Alerting**: `SLACK_WEBHOOK_URL` (default `""` — disables `utils/alerting.py` entirely when unset).

### Example
`from config import AZURE_OPENAI_DEPLOYMENT, AZURE_AI_API_VERSION` in `pipeline/router.py` yields e.g. `"gpt-4.1"` and `"2024-12-01-preview"`, used to construct the shared `AzureOpenAI` client at module scope.

### How it connects
- Imports: `dotenv.load_dotenv`, `os`. No project-internal imports (this is a leaf/root module).
- Imported by nearly every module that needs credentials or environment-driven behavior: `pipeline/router.py` (Azure OpenAI settings), `main.py` (`VERIFY_TOKEN`, `WEBHOOK_SECRET`, `APP_ENV`, `AZURE_OPENAI_DEPLOYMENT`), `adapter/whatsapp_adapter.py` (`WABA_ID`, `PHONE_NUMBER_ID`, `BUSINESS_NAME`, `ACCESS_TOKEN`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_STORAGE_BUCKET`), `utils/invoice.py` (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_STORAGE_BUCKET`), `utils/alerting.py` (`SLACK_WEBHOOK_URL`, `APP_ENV`), plus every other `ai/`/`db/` module that constructs an `AzureOpenAI` client.

---

## models/schemas.py

### Why this file exists
Defines the shared data contracts (`IncomingMessage`, `RoutingDecision`, `IntentResult`, `OrderItem`, `EntityResult`) used across the entire pipeline — adapter, pipeline/setup, pipeline/router, and every `ai/` handler. Having these as plain `@dataclass`es in one file (rather than duplicating shape across modules, or passing loose dicts) gives every consumer a single, typed source of truth for what fields exist on an inbound message, an intent-classification result, or an extracted order item.

### How it works

`@dataclass class IncomingMessage`
The largest and most central schema — represents one inbound customer message plus every piece of tenant/session state accumulated as it flows through the pipeline. Grouped by comment sections: **Tracing** (`trace_id`, `message_id`, `session_id`, `channel`, `timestamp` — all required, no defaults); **Tenant** (`tenant_id`, `waba_id`, `phone_number_id`, `biz_name`, `region`, `timezone`, `language` — required, populated first from the adapter with placeholders like `"UNRESOLVED"`, then overwritten by `pipeline/setup.py._apply_tenant`); **Sender** (`sender_name`, `sender_phone`); **Message** (`text`, `original_type`, `received_at`); **Billing fields** (`tagline` through `gst_rate`, all `Optional` with defaults, populated by `_apply_tenant`); **Core handler prompts**, **Invoice handler prompts**, **Negotiation detection prompts**, **Negotiation reply prompts**, **Fast confirm prompt**, **Product summary prompt** — all `Optional[str] = None`, corresponding 1:1 to `pipeline/setup.py`'s `PROMPT_COLUMNS` list, loaded from the `tenants` table; **negotiation/tuning numeric config** (`max_negotiation_rounds`, `neg_floor_disc_pct`, `neg_floor_multiplier`, `intent_min_confidence`, `require_offer_disclosure`, `max_image_products`); **Per-tenant config** (`valid_intents`, `graphrag_api_url`, `products_api_url`, `access_token`); **Quick actions** (`quick_actions: Optional[Dict[str, frozenset]]`, pre-normalized by `_apply_tenant`, matched via `utils/conversation_actions.py`); **Media** (`media_id`, `media_mime_type`, `media_binary`, `media_url`); **Quoted message** (`quoted_message_id`, `quoted_caption`); **Output** (`captured_replies: List[dict]` via `field(default_factory=list)`, `raw: dict`, `_cached_neg_state`); **Runtime-computed attributes** set after construction (`_routing: Optional[RoutingDecision]`, `_cached_arc: Optional[object]` — set by `main.py`/`pipeline/router.py`, `resolved_product`, `resolved_query` — used by the dynamic-knowledge guard in `pipeline/router.py`), all marked `repr=False` to keep debug prints manageable.

`@dataclass class RoutingDecision`
A small, deliberately tenant-agnostic signal computed once during intent classification (no extra LLM call), answering "which subsystems does this message need". Fields: `operation` (`"NEW_SEARCH" | "MODIFY_WORKFLOW" | "OTHER"` — a semantic label the LLM assigns based on meaning, not domain-specific keywords), `needs_graphrag: bool`, `needs_customer_context: bool` (renamed from `needs_memory`), `needs_workflow_state: bool`, `needs_product_context: bool`, `needs_customer_history: bool = False`, `knowledge_domain: Optional[str] = "product"`, `requested_knowledge_field: Optional[str] = None`. This is exactly the structure `pipeline/router.py` reads via `incoming._routing`/`result.routing` to drive the dynamic-knowledge guard, the `MODIFY_WORKFLOW` gate, and the customer-history intercept. *(Project memory note: `routing.operation` must never be used as a same-product-vs-new-search proxy in `product_followup.py` — that logic belongs to `ai/product_context_resolver.py` and the LLM-driven follow-up parser instead.)*

`@dataclass class IntentResult`
`intent: str`, `confidence_score: float`, `raw_text: str`, `routing: Optional[RoutingDecision] = None`. The return shape of `ai.handlers.classify_intent`; `main.py`'s `_BypassResult` stub duck-types this shape (same attribute names) without literally subclassing it.

`@dataclass class OrderItem`
`product_name: Optional[str]`, `quantity_value: Optional[int]`, `quantity_unit: Optional[str]`. Properties: `is_complete` (`True` if both product and quantity are set); `missing` (list of missing field names, `"product_name"`/`"quantity"`); `quantity_str` (formats `"{value} {unit}"` if both present, else just the value, else `None`).

`@dataclass class EntityResult`
`items: List[OrderItem]`, `delivery_date: str`, `invoice_number: Optional[str]`, `payment_reference: Optional[str]`, `missing_entities: List[str]`, `raw_text: str`, `tenant_id: str`. Properties provide convenience single-item access assuming `items[0]` is the primary/only item when not multi-product: `product_name`, `quantity_value`, `quantity_unit`, `quantity` (all delegate to `items[0]`'s corresponding field, or `None`/default if `items` is empty), `all_complete` (`True` only if `items` is non-empty AND every item `is_complete`), `is_multi_product` (`len(items) > 1`).

### Example
After entity extraction on "I want 5 mini elena gate lights and 2 solar panels", the entity extractor would return an `EntityResult` with `items=[OrderItem("mini elena gate lights", 5, "units"), OrderItem("solar panels", 2, "units")]`; `.is_multi_product` → `True`, `.product_name` → `"mini elena gate lights"` (first item only), `.all_complete` → `True` since both items have product+quantity.

### How it connects
- No project-internal imports — this is a pure leaf module (only `dataclasses`/`typing` from the standard library).
- `IncomingMessage` is imported by essentially every module in the pipeline: `pipeline/setup.py`, `adapter/whatsapp_adapter.py`, `messaging/sender.py`, `main.py`, `ai/handlers.py`, `db/prompt_store.py`, `db/session_store.py`, `db/workflow_state.py`, `ai/customer_profile_updater.py`, and others (10 files total, confirmed via grep).
- `RoutingDecision` and `IntentResult` are produced by `ai.handlers.classify_intent` and consumed by `pipeline/router.py` (`incoming._routing`, `result.routing`, `result.intent`, `result.confidence_score`).
- `OrderItem`/`EntityResult` are produced by the entity-extraction path and consumed wherever order items are processed (`ai/order_service.py`, `ai/negotiator.py`, `db/workflow_state.py`).

---

# Part 2 — Channel Adapter & Messaging

## adapter/whatsapp_adapter.py

### Why this file exists
This is the boundary/translation layer between Meta's WhatsApp Cloud API wire format and the bot's internal, platform-neutral `IncomingMessage` schema. It exists as a separate module so the rest of the pipeline never has to know about Meta's webhook JSON shape, Graph API endpoints, or media-download mechanics — swapping to a different messaging platform would mean rewriting only this adapter (and `messaging/sender.py`, which also depends on it for outbound sends). Without it, webhook parsing and Graph API calls would be duplicated/inlined across handlers.

### How it works

Module-level: `GRAPH_API_VERSION` (env-overridable, defaults `"v21.0"`, explicitly called out as platform-wide rather than per-tenant config so it stays an env var, not a DB column). `_supabase: Optional[Client] = None` — lazy singleton so a missing/placeholder `.env` doesn't crash uvicorn at import/startup time.

`_get_client() -> Client`
Lazily constructs (once) and returns the module-level Supabase client using `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` from `config`.

`async def download_media(media_id: str) -> Optional[bytes]`
Two-step Meta media download required by the Graph API: Step 1 GETs `https://graph.facebook.com/{version}/{media_id}` with a Bearer token to resolve a temporary signed download URL; Step 2 GETs that URL (Bearer token required again — the URL itself isn't public) to fetch the actual binary. Returns `None` on either step's non-200 status; logs failures with status codes. Uses a 30s-timeout `httpx.AsyncClient`.

`get_file_extension(mime_type: str) -> str`
Maps a MIME type (stripping any `; codecs=...` suffix and lowercasing) to a file extension via a small static dict covering `image/jpeg`, `image/png`, `image/webp`, `audio/ogg`, `audio/mp4`, `audio/mpeg`. Falls back to `.bin` for anything unrecognized.

`upload_to_storage(binary: bytes, mime_type: str, folder: str, file_name: str, tenant_id: str = "shared") -> Optional[str]`
Synchronous (not `async`) upload to Supabase Storage at path `{tenant_id}/{folder}/{file_name}`, tenant-scoped to avoid filename collisions and to enable future per-tenant quotas/policies. Strips codec suffix from the MIME type for the `Content-Type` header. Returns the permanent public URL (constructed by string formatting — relies on the bucket being configured Public) on success, or `None` on any exception (pipeline continues without a stored URL).

`async def parse_webhook(data: dict) -> Optional[IncomingMessage]`
The central webhook translator. Wrapped in a broad `try/except (KeyError, IndexError, TypeError)` to handle malformed payloads gracefully (logs and returns `None` rather than crashing the webhook handler). Logic:
- Extracts `entry[0].changes[0].value`; if `entry` is empty, returns `None`.
- If `value.messages` is empty/absent, it's a delivery/read receipt — returns `None` silently (no logging, since this is expected/frequent).
- Reads `msg_type`, timestamp, and any quoted-message context (`context.id` → `quoted_message_id`).
- **Text messages**: fastest path, no HTTP calls — pulls `msg["text"]["body"]` directly.
- **Image messages**: extracts `media_id`/`mime_type`, calls `download_media`; if download fails, logs and returns `None` (message dropped entirely — not queued/retried). On success, uploads to storage under `images/` scoped by `phone_number_id` (tenant_id isn't resolved yet at this point, so `phone_number_id` is used as a proxy scope). Sets `text` to the internal marker `"__MEDIA_UNSUPPORTED_IMAGE__"` since OCR isn't implemented — `pipeline/setup.py`'s `_resolve_media_unsupported_marker` converts this into tenant-facing copy once tenant resolution has happened.
- **Audio messages**: symmetric to image handling, uploads under `audio/`, sets marker `"__MEDIA_UNSUPPORTED_AUDIO__"` (STT not implemented).
- **Other types** (sticker, reaction, location, contact, document): logs and returns `None` — explicitly out of scope for "Phase 1".
- Builds and returns the `IncomingMessage` dataclass with `tenant_id="UNRESOLVED"` (resolved downstream in `pipeline/setup.py`), `region="unresolved"`/`timezone="UTC"` as neutral placeholders overwritten by tenant resolution, sender info from `contact["profile"]["name"]`/`contact["wa_id"]`, and all media fields populated (or left `None` for text).

`async def send_whatsapp_reply(to: str, message: str, phone_number_id: Optional[str] = None, access_token: Optional[str] = None) -> Optional[str]`
Sends a plain text WhatsApp message via `POST {graph_url}/{phone_number_id}/messages` with a standard Cloud API text payload. Accepts per-tenant `phone_number_id`/`access_token` overrides (falling back to the global `config` constants for single-tenant deployments — this is how multi-tenant WhatsApp Business Accounts are supported without duplicating this function). On HTTP 200, extracts and returns the `wamid` from the response JSON (falling back to `"unknown_wamid"` if the JSON shape is unexpected); on non-200, logs the status/body and returns `None`. No retry logic — a single attempt.

`async def send_whatsapp_image(to: str, image_url: str, caption: str = "", phone_number_id: Optional[str] = None, access_token: Optional[str] = None) -> Optional[str]`
Same pattern as `send_whatsapp_reply` but for a WhatsApp `"image"` message type with a `link`+`caption` payload (sends by URL, not by re-uploading bytes — the image must already be at a public URL, e.g. from `upload_to_storage`). Wrapped in its own outer `try/except` — any exception (including connection errors) is caught, logged, and results in `None`.

### Example
`await download_media("1234567890")` resolves the temporary URL via Graph API, downloads the JPEG bytes, and returns e.g. `b'\xff\xd8\xff...'` (2.1MB). Then `upload_to_storage(binary, "image/jpeg", "images", "919876543210_1700000000.jpg", tenant_id="unresolved")` returns `"https://xyz.supabase.co/storage/v1/object/public/whatsapp-media/unresolved/images/919876543210_1700000000.jpg"`.

### How it connects
- Imports: `models.schemas.IncomingMessage`, `config` (`WABA_ID`, `PHONE_NUMBER_ID`, `BUSINESS_NAME`, `ACCESS_TOKEN`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_STORAGE_BUCKET`), `supabase.create_client`/`Client`, `httpx`.
- `parse_webhook` is imported and called by `main.py` (`receive_message`'s `_guarded()` inner function) — confirmed sole production caller.
- `send_whatsapp_reply` and `send_whatsapp_image` are imported by `messaging/sender.py` (`send_reply`/`send_image` delegate to them when `incoming.channel == "whatsapp"`) and by `ai/invoice_handler.py` (imports `send_whatsapp_reply` directly).
- No other file imports `download_media`, `get_file_extension`, or `upload_to_storage` directly outside this module — internal helpers used only within `parse_webhook`.

---

## messaging/sender.py

### Why this file exists
A thin, channel-agnostic abstraction over "send a reply to the customer" that decouples the rest of the pipeline (`pipeline/router.py`, `ai/graphrag_handler.py`, `ai/product_followup.py`, `main.py`) from knowing whether the current conversation is real WhatsApp traffic or the `/chat` mock/Streamlit test channel. It also centralizes `captured_replies` bookkeeping (used both for the `/chat` endpoint's JSON response and debugging), so every send path — text or image — records what was "said" regardless of transport.

### How it works

`async def send_reply(incoming: IncomingMessage, message: str) -> Optional[str]`
Always appends `{"type": "text", "body": message}` to `incoming.captured_replies` first (so the mock/test path and the real path both get a record). If `incoming.channel == "whatsapp"`, delegates to `adapter.whatsapp_adapter.send_whatsapp_reply`, passing through `incoming.phone_number_id` and `incoming.access_token` (via `getattr` with a `None` default) — this is what makes per-tenant WhatsApp credentials work. Otherwise (mock/Streamlit channel), just prints a truncated preview to stdout and returns a fabricated `f"mock_wamid_{uuid.uuid4().hex[:8]}"` so downstream code (`main.py` Step 7, which only persists a reply if `sent_wamid` is truthy) behaves identically to the real-send path.

`async def send_image(incoming: IncomingMessage, image_url: str, caption: str = "") -> Optional[str]`
Same pattern: appends `{"type": "image", "link": image_url, "caption": caption}` to `captured_replies`, then either calls `adapter.whatsapp_adapter.send_whatsapp_image` (real channel) or fabricates a mock wamid (test channel).

### Example
During a `/chat` test session (`incoming.channel == "web"`), `await send_reply(incoming, "Your order is confirmed!")` appends the text to `captured_replies`, prints a mock-sender log line, and returns `"mock_wamid_a1b2c3d4"` — which `main.py` then uses as truthy to trigger `update_reply`/`save_outbound_message`.

### How it connects
- Imports: `adapter.whatsapp_adapter` (`send_whatsapp_reply`, `send_whatsapp_image`), `models.schemas.IncomingMessage`, `uuid`.
- Re-exported via `messaging/__init__.py` as `from messaging.sender import send_reply, send_image` — so all external callers import from the `messaging` package, not this submodule directly.
- `send_reply` is called by `main.py` (`_send_reply_chunked`) and `send_image` is called by `pipeline/router.py` (dynamic-knowledge image-asset delivery path). `ai/product_followup.py` and `ai/graphrag_handler.py` also import `send_image`/`send_reply` from the `messaging` package.

---

# Part 3 — Pipeline (Setup & Routing)

## pipeline/setup.py

### Why this file exists
Encapsulates pipeline setup steps 1-5 (tenant resolution, deduplication, session-history fetch, lock acquisition, message persistence, negotiation-state fetch) as one coherent unit with strict lock-ownership semantics. It exists separately from `main.py` to keep `main.py` a pure orchestrator and to centralize the tricky invariant around who owns the per-session processing lock — getting that wrong (double-releasing, or leaking a lock) would corrupt concurrent-request handling across the whole bot.

### How it works

Module-level constant `PROMPT_COLUMNS`: a list of ~21 legacy prompt column names still read directly off the `tenants` table row (as opposed to the newer `prompt_templates` DB table used by `db/prompt_store.py`). The comment notes this list is expected to shrink to empty as tenants migrate, and that `acceptance_keywords`/`acceptance_exact_words`/`new_order_trigger_phrases` were removed as dead columns.

`async def setup_pipeline(incoming: IncomingMessage) -> tuple`
The main entry point, returning `(True, session_history, neg_state)` to continue or `(False, None, None)` to abort silently. Documents a critical lock-ownership invariant: callers only own the processing lock if this returns `(True, ...)`; on `False` or an exception, the lock is never the caller's responsibility (either never acquired, or already released internally before the exception propagates). Steps:
1. **Step 1**: resolves `tenant_id` from `phone_number_id` via `db.session_store.resolve_tenant_id`. If unknown, logs and returns `(False, None, None)` immediately (no lock ever touched). Otherwise applies tenant info (`_apply_tenant`), resolves any media-unsupported marker (`_resolve_media_unsupported_marker`), and logs the incoming message (`_log_incoming`).
2. **Step 2** (parallel via `asyncio.gather` with `return_exceptions=True`): dedup check (`db.session_store.is_duplicate`), session history fetch (`db.session_store.get_session_history`, limit 10), and quoted-caption resolution (`_resolve_quoted_caption`) — three independent operations run concurrently to save ~150ms. Dedup errors or a `True` dedup result both cause an early `(False, None, None)` return (no lock held). History errors are logged as non-critical and default to an empty list.
3. **Step 3**: acquires the per-session processing lock via `db.processing_lock.acquire_lock`; if another worker already holds it, returns `(False, None, None)`.
4. **Step 4** (parallel, only after lock acquired): fetches negotiation state (`db.session_store.get_negotiation_state`) and persists the inbound message (`db.session_store.save_message`) concurrently. Both wrapped in `return_exceptions=True` and logged as non-critical on failure — except the whole gather is inside a `try/except Exception`: if anything else raises, the lock is explicitly released via `db.processing_lock.release_lock` before re-raising, satisfying the ownership invariant. Caches `neg_state` onto `incoming._cached_neg_state` and returns `(True, session_history, neg_state)`.

`_apply_tenant(incoming: IncomingMessage, info: dict) -> None`
Copies every relevant tenant DB column (`biz_name`, `timezone`, `region`, `language`, `tagline`, `city`, contact/payment fields, `gst_rate` as a decimal fraction, negotiation tuning params, `intent_min_confidence`, `valid_intents`, per-tenant API URLs/tokens) onto `incoming`. Normalizes `quick_actions` from raw `{action: [phrase, ...]}` JSON into `{action: frozenset(casefold(phrase))}` once here (not per-message) for cheap membership tests later via `utils.conversation_actions`. Loads every column in `PROMPT_COLUMNS` onto `incoming` (defaulting to `None`), and logs (warning only, not fatal) any that are missing, pointing at `migrations/003_all_prompts_dynamic.sql`.

`_resolve_media_unsupported_marker(incoming: IncomingMessage) -> None`
`adapter/whatsapp_adapter.py` sets `incoming.text` to an internal marker string (`__MEDIA_UNSUPPORTED_IMAGE__` / `__MEDIA_UNSUPPORTED_AUDIO__`) for image/audio messages because OCR/STT aren't implemented and tenant isn't resolved yet at parse time. This function, now that `tenant_id` is known, looks up the marker in a small dict, and if found, replaces `incoming.text` with the tenant's own `media_unsupported_prompt` rendered via `db.prompt_store.get_prompt`. Falls back to a neutral, non-branded placeholder string if the prompt isn't seeded (`RuntimeError`), rather than crashing.

`_log_incoming(incoming: IncomingMessage) -> None`
Pure logging — masks the sender's phone to last 4 digits, prints trace ID, first name, tenant, and message text to stdout.

`async def _resolve_quoted_caption(incoming: IncomingMessage) -> None`
No-op if `incoming.quoted_message_id` is unset. Otherwise looks up the original message text via `db.session_store.get_reply_by_message_id`, truncates to 200 chars, stores it on `incoming.quoted_caption`, and prepends `"[Quoting: {preview}]\n"` to `incoming.text` so downstream LLM calls see the quoted context inline. Failures are caught and logged, non-fatal.

### Example
An inbound WhatsApp text "yes" arrives with `phone_number_id="1124766240726230"`. `setup_pipeline` resolves the tenant to `tenant_inventaa_led_001`, copies over GST rate, prompts, quick actions. Dedup passes, history returns the last 10 turns, no quoted message. Lock is acquired for `session_id`. In parallel, negotiation state (`awaiting_invoice_confirmation=True`) is fetched and the inbound "yes" message is saved to the `messages` table. Returns `(True, [...10 turns...], {"awaiting_invoice_confirmation": True, ...})`; `main.py` proceeds to classify intent and dispatch, and is now responsible for calling `release_lock` in its `finally` block.

### How it connects
- Imports: `models.schemas.IncomingMessage`, `db.session_store` (`resolve_tenant_id`, `is_duplicate`, `get_session_history`, `save_message`, `get_negotiation_state`, and inside `_resolve_quoted_caption`, `get_reply_by_message_id`), `db.processing_lock` (`acquire_lock`, and `release_lock` inside the exception handler), `db.prompt_store.get_prompt` (inside `_resolve_media_unsupported_marker`).
- Called by: `main.py` — `run_pipeline()` calls `await setup_pipeline(incoming)` as Steps 1-5 (confirmed sole caller via grep).

---

## pipeline/router.py

### Why this file exists
This is the central intent-dispatch layer that sits between intent classification (`ai/handlers.py`) and the specialist handlers (`ai/negotiator.py`, `ai/invoice_handler.py`, `ai/graphrag_handler.py`). It exists to keep `main.py` a thin orchestrator: all "what do we do with this message" branching — negotiation-state guards, invoice-confirmation guards, dynamic-knowledge interception, and the final GraphRAG/greeting/escalation/unknown routing — lives here instead of being scattered across handlers. Without it, every handler would need to independently re-check negotiation/invoice state, causing duplicated (and inconsistent) guard logic. All `ai.*` imports are deliberately local (inside functions) to avoid circular imports, since `ai/` modules also import back into pipeline-adjacent helpers.

### How it works

Module-level: `_client` is a single reused `AzureOpenAI` client (matching the singleton pattern in `ai/negotiator.py` and `ai/product_followup.py`) — avoids reconnecting per call. It's wrapped with `wrap_llm_client` from `ai/request_profiler.py` so every completion call is timed into the request profiler automatically.

`_call_graphrag_timed(incoming, session_history) -> str`
Thin timing wrapper around `ai.graphrag_handler.call_graphrag_api()`. Records wall-clock time into the `"graphrag"` profiling bucket via `ai.request_profiler.add()`, without decomposing GraphRAG's internals. Returns whatever `call_graphrag_api` returns.

`async def dispatch(incoming, result, session_history: list) -> str`
The main routing function, called once per message from `main.py`. Logic in order:
1. **Neg-state caching**: if `incoming._cached_neg_state` isn't already set (normally it is, from `pipeline/setup.py`), fetches it via `db.session_store.get_negotiation_state`.
2. **Dynamic Knowledge Resolution Intercept Guard**: reads `incoming._routing` (a `RoutingDecision` computed during intent classification). If `needs_product_context` is true and a `requested_knowledge_field` was extracted (e.g. "warranty", "installation"), this is a request about an already-established product context ("is it waterproof") rather than a new search — the guard intercepts regardless of `intent`.
3. **ContextBuilder assembly**: builds `arc.llm_context` via `ai.context_builder.ContextBuilder(arc).build()`, but skips this for `GREETING`/`HUMAN_ESCALATION` intents (their handlers don't read `arc.llm_context`) unless the dynamic-knowledge guard needs it anyway.
4. **Dynamic-knowledge resolution** (only if `_wants_dynamic_knowledge`): looks up the last GraphRAG response JSON for the session (`db.session_store.get_latest_graphrag_response`), fuzzy-matches `arc.resolved_product` against the returned product list, falls back to `db.session_store.get_cached_product_by_name` if not found. Loads per-tenant field-alias config (`product_field_aliases` prompt, JSON) via `db.prompt_store.get_raw_prompt` to map logical field names (e.g. "specifications") to actual product-cache keys. Resolves the value (`val`) from the matched product dict, with special-casing for `installation` (checks `installation_url`/`pdf_url`, or parses a JSON blob) and for dict-shaped values (unwraps `url`/`pdf_url`/`value`). If a URL value is found, returns a DB prompt (`asset_success_{field}_prompt` or `followup_installation_header_prompt`) with the link. If a text value is found, it's run through the LLM (`knowledge_asset_answer_prompt` as system prompt, `incoming.text` as user turn, temperature 0, max 250 tokens, via `run_in_executor` since the Azure SDK client is sync) to answer the specific question rather than dumping raw text; falls back to the raw template on LLM failure. If no cached value is available or the cache needs refresh (`kstate.needs_refresh`), it temporarily rewrites `incoming.text` to `"Product: {resolved_product}. Query: {original_text}"`, calls GraphRAG via `_call_graphrag_timed` (which updates `product_cache` as a side effect), restores `incoming.text`, and if GraphRAG returned a reply, returns it directly. Otherwise rebuilds `arc.llm_context` with `force_refresh=True` and falls through to `ai.knowledge_accessor.KnowledgeAccessor.get()` to fetch a structured asset (image/document/text) and deliver it — images are sent directly via `messaging.send_image` and logged with `db.session_store.save_outbound_message` (returning `""` since the reply was already sent as a side effect); documents/text return DB-prompted messages with the URL/value.
5. **Guard 1 — `_neg_guard`**: called next; if it returns a non-empty reply, dispatch returns immediately.
6. **Guard 2 — `_invoice_guard`**: same pattern — only fires if the bot is actually expecting a confirmation.
7. **Main intent routing**: `GREETING` → `ai.handlers.handle_greeting`; `HUMAN_ESCALATION` → `ai.handlers.handle_escalation`. Otherwise loads `_min_conf` (tenant override or `DEFAULT_INTENT_MIN_CONFIDENCE`). A routing gate blocks `MODIFY_WORKFLOW`-operation messages (e.g. "add 3 more units") from reaching GraphRAG when there's no active workflow state (`_cached_neg_state` or `db.session_store.get_last_discussed_product`) — returns a DB-driven `no_active_workflow_prompt` (with hardcoded English fallback) instead. History-related queries (`needs_customer_history` + no specific field requested) are intercepted by `ai.customer_history_handler.handle_customer_history_query`. Finally, intents in `_graphrag_intents` (`FAQ_KNOWLEDGE`, `WORKFLOW_ACTION`, `FIND_PRODUCT`, `BROWSE_CATEGORY`, `GET_PRODUCT_INFO`, `GET_ADVICE`, `CHECK_POLICY`) or low-confidence results fall through to `_call_graphrag_timed`; anything else goes to `ai.handlers.handle_unknown`.

`_load_fast_confirm_prompt(incoming) -> str`
Loads the `fast_order_confirm_check_prompt` from DB via `db.prompt_store.get_prompt`. One-liner helper for `_check_fast_confirm`.

`_has_invoice_keyword(incoming) -> bool`
Pre-filter to avoid an LLM call on every message. Reads the tenant's `quick_actions["INVOICE_INQUIRY"]` phrase list; if not configured, returns `True` (safe fallback — let the LLM decide). Otherwise checks case-folded substring match of any keyword in `incoming.text`.

`async def check_is_shipping_address(incoming) -> bool`
Used when negotiation state is `awaiting_address_and_confirm`. Rejects trivially short input (<4 chars) as not an address. Otherwise calls the LLM with `validate_shipping_address_prompt` (loaded via `db.prompt_store.get_prompt`), expects a JSON response like `{"is_shipping_address": bool, "reason": str}`, strips Markdown code fences if present, and parses it. On any exception, defaults to `True` (fail open — treats the ambiguous message as an address rather than blocking checkout).

`async def _neg_guard(incoming, result, session_history: list) -> Optional[str]`
Implements the 3-phase negotiation state machine described in its docstring: NEGOTIATING → COUNTER_OFFER_PRESENTED → AWAITING_CONFIRMATION. Uses the cached (or freshly fetched) `pre_neg_state`; returns `None` immediately if there's no negotiation state at all. Special-cases `awaiting_address_and_confirm`: validates the message is a shipping address via `check_is_shipping_address`; if not, clears that flag and returns `None` to let normal routing continue; if it is, updates the order to `CONFIRMED` with the address (`db.product_store.update_order_status_and_address`), reconstructs invoice fields from `neg_state` via `ai.pricing.PricingResult.from_neg_state`, clears negotiation state (`db.session_store.clear_negotiation_state`), and calls `ai.invoice_handler.handle_invoice_request` to generate/send the invoice. Otherwise, uses `_routing.needs_workflow_state` (or a fallback check against `HUMAN_ESCALATION`/`FAQ_KNOWLEDGE`/`GREETING`) to decide whether to bypass the guard entirely — most of the actual negotiation-phase branching is delegated onward (this function mostly acts as an early-exit gate; the real phase logic lives in `_resume_negotiation` and `_handle_qty_confirm_split`).

`async def _handle_qty_confirm_split(incoming, neg_state: dict, session_history: list) -> Optional[str]`
Handles the case where a customer changes quantity while `awaiting_invoice_confirmation=True`. Detects a quantity delta via `ai.negotiator.detect_quantity_change`; if none, returns `None`. Otherwise resets `awaiting_invoice_confirmation=False`, fetches tenant offers/cached product data, and calls `ai.negotiator.handle_negotiation` to regenerate the summary at the new quantity. Saves the updated state with `awaiting_invoice_confirmation=True` again. Then checks for a **compound intent** — "add 1 unit AND confirm" — via `ai.negotiator.detect_acceptance`; if present, skips straight to `_confirm_negotiated_order` instead of showing the summary screen again. Returns the negotiation reply text, or `None` on any exception (non-fatal — falls through to other guards).

`async def _resume_negotiation(incoming, neg_state: dict, session_history: list) -> Optional[str]`
Re-enters active negotiation for a message. Bails to `_call_graphrag_timed` if there's no product/price in state. Calls `ai.negotiator.handle_negotiation` with `awaiting_invoice_confirmation` reset to `False`. If the negotiator signals `defer_intent` (message wasn't actually negotiation-related, e.g. an escalation mid-negotiation), saves state and returns `None` so `main.py`'s normal routing takes over, stashing `incoming._deferred_intent` for accurate debug reporting. If `order_ready` and `agreed_price` are set: if there's a reply string, either saves a fast-confirm-ready state (if negotiator already flagged `awaiting_invoice_confirmation`) or transitions to `counter_offer_presented=True` (Phase 2) and returns the counter-offer reply. If there's no reply string, computes subtotal/GST/total, marks `awaiting_invoice_confirmation=True`, and returns `_build_order_summary(...)`. If not `order_ready`, just persists state and returns the negotiator's reply as-is.

`async def _invoice_guard(incoming, session_history: list) -> Optional[str]`
Only treats a message as an order confirmation if `pre_neg_state.awaiting_invoice_confirmation` is `True` — a deliberate bug fix (previously any message could be misclassified as confirmation). Independently checks for an explicit invoice inquiry (`_is_invoice_inquiry`), gated by the cheap `_has_invoice_keyword` pre-filter to skip the LLM call (~2.5s) on unrelated messages. When `awaiting_conf` is true, checks `_check_fast_confirm` (phrase match, cheap) first, and only falls back to the LLM-based `_is_invoice_confirmation_request` if the fast check fails (short-circuit optimization). If any of the three checks is true: if state has both `quantity` and `last_offer_price`, calls `_transition_to_address_and_payment`; otherwise calls `ai.invoice_handler.handle_invoice_request(incoming)` directly (e.g. plain "send my invoice" with no active negotiation).

`async def _transition_to_address_and_payment(incoming, neg_state: dict) -> str`
Builds a `PricingResult` from state (`ai.pricing.PricingResult.from_neg_state`), creates a `PENDING_PAYMENT` order via `ai.order_service.complete_order`, builds a fake payment link (`https://demo.payment.gateway/pay?order_id=...` — placeholder, not a real gateway integration), renders the `neg_payment_and_address_request_prompt` DB prompt with the order/payment details, and saves negotiation state with `awaiting_address_and_confirm=True` / `awaiting_invoice_confirmation=False` / `pending_order_id` set. Also updates `incoming._cached_neg_state` so subsequent guard checks in the same request see the fresh state.

`async def _check_fast_confirm(incoming, neg_state) -> bool`
Returns `False` immediately if not `awaiting_invoice_confirmation`. Checks `utils.conversation_actions.is_quick_confirm` (tenant-configured exact-phrase match, e.g. "yes"/"confirm") first — zero LLM cost. Otherwise calls the LLM with the `fast_order_confirm_check_prompt` (max 5 tokens, temperature 0) and checks if the response contains `"YES"`. Returns `False` on any exception (fails closed — doesn't accidentally confirm an order).

`async def _confirm_negotiated_order(incoming, neg_state: dict) -> str`
Finalizes a negotiated order into a real order + invoice. Resolves the agreed price with an explicit-`None` guard chain (rounds>0+last_price → auto_price → last_price → 0.0). Recomputes canonical pricing via `ai.pricing.PricingResult.from_neg_state` (single source of truth — no recalculation elsewhere). Calls `ai.order_service.complete_order` to create the order, then `db.session_store.clear_negotiation_state`. Fire-and-forget (via `asyncio.create_task`, wrapped in try/except so it never blocks): clears stale post-order context (`db.session_store.clear_post_order_context`) and saves the negotiation outcome to Postgres for future personalization (`_save_neg_outcome_async`). If `complete_order` returned `None` (creation failed), falls back to `ai.invoice_handler.handle_invoice_request(incoming)` without a negotiated order; otherwise passes the new order into `handle_invoice_request(incoming, negotiated_order=new_order)`.

`_build_order_summary(incoming, product, agreed_price, qty, sub, gst, tot, state) -> str`
Pure string-building function — computes store-discount savings (`s_save`) vs. negotiation savings (`n_save`) vs. total savings (`tot_save`) from `state`. Chooses one of three DB prompt templates based on which discounts actually apply (`order_summary_full_discount_prompt`, `order_summary_store_discount_only_prompt`, `order_summary_plain_price_prompt`), each with a hardcoded English fallback if the tenant hasn't seeded that prompt. Appends a savings line and a footer ("Reply Confirm...") similarly sourced from DB with fallback.

`async def _save_neg_outcome_async(tenant_id, session_id, product, opening_price, final_price, rounds, accepted, quantity) -> None`
Fire-and-forget helper — instantiates `db.customer_data_service.CustomerDataService` and calls `save_negotiation_outcome`. Swallows all exceptions with a print — never allowed to affect the invoice-delivery path.

### Example
A customer who was shown a product and told "Rs.500 discount available" sends "ok I'll take 5 units at that price". `main.py` calls `dispatch(incoming, result, session_history)`. `incoming._cached_neg_state` shows `rounds=1, awaiting_invoice_confirmation=False`. `_neg_guard` sees `needs_workflow_state=True` and lets it fall through; `_resume_negotiation` is invoked, calls `ai.negotiator.handle_negotiation`, gets back `order_ready=True, agreed_price=500, quantity=5`, no negotiator reply text, computes `sub=2500, gst=450, tot=2950`, saves `awaiting_invoice_confirmation=True`, and returns `_build_order_summary(...)` — e.g. "Here's your updated order summary... Reply *Confirm* to place your order...". The customer then replies "Confirm" — `dispatch` runs `_invoice_guard`, `_check_fast_confirm` phrase-matches "confirm" against the tenant's `ORDER_CONFIRM` quick actions, returns `True` instantly (no LLM), and since `quantity`+`last_offer_price` are set, `_transition_to_address_and_payment` runs, asking for a shipping address and payment link.

### How it connects
- Imports (local, inside functions) from: `ai.graphrag_handler` (`call_graphrag_api`), `ai.request_profiler` (`wrap_llm_client`, `add`), `ai.context_builder.ContextBuilder`, `ai.knowledge_accessor.KnowledgeAccessor`, `ai.handlers` (`handle_greeting`, `handle_escalation`, `handle_unknown`, `DEFAULT_INTENT_MIN_CONFIDENCE`), `ai.customer_history_handler.handle_customer_history_query`, `ai.negotiator` (`handle_negotiation`, `detect_quantity_change`, `detect_acceptance`, `_client`, `AZURE_OPENAI_DEPLOYMENT`), `ai.invoice_handler` (`handle_invoice_request`, `_is_invoice_inquiry`, `_is_invoice_confirmation_request`), `ai.pricing.PricingResult`, `ai.order_service.complete_order`, `ai.customer_data_service.CustomerDataService` (sic — via `db.customer_data_service`), `db.session_store` (many functions), `db.prompt_store` (`get_prompt`, `get_raw_prompt`), `db.product_store.update_order_status_and_address`, `messaging.send_image`, `utils.conversation_actions.is_quick_confirm`. Top-level imports: `config` (Azure OpenAI settings), `openai.AzureOpenAI`.
- Called by: `main.py` (`run_pipeline` calls `dispatch(incoming, result, session_history)` at Step 5) — confirmed the only production caller via grep.
- `dispatch()` is the sole entry point other modules use from this file.

---

# Part 4 — AI Layer (Core Conversation Logic)

## ai/handlers.py

### Why this file exists
Consolidates the core "simple" LLM-driven reply handlers — intent classification, greeting, escalation, and unknown-fallback — that don't need product/pricing/negotiation logic. The file header states it explicitly replaces two older modules (`ai/intent_router.py`, `ai/response_handlers.py`) and that a former entity-extraction responsibility was removed as dead code. It enforces a hard rule: no hardcoded prompt text lives here — every system prompt is fetched via `db.prompt_store.get_prompt(incoming, key)`, and a missing DB prompt is a loud `RuntimeError`, not a silent fallback (contrast with `invoice_handler.py`/`pricing.py`, which do have hardcoded fallbacks).

### How it works
Module-level: `_client` (AzureOpenAI, 30s timeout, 0 retries) wrapped by `wrap_llm_client` for "llm" profiling. `DEFAULT_VALID_INTENTS` is the fallback set of allowed intent labels when a tenant hasn't configured its own `incoming.valid_intents`. `DEFAULT_INTENT_MIN_CONFIDENCE = 0.50` is the fallback confidence floor.

- `async classify_intent(customer_message, session_history=None, incoming=None) -> IntentResult` — loads `intent_system_prompt` from DB (raises `RuntimeError` if unset — not caught, so it propagates). Builds a message list (system + optional session history + user message). Dynamically loads `max_tokens` from `db.session_store.get_tenant_config(tenant_id, "intent_classifier_config")` (defaults to 400 if missing/fails). Calls the LLM at temp=0, parses JSON response for `intent`/`confidence_score`, clamps confidence to [0,1]. If `"operation"` key is present in the parsed JSON, also builds a `RoutingDecision` (operation, needs_graphrag, needs_customer_context — accepts either `needs_customer_context` or legacy `needs_memory` key, needs_workflow_state, needs_product_context, needs_customer_history, knowledge_domain default "product", requested_knowledge_field). Applies tenant-specific `intent_min_confidence` override (falls back to `DEFAULT_INTENT_MIN_CONFIDENCE`); if intent isn't in `valid_intents` or confidence is below threshold, forces `intent="UNKNOWN", conf=0.0`. On `json.JSONDecodeError` or generic `Exception`, logs and falls through to return `UNKNOWN`/0.0 — but `RuntimeError` (missing prompt) is explicitly re-raised so it stays visible instead of being silently swallowed into "UNKNOWN".
- `_get_time_greeting(timezone_str: str) -> tuple` — resolves the tenant's IANA timezone via `zoneinfo.ZoneInfo` (falls back to UTC on bad/missing tz string), and returns `("morning"|"afternoon"|"evening", "Good morning"|"Good afternoon"|"Good evening")` based on current local hour (<12, <17, else).
- `async handle_greeting(incoming) -> str` — builds `greeting_system_prompt` with `time_of_day`, `time_greeting`, `sender_name`, `biz_name` injected. Calls LLM at temp=0.7, expects JSON `{"reply": "...", "type": "..."}`. Raises `ValueError` (converted to `RuntimeError`) if `reply` is empty or content is missing — this handler has no hardcoded fallback text at all, unlike invoice/pricing handlers.
- `async handle_escalation(incoming) -> str` — builds `escalation_prompt` with `sender_name`/`biz_name`, calls LLM at temp=0.7, returns raw stripped text (not JSON-parsed, unlike greeting). Raises `RuntimeError` on any failure.
- `async handle_unknown(incoming) -> str` — same pattern as escalation but with `unknown_prompt`; used as the final catch-all reply.

### Example
`await classify_intent("show me gate lights", session_history=[], incoming=incoming)` → LLM returns JSON like `{"intent": "FIND_PRODUCT", "confidence_score": 0.92, "operation": "SEARCH", "needs_graphrag": true}` → function returns `IntentResult(intent="FIND_PRODUCT", confidence_score=0.92, raw_text="show me gate lights", routing=RoutingDecision(operation="SEARCH", needs_graphrag=True, ...))`.

### How it connects
- Imports: `config` (Azure creds), `models.schemas.IntentResult`/`RoutingDecision`/`IncomingMessage`, `db.prompt_store.get_prompt`, `db.session_store.get_tenant_config` (deferred), `ai.request_profiler.wrap_llm_client`.
- Called by: `main.py` (direct `classify_intent(...)` call in the live `run_pipeline`), `ai/orchestrator.py::_classify_intent` (the dead orchestrator path), `ai/negotiator.py` (`classify_intent` used for a deferred re-classification), `pipeline/router.py` (`handle_greeting`, `handle_escalation`, `handle_unknown` dispatched by intent), `ai/product_followup.py` (`handle_greeting`, `handle_escalation` as fallback replies within a follow-up flow).

---

## ai/request_context.py

### Why this file exists
Defines the single context object (`AIRequestContext`) meant to flow through the entire pipeline per request, replacing the older pattern of passing `incoming`, pricing, workflow, and tenant data as many separate positional arguments to every handler. Also defines `PromptContext`, the assembled semantic-context bundle used for LLM prompt injection. Unlike `ai/orchestrator.py` and `ai/prompt_builder.py` (see Dead Code appendix), **this file is very much alive** — `AIRequestContext` is constructed directly in `main.py` (bypassing the dead `AIOrchestrator.create()`) and consumed throughout `pipeline/router.py`.

### How it works
- `@dataclass PromptContext` — plain-string/dict container: `product_context`, `customer_preferences`, `negotiation_profile`, `workflow_context`, `conversation_summary`, `customer_context`, `active_product_session: bool`, `resolved_product: Optional[str]`, `knowledge_state: dict`, `knowledge_context: dict`.
  - `to_dict(self) -> dict` — flattens all fields into a plain dict (used for both DB metrics logging and prompt variable injection).
  - `has_content(self) -> bool` — `True` if any of the six string fields is non-empty, OR if `knowledge_state.get("available")` is truthy. Used by `AIOrchestrator._record_metrics` (dead path) to log whether context was actually assembled.
- `@dataclass AIRequestContext` — holds `incoming` (the `IncomingMessage`, treated as immutable), `result` (intent classification), `session_history: list`, `llm_context: PromptContext` (default empty, filled in later by `ContextBuilder`), `neg_state: Optional[dict]`, `workflow_state: Optional[dict]`, `pricing: Optional[Any]`, `resolved_product: Optional[str]`, `customer_context: str`, and `_updates: dict` (deferred writes flushed at pipeline end).
  - `tenant_id` (property) -> str — `self.incoming.tenant_id`.
  - `session_id` (property) -> str — `self.incoming.session_id`.
  - `sender_name` (property) -> str — `self.incoming.sender_name`.
  - `text` (property) -> str — `self.incoming.text`.
  - `intent` (property) -> str — `self.result.intent` if `self.result` is set, else `"UNKNOWN"`.
  - `workflow` (property) -> str — derives a coarse phase label from `neg_state`: if `neg_state.rounds > 0`, returns `"CONFIRMING"` (if `awaiting_invoice_confirmation`), `"COUNTER_PRESENTED"` (if `counter_offer_presented`), else `"NEGOTIATING"`; else if `neg_state.quantity` is set, returns `"ORDERING"`; else defaults to `"BROWSING"`. Used by `ContextBuilder` to pick the right memory-fetch strategy.
  - `current_product` (property) -> str — reads `product_name` from `neg_state` first, falling back to `workflow_state`, else `""`.
  - `queue_update(self, key, value) -> None` — writes into `self._updates[key]` (a deferred-write mechanism; the actual flush logic is only exercised by the dead `AIOrchestrator.finalize` path).
  - `get_prompt_vars(self) -> dict` — merges `self.llm_context.to_dict()` with `sender_name` and `biz_name` (from `incoming`, default `""`); a compact alternative to manually spreading context each time.

### Example
`main.py` constructs `arc = AIRequestContext(incoming=incoming, result=result, session_history=session_history, neg_state=_neg)` right after intent classification, then caches it as `incoming._cached_arc = arc` so `pipeline/router.py::dispatch()` can retrieve it later in the same request without rebuilding it, and then conditionally does `arc.llm_context = await ContextBuilder(arc).build()`.

### How it connects
- Imports: nothing beyond stdlib (`dataclasses`, `typing`) — deliberately dependency-light so it can be imported broadly without circularity risk.
- Called by: `main.py` (constructs the live `arc`), `pipeline/router.py` and `ai/context_builder.py` (read/write `arc.llm_context`, `arc.resolved_product`, etc.), `db/prompt_store.py` (constructs its own `AIRequestContext`), `ai/orchestrator.py` (dead path also constructs one), `scratch/test_dynamic_knowledge.py`.

---

## ai/context_builder.py

### Why this file exists
Centralizes the assembly of `PromptContext` — the structured bundle of product/customer/workflow/conversation context handed to LLM prompts across the pipeline — so that every intent handler doesn't need its own ad hoc logic for deciding which memory types to fetch and how to format them. It exists as "ContextBuilder v3" (per the header) to decouple *which* context is relevant for a given (intent, workflow) pair (configurable per-tenant via a `memory_strategy` DB table) from the actual data-fetching (delegated to `CustomerDataService` and `ProductContextResolver`), and to cache the assembled result on the request object so it's computed once per request unless explicitly force-refreshed.

### How it works
Module-level: `_STRATEGY_CACHE` (in-memory dict, 5-minute TTL via `_STRATEGY_TTL = 300`) avoids re-querying the `memory_strategy` table on every request. `_DEFAULT_STRATEGY` is a hardcoded fallback mapping `(intent, workflow) -> list[str]` of memory-type names (e.g. `("NEGOTIATION", "*") -> ["negotiation_outcome", "workflow_snapshot", "product_context"]`), used when no matching per-tenant DB row exists — this is the one deliberately-hardcoded piece of business logic in an otherwise DB-driven file, functioning as the safety-net default.

- `_load_strategy_from_db(tenant_id) -> dict` — Reads and caches the tenant's `memory_strategy` table (columns: `intent, workflow, memory_types, max_results, enabled`, filtered `enabled=True`, ordered by `priority`) into a `{(intent, workflow): [type, ...]}` dict, splitting the comma-separated `memory_types` string. Returns `{}` (silently) on any DB error — never raises, so a strategy-table outage just means the default strategy is used for everyone.
- `_get_memory_types(tenant_id, intent, workflow) -> list[str]` — Merges DB strategy over the hardcoded default (`{**_DEFAULT_STRATEGY, **db}`) and looks up in priority order: exact `(intent, workflow)` → `(intent, "*")` → `("*", workflow)` → `("*", "*")`. Notably, this function is **defined but never called** within `build()` — the memory-type gating actually implemented in `build()` uses a simpler two-branch `_needs_customer_fetch` boolean derived from `routing` flags rather than iterating this returned list; this looks like either dead code or a hook for future use.

- `class ContextBuilder`:
  - `__init__(self, arc)` — Stores `arc` (an `AIRequestContext`), plus `tenant_id`/`session_id` pulled from it.
  - `async build(self, max_results=3, force_refresh=False) -> PromptContext` — The main method:
    1. **Request-scoped cache check**: keyed by `f"_ctx_{arc.intent}_{arc.workflow}"` stashed as an attribute directly on `arc`; skipped if `force_refresh=True` (needed when the caller just wrote fresh data — e.g. GraphRAG updating `product_cache` — and needs to re-read it within the same request rather than get a stale pre-write `PromptContext`).
    2. **Customer-fetch gating**: reads `routing = arc.result.routing`. If `routing` is `None` (e.g. main.py skipped `classify_intent` for a NEG BYPASS / awaiting-quantity fast path), defaults to `_needs_customer_fetch=True` (fetch everything). Otherwise only fetches customer profile/history data if `routing.needs_customer_context` or `routing.needs_customer_history` is set — because the other `PromptContext` fields this fetch would populate (`customer_preferences`, `negotiation_profile`, `conversation_summary`) aren't actually consumed by any live prompt yet (`product_followup.py`'s call hardcodes them to `""`), so skipping the fetch removes DB round-trips with no behavior change. **Product-context resolution is always run unconditionally** regardless of this flag — `routing.needs_product_context` doesn't reliably track when `arc.resolved_product` is actually needed in production, so gating it would risk breaking installation/warranty/spec lookups.
    3. Runs `_resolve_product_context()` and (if needed) `_resolve_customer_context(cds)` concurrently via `asyncio.gather` when both are needed; otherwise only runs the product-context resolution and defaults customer values to `{}, {}, ""`.
    4. Sets `self.arc.resolved_product = pname`.
    5. **Formats `product_str`**: uses tenant-configurable format-string templates read off `incoming` (`cb_product_format`, default `"{name} | Rs.{price}"`), doing manual `.replace()` substitution (not `.format()`) for `{name}`, `{price}`, `{warranty}`, `{waterproof}`, prefixed with `cb_product_marker` (default `"PRODUCT_CONTEXT:"`).
    6. **Formats `workflow_str`**: only populated if `self.arc.neg_state` is truthy; similarly uses `cb_workflow_format_state`/`cb_workflow_format_product`/`cb_workflow_format_price` templates and a `cb_workflow_marker` prefix.
    7. **Formats `prefs_str`**: joins non-empty `prefs` dict entries, prefixed by `cb_preferences_prefix` (default `"Preferences - "`).
    8. **Formats `neg_str`**: only if `summary.get("avg_negotiation_discount_pct")` is not None — builds a sentence like `"Typically accepts 12% discount in 3.2 rounds, budget Rs.900 - Rs.1400"` by aggregating the customer's historical `_negotiations` list (average rounds, min/max accepted final price when ≥2 accepted negotiations exist).
    9. **`customer_context`**: only built (via `_build_customer_context_text`) if `_customer_flags_true` (same flag as the fetch gate). Result is stashed on `self.arc.customer_context` too (read elsewhere, e.g. by `graphrag_handler.py`'s query enrichment).
    10. Constructs and returns a `PromptContext` dataclass with all the above plus `active_product_session`, `resolved_product`, `knowledge_state`, `knowledge_context` from the product-context resolution — then caches it on `arc` under the cache key from step 1.

  - `async _resolve_product_context(self)` — Resolves the single "active" product name via `ProductContextResolver.resolve(...)` (delegated entirely to `ai/product_context_resolver.py`). If a product name resolves, concurrently fetches (`asyncio.gather`) `get_last_discussed_product`, `get_graphrag_product_selection`, and `get_cached_product_by_name`. Computes `active_product_session` as True if the resolved product matches the negotiation state's product, the last-discussed product, or any item in the current GraphRAG selection. If cached product data exists, determines cache freshness using a tenant-configurable `knowledge_refresh_policy.ttl_hours` (default 24) — but explicitly **skips the TTL check and trusts the cache** if `active_product_session` is True (within a live product conversation), otherwise parses `_cached_at` and flags `ttl_expired`/`missing_timestamp`. Builds a large `knowledge_context["product"]` dict normalizing several possible field-name variants.
  - `async _resolve_customer_context(self, cds)` — Fetches `cds.get_customer_summary()` and the last 2 turns of session history, extracting only `role=="user"` turns' content into `conv_str`. Catches and logs (non-fatally) any history-fetch failure.
  - `async _build_customer_context_text(self, cds, summary) -> str` — Pure formatting function assembling up to 5 text sections (Preferences, Customer Profile Summary, Completed Orders, Negotiation History, Past Offers) from already-fetched `summary` data — reuses data already inside `summary` and only calls one *new* query, `cds.get_offer_history(limit=5)`.

### Example
`ContextBuilder(arc).build()` where `arc.intent="FAQ_KNOWLEDGE"`, `arc.workflow="NEGOTIATING"`, `arc.neg_state={"product_name": "Romy 12W", "quantity": 10, "offer_price": 1100}`, and `arc.result.routing.needs_customer_context=False`: skips the customer-profile fetch entirely, still resolves the active product (Romy 12W), builds `product_str = "PRODUCT_CONTEXT: Romy 12W Wall Light | Rs.1200"` and `workflow_str = "WORKFLOW_SNAPSHOT: State: NEGOTIATING - Romy 12W x10 @ Rs.1100"`.

### How it connects
- Imports from: `ai.request_context` (`AIRequestContext`, `PromptContext`), `db.customer_data_service.CustomerDataService`, `db.session_store` (`get_cached_product_by_name`, `get_last_discussed_product`, `get_graphrag_product_selection`, `get_tenant_config`, `get_session_history`, `_get_client`), `ai.product_context_resolver.ProductContextResolver`.
- Called by: `ai/orchestrator.py` (dead path), `pipeline/router.py` (two call sites — one plain `build()`, one `build(force_refresh=True)` after a write), `db/prompt_store.py` (imports and instantiates for prompt-variable resolution), `ai/customer_profile_updater.py` (dead path).
- `graphrag_handler.py` reads the output of this module indirectly via `incoming._cached_arc.llm_context` and `incoming._cached_arc.customer_context`, both populated here.

---

## ai/product_context_resolver.py

### Why this file exists
Solves "what product is the customer currently talking about?" when they say something like "I want 5 of those" without naming a product — needed because negotiation, quantity capture, and follow-up Q&A all depend on knowing the active product, and that context can come from several different places depending on where the conversation currently is (an in-progress negotiation, a product shown earlier in the session, or a past completed order). Centralizing this waterfall avoids each caller reimplementing its own fallback order. *(Project memory note: an earlier version of this logic used `routing.operation` as a same-product-vs-new-search proxy, which was a bug — that check must never reappear here or in `product_followup.py`.)*

### How it works
Module-level: `_NOT_PROVIDED: Any = object()` — a sentinel distinguishing "caller didn't pass `incoming_cached_neg`" (so we should fetch negotiation state fresh) from "caller explicitly passed `None`" (already fetched, genuinely no active negotiation). A plain `None` default couldn't distinguish these two cases, which used to cause a redundant `get_negotiation_state()` DB round-trip on every message for the common non-negotiating customer.

- `ProductContextResolver.resolve(tenant_id, session_id, incoming_cached_neg=_NOT_PROVIDED, incoming=None, session_history=None) -> Optional[str]` (staticmethod) — four-step waterfall:
  0. **Index selection**: if `incoming` and `incoming.text` are given, fetches the active `PRODUCT_SELECTION` list via `get_graphrag_product_selection`; if one exists, calls `ai.product_followup._parse_followup_message(incoming, selection, session_history)` (deferred import — an LLM-backed, DB-prompt-driven parse, not a hardcoded regex) to see if the message resolves to a specific product from that list (e.g. "the second one"). If it resolves, fires a background `asyncio.create_task(save_last_discussed_product(...))` and returns the product name immediately.
  1. **Active negotiation**: if `incoming_cached_neg is _NOT_PROVIDED`, fetches fresh via `get_negotiation_state`; otherwise uses the passed-in cached value directly. If a negotiation state exists and has a `product_name`, returns it.
  2. **Last discussed product**: falls back to `get_last_discussed_product(tenant_id, session_id)`.
  3. **Latest completed order**: falls back to `CustomerDataService(tenant_id, session_id).get_latest_ordered_product()`, wrapped in a try/except that logs and continues (returns `None` overall) on failure.
  - Returns `None` if none of the four steps found anything.

### Example
Customer previously saw a list of 3 products from a GraphRAG search, then says "2nd one please" — step 0 parses this via `_parse_followup_message`, resolves to the 2nd product's name, saves it as the last-discussed product in the background, and returns it immediately without needing to fall through to negotiation/order-history checks.

### How it connects
- Imports: `db.session_store.get_negotiation_state`/`get_last_discussed_product`/`get_graphrag_product_selection`/`save_last_discussed_product` (deferred), `db.customer_data_service.CustomerDataService`, `ai.product_followup._parse_followup_message` (deferred, avoids circular import).
- Called by: `ai/context_builder.py` (the live path, feeding `arc.resolved_product`), `ai/graphrag_request_builder.py` (dead path also calls it).

---

## ai/graphrag_handler.py

### Why this file exists
Owns all direct integration with the external GraphRAG (Neo4j/LangChain-based) product-search API: building the outbound query, calling the HTTP endpoint, and turning GraphRAG's various response shapes (structured product list, clarification request, plain text, malformed Python-dict-as-string, transient error) into safe, tenant-branded WhatsApp replies. It exists as a separate module to keep `main.py` thin, and because GraphRAG's response format is inconsistent enough (three different shapes, occasional Python `str(dict)` instead of JSON, occasional raw error text) that isolating the defensive parsing logic in one file prevents that fragility from leaking into the rest of the pipeline.

### How it works

Module-level: own `AzureOpenAI` client (30s timeout, no retries, not wrapped by `request_profiler` here — unlike `product_followup.py`/`negotiator.py`).

- `_load_category_prompt(incoming, cat_list) -> str` — Thin wrapper fetching `category_matcher_prompt` from DB with the numbered category list interpolated in.
- `_reply_prompt(incoming, key, fallback, **kwargs) -> str` — The file's standard pattern for every customer-facing string: try `get_prompt(incoming, key, **kwargs)`, catch `RuntimeError` and return `fallback` (a plain-English literal) if the tenant hasn't seeded that prompt key yet.

- `async _send_product_card(incoming, position, p) -> None` — Sends a single product image card (caption includes position, name, price, discount-vs-regular-price, rating/reviews) via `send_image`, or a text-only card via `send_reply` if no `image_url`. The subsequent `save_outbound_message` audit-log write is fired via `asyncio.create_task` (fire-and-forget, non-blocking). Designed to be run in parallel across multiple products via `asyncio.gather`.

- `async _send_structured_product_list(incoming, products) -> str` — Builds and sends the full product-list response for a GraphRAG structured (list-of-dicts) result. If exactly one product, fires a background task to save it as `last_discussed_product`. Builds per-SKU batch cache entries and fires three background DB writes (`save_product_api_responses_batch`, `save_graphrag_product_selection`, and — if any product carries `global_offers` — `save_tenant_offers`) all via `asyncio.create_task`, explicitly to keep them off the critical response path (~400-600ms savings). Sends up to `incoming.max_image_products` (default 3) product image cards concurrently via `asyncio.gather(..., return_exceptions=True)`. Builds a numbered text summary (header/footer from DB prompts), truncating to 4096 chars if needed. Returns the summary text (image cards were already sent as a side effect).

- `_coerce_pythonic_dict(value)` — Defensive fix for a production bug: GraphRAG sometimes returns a dict already stringified using Python's `str(dict)` (single-quoted, not valid JSON) instead of real JSON. Uses `ast.literal_eval` to safely parse a string that looks like `{...}` back into a real dict; returns the input unchanged (never raises) if it isn't a clean dict literal. Without this, such responses would previously be sent to the customer verbatim as raw Python syntax.

- `_parse_plaintext_categories(text) -> Optional[list]` — Detects when GraphRAG's clarification request arrived as a bulleted/numbered plain-text list instead of a structured dict. Requires at least one clarification keyword to be present, then regex-matches bullet/numbered lines, stripping trailing `— description` suffixes and markdown bold markers. Returns `None` unless ≥2 categories were found.

- `async _resolve_category_from_message(incoming, text, categories) -> Optional[str]` — Matches a customer's reply to a previously-offered category list via a 3-tier priority: (1) index-based (`"5"`, `"#5"`, `"option 5"`) via regex; (2) exact then substring case-insensitive string match, then a hardcoded word-overlap heuristic; (3) LLM semantic fallback via `category_matcher_prompt` if nothing else matched. Returns `None` if all three tiers fail.

- `async _build_enriched_graphrag_query(incoming, customer_message, memory_context, current_product=None) -> Optional[str]` — Builds a rewritten GraphRAG query via `graphrag_query_builder_prompt`, blending short-term context (`current_product`) with long-term context (`memory_context`) so a customer discussing one product who asks "recommend something for me" gets contextually relevant results. Returns `None` on failure (non-critical, caller uses the original query).

- `async call_graphrag_api(incoming, session_history=None, graphrag_url=None) -> str` — The main entrypoint, called for every product-related query.
  1. **Pre-check**: calls `_try_resolve_product_followup(incoming, session_history or [])` first. If it returns `"__ALREADY_HANDLED__"`, returns `""` (image/link already sent directly). If it returns any other truthy string, returns that directly — GraphRAG is never called. This is the primary integration point with `product_followup.py`.
  2. **Query prep**: uses `incoming.resolved_query` if set, else `incoming.text`, verbatim (no stripping/cleaning, since GraphRAG's Neo4j semantic search understands natural language). Strips a `[Quoting:...]\n` prefix if the message was a WhatsApp quote-reply.
  3. **Category selection resolution**: if a prior turn saved category options, tries to resolve the current message against them via `_resolve_category_from_message`; on match, clears the saved selection and rewrites the query to `"Show products in {category}"`; on no match, still clears the selection and proceeds with the original query.
  4. **Memory-aware query enrichment**: reads `incoming._cached_arc.llm_context`. If the message references a past order/purchase (checked via a keyword list), resolves the actual product via `CustomerDataService.get_latest_ordered_product()`. Otherwise, if routing indicates a details-query, resolves via `get_last_discussed_product` or `arc.resolved_product`. Otherwise, only re-attaches the previously active product if `incoming._is_new_category_search` was NOT set by `product_followup.py` (guards against re-injecting stale context into a genuinely new search) and `active_product_session` is true. If either enrichment source is non-empty, calls `_build_enriched_graphrag_query` and swaps in the enriched query text.
  5. **Payload build**: constructs a dict matching the `messages` table schema — this is sent as the actual GraphRAG request body, not just a DB write.
  6. **URL resolution**: `graphrag_url` param (per-tenant override from the `tenants` table) takes priority over the global `GRAPHRAG_API_URL` env var. If neither is set, returns a "can't look up products" reply immediately without any HTTP call.
  7. **HTTP call**: `httpx.AsyncClient` with a generous 90s read timeout (GraphRAG can take 40-60s). Handles `403` (host not whitelisted) and non-200 status with distinct tenant-brandable fallback prompts.
  8. **Response shape handling**: `response_text = data.get("response_text", [])`, passed through `_coerce_pythonic_dict`. Three branches: (a) dict with `status == "needs_clarification"` → renders a friendly numbered category list and persists it via `save_category_selection`; (b) non-empty list of dicts → delegates to `_send_structured_product_list`; (c) explicitly-checked empty list `[]` → returns a clean "no matches" reply (a documented bug fix — an empty list is falsy in Python, so a naive truthiness check previously fell through to stringifying the *entire raw API payload* and sending that to the customer).
  9. **Plain-text branch**: also runs `_parse_plaintext_categories` in case the clarification arrived as unstructured text. If the reply is short (`≤100 chars`) and contains `"error"`/`"sorry"`, retries once against `GRAPHRAG_API_URL` (note: the retry always uses the global URL, not the possibly-per-tenant one) with a simplified query. If the retry also fails, returns a friendly fallback rather than ever exposing GraphRAG's raw error text.
  10. **Long-reply splitting**: replies >4096 chars are split at line boundaries into ≤3800-char chunks joined with a `\n\n⟨MSG_SPLIT⟩\n\n` sentinel.
  11. Any uncaught exception anywhere in the function is caught at the top level and converted into a generic "product search temporarily unavailable" branded fallback.

### Example
Customer sends "show me outdoor gate lights" with no active product context. `_try_resolve_product_followup` returns `None` (no selection, no last-discussed product — a fresh browse). `call_graphrag_api` builds the payload with `text="show me outdoor gate lights"`, POSTs to the tenant's GraphRAG URL, receives a structured product list, and returns via `_send_structured_product_list` — which sends up to 3 image cards concurrently and returns a numbered text list ending in the tenant's configured footer.

### How it connects
- Imports from: `messaging` (`send_reply`, `send_image`), `ai.product_followup._try_resolve_product_followup` (critical pre-check), `db.session_store` (product/category/offer save+get functions), `config` (`GRAPHRAG_API_URL`, Azure creds), and internally `db.prompt_store.get_prompt`, `db.customer_data_service.CustomerDataService`, `db.session_store.get_last_discussed_product`.
- Called by: `pipeline/router.py`'s `_call_graphrag_timed` wrapper (imports `call_graphrag_api` and times the whole call), and `main.py` (imports `call_graphrag_api`, `_send_structured_product_list`, `_coerce_pythonic_dict` directly).

---

## ai/product_followup.py

### Why this file exists
This module resolves whether an incoming WhatsApp message is a *follow-up* about a product the customer already saw (from a prior GraphRAG product list, a negotiation, or "last discussed product" state) rather than a brand-new search. It exists to keep `main.py`/`graphrag_handler.py` thin: all the LLM-driven parsing, negotiation hand-off, comparison, offer-inquiry, image/installation delivery, and quantity-injection logic for "continuing a conversation about a product" lives here. Without it, every follow-up message ("is it aluminum?", "1 unit", "compare X and Y", "any offers?") would incorrectly re-trigger a full GraphRAG search instead of being answered from cached product context, and negotiation state would never be checked before routing.

### How it works

Module-level: builds its own `AzureOpenAI` client (`_client`, 30s timeout, 0 retries, wrapped by `request_profiler.wrap_llm_client`). Imports negotiation entrypoints (`is_negotiation_request`, `handle_negotiation`) from `ai.negotiator` and numerous session-state accessors from `db.session_store`. All prompts are fetched from DB via `get_prompt(incoming, key, **vars)` — no hardcoded prompt strings.

- `_format_faq_entry(f) -> dict` — Normalizes an FAQ entry that may arrive as either `{"question","answer"}` or a plain string into `{"q","a"}`. Used when building `product_context["faqs"]`.

- `async _parse_followup_message(incoming, selection, session_history=None) -> dict` — The core LLM classifier for a follow-up message. Builds a 1-based numbered product catalog from `selection` (so the LLM can resolve "features of 44" → item 44), sends it plus last 4 turns of history to the LLM via `pf_data_extraction_prompt`, and parses a JSON object with fields like `selected_product_name`, `quantity`, `is_comparison`, `is_offer_inquiry`, `asks_for_image`, `is_new_search`. Result is cached on `incoming._cached_quick_parsed` so it's computed only once per request even if called from multiple code paths. On any exception (bad JSON, empty response, LLM failure) returns an all-`None`/`False` default dict rather than raising — a defensive fallback so a parser hiccup routes to GraphRAG instead of crashing.

- `async _get_active_product_context(incoming, selection, session_history) -> list` — LLM-driven resolution of "which products were we just discussing" from the last 6 turns, used for pronoun/ambiguous follow-ups ("suggest me one", "which is better?"). Returns `[]` immediately if there's no history or no product names in `selection`. Sends `pf_history_resolver_prompt`, parses a JSON list of product name strings, then fuzzy-matches each name against `selection` via substring containment (case-insensitive). Swallows all exceptions and returns `[]` on failure.

- `async _handle_comparison(incoming, compared, session_history, show_recommendation=False) -> str` — Formats a side-by-side text block (price, features, specs, description) for each product in `compared` and sends it to the LLM via `pf_comparison_prompt` to generate the natural-language comparison/recommendation reply. If `compared` is empty, short-circuits with a generic "which product?" string without calling the LLM.

- `async _try_resolve_product_followup(incoming, session_history) -> Optional[str]` — The main entrypoint (~1500 lines of logic), called by `call_graphrag_api()` before ever hitting GraphRAG. High-level flow:
  1. **Load state**: reads cached/fresh negotiation state (`neg_state`) and the active `selection` (GraphRAG product list saved in `workflow_sessions`). If no selection exists, falls back to `get_last_discussed_product`, then to `neg_state.get("product_name")`; if none of these exist, returns `None` (truly nothing to follow up on — defers to GraphRAG).
  2. **Negotiation bypass check**: if `incoming._routing.requested_knowledge_field` is a specific non-"none" field (e.g. customer asked about installation), negotiation is bypassed for this turn and `neg_state` is cleared locally.
  3. **Parallel early classifiers**: runs `_offer_inquiry_precheck()`, `_parse_followup_message()`, and `_neg_request_check()` (wraps `is_negotiation_request`) concurrently via `asyncio.gather` — a documented latency optimization (was ~4-7s sequential, now ~2-2.7s).
  4. **Stale-negotiation guards**: if the parsed result says `is_comparison`/`is_recommendation`/`is_offer_inquiry`, negotiation is never entered. If `neg_state` exists and the message looks like a new-category search, negotiation state is cleared. If the negotiation's stored product no longer matches anything in the current `selection`, an extra LLM check decides whether the customer switched products — if so, state is cleared.
  5. **Negotiation branch** (`if neg_state or _is_neg_req`): resolves which product is being negotiated (priority: existing state → `get_last_discussed_product` → quoted-message caption match → first item in `selection`), fetches it from cache, and calls `handle_negotiation(...)` from `ai.negotiator`. Sub-cases: `defer_intent` (negotiator decided this isn't negotiation-related — routes to greeting/escalation/customer-history handlers directly, preserving negotiation state); `order_ready + agreed_price` (builds and returns an order summary); otherwise returns the negotiator's `reply` directly.
  6. **Standard follow-up parsing**: reuses `quick_parsed` from step 3. Applies a "selection match override" guard — if the raw message text exactly equals a product name in `selection`, forces `is_new_search=False`. Applies a numeric-index guard — a bare number in range of `selection` overrides `is_new_search` to select that product by position.
  7. **New-search branch**: if still classified `is_new_search`, sets `incoming._is_new_category_search = True` (consumed by `graphrag_handler.py` to prevent re-injecting stale product context), optionally rewrites vague queries, then returns `None`.
  8. **Offer-inquiry branch** (`is_offer_inquiry`): a second LLM layer-2 check catches cases layer 1 missed. Resolves offer text + reference price via a 4-tier priority cascade (last-discussed product → named product in message → first product in `selection` with cached `global_offers` → `tenant_offers` table). Persists `offer_disclosed=True` on negotiation state. Computes per-tier prices via `parse_global_offer_tiers` and formats the final reply.
  9. **Comparison/recommendation branch**: resolves the set of products being compared through four fallback levels — LLM-parsed list, direct substring scan, pronoun resolution, `_get_active_product_context`, or full `selection` as last resort. Sends product images if `asks_for_image` was set, then delegates to `_handle_comparison`.
  10. **Name-match branch (Case 2)**: tries LLM-parsed `selected_product_name` substring match first, then falls back to a word-overlap scorer picking the highest-scoring product.
  11. **Deterministic bare-number resolution**: if the message is a bare number and no product matched yet, inspects only the bot's single most recent message to disambiguate: freshly-shown product list → 1-based list position; explicit "how many units?" question → falls through as quantity; otherwise (ambiguous) explicitly asks the customer to reply with a product name.
  12. **Case 3 DB fallback**: `get_last_discussed_product`, matched against `selection`.
  13. **Case 4 heuristic fallback**: scans the last 6 turns of bot messages for any product's first word (>3 chars) appearing in the combined text.
  14. If still nothing matched, returns `None` (defers to GraphRAG).
  15. Once `matched_product` is resolved: appends `selected_options` (wattage/size/color) to the product name if present, persists it via `save_last_discussed_product`, and fetches the full cached record.
  16. **Image/installation delivery** (`asks_for_image`): an LLM call distinguishes "send me the product photo" from "send me installation instructions." For installation requests, returns the sentinel `"__ALREADY_HANDLED__"` so `graphrag_handler.py` knows not to run any further LLM reply. For plain product-image requests, sends the image and continues to the final answer step.
  17. **Builds `product_context`**: a full copy of the cached product dict overlaid with formatted display fields. If a quantity was parsed, also runs an **auto-apply global offer tier** calculation — gated by the tenant-configurable `require_offer_disclosure` flag (skips auto-applying a discount the customer hasn't asked about yet unless `offer_disclosed` is already `True`).
  18. **Final LLM answer**: sends `pf_main_followup_prompt` (product context as JSON, last 6 history turns, the parsed intent JSON) to generate the natural-language reply.
  19. **Pending-order persistence**: if a quantity was parsed AND there's no already-active negotiation quantity, saves a pending order row and rebuilds a fresh negotiation state with `awaiting_invoice_confirmation=True`.
  20. Returns the final LLM reply string, or `None` on any exception in the final LLM call (falls through to GraphRAG).

Non-obvious gotchas documented inline: the numeric-index guard exists because customers reply with bare numbers that could mean list-position, quantity, or nothing depending on the bot's last message; a disabled (`and False`) LLM guard block is dead code kept for reference, superseded by reusing `parsed["is_new_search"]`; the `_fresh_neg`/`_has_active_qty` guard exists specifically to prevent `_parse_followup_message`'s raw quantity number from clobbering `handle_negotiation`'s own quantity-delta tracking mid-negotiation.

### Example
Customer previously received a GraphRAG list including "Romy 12W Wall Light" (Rs.1200) and "Reva 8W" (Rs.900), then sends "is Romy waterproof?". `_try_resolve_product_followup` loads `selection`, runs the three parallel classifiers (none detect negotiation/offer-inquiry), name-matches "Romy" via `parsed["selected_product_name"]`, fetches the cached Romy record, skips the image branch, builds `product_context`, and returns an LLM-generated answer — never touching GraphRAG at all.

### How it connects
- Imports from: `config`, `db.session_store` (negotiation state, product selection/cache, last-discussed-product, pending orders, tenant offers, outbound message logging), `db.prompt_store.get_prompt`, `ai.negotiator` (`is_negotiation_request`, `handle_negotiation`, and dynamically `parse_global_offer_tiers`/`get_applicable_tier`/`get_next_tier`), `ai.request_profiler`, `messaging` (`send_reply`, `send_image`).
- Called by: `ai/graphrag_handler.py`'s `call_graphrag_api()` (as a pre-check before hitting GraphRAG) and `main.py` (imports `_try_resolve_product_followup`, `_parse_followup_message` directly). Also `ai/product_context_resolver.py` imports `_parse_followup_message` internally.
- Dynamically imports `ai.handlers.handle_greeting`, `ai.handlers.handle_escalation`, and `ai.customer_history_handler.handle_customer_history_query` for the negotiator-deferral routing case.

---

## ai/negotiator.py

### Why this file exists
Implements the entire price-negotiation state machine — quantity capture, tier-based store discounts, multi-round haggling with escalating concessions, acceptance/counter-offer/quantity-change intent classification, and final order creation — as an isolated module so `product_followup.py` and `pipeline/router.py` can drive negotiation without owning any of its internal math or LLM prompt wiring. The file's own header states the hard rule: no prompt string may live in this file, every prompt is fetched via `get_prompt(incoming, key, **vars)` from the DB, and missing prompts raise `RuntimeError` rather than silently falling back to hardcoded English.

### How it works

Module-level: own `AzureOpenAI` client (`_client`, wrapped by `request_profiler`). Constants `MAX_NEGOTIATION_ROUNDS = 5` and `DEFAULT_FLOOR_DISC_PCT = 5` (used only when no tier data exists at all).

**Tier helpers (pure math / DB cache, no LLM prompts except tier parsing):**

- `parse_global_offer_tiers(incoming, global_offers) -> list` — Parses a tenant's free-text offer description (e.g. "10% off orders above Rs.10,000") into a sorted list of `[min_order_value, discount_pct]` pairs. First checks a Postgres cache (`tenant_offers.tiers_json`); if missing/invalid, calls the LLM, parses the JSON array response, and **upserts** the parsed result back to `tenant_offers` for future calls. Returns `[]` on any failure — never raises.
- `get_negotiation_floor_disc(tiers) -> int` — Legacy fixed-position lookup, explicitly noted as the OLD buggy approach — superseded by `max(d for _, d in tiers)`, but still called as a fallback when `max_disc == 0`.
- `get_applicable_tier(order_value, tiers) -> tuple` — Returns the highest `(min_val, disc_pct)` tier whose threshold the current order value has crossed.
- `get_next_tier(order_value, tiers) -> Optional[tuple]` — Returns the next higher tier not yet reached, or `None` if already at max tier or no tiers exist. Used to build "spend Rs.X more to unlock Y% off" upsell lines throughout the codebase.
- `calculate_offer(price_num, quantity, tiers=None) -> dict` — The core pricing calculation. Computes `order_value_for_tier = price_num * quantity` to determine the store-tier discount the customer's *current* order actually qualifies for (`current_tier_disc`), separately computes `max_disc` (highest tier discount anywhere) as the negotiation ceiling, derives `tier_price` and `floor_price` from those two discounts respectively, and sets the initial `offer_price` as one-third of the way from `tier_price` toward `floor_price`. A documented fix note explains `order_value` (used for upsell-gap math) must be based on `offer_price * quantity` — the subtotal the customer actually sees — not `price_num * quantity`. Returns a dict with 11 fields including `has_discount`.

**Detection helpers — all single-purpose LLM classifiers, all DB-prompt-driven, all defensively catch non-`RuntimeError` exceptions and return a safe default:**

- `async is_negotiation_request(message, incoming, session_history=None) -> bool` — Uses `neg_is_request_prompt` + last 4 history turns.
- `async extract_quantity(message, product_name, incoming, session_history=None) -> Optional[int]` — Uses `neg_extract_qty_prompt`; parses `"NONE"` as `None`.
- `async detect_quantity_change(message, current_qty, product_name, incoming) -> Optional[int]` — Uses `neg_detect_qty_change_prompt`; a legacy standalone detector, superseded inside `handle_negotiation` by `extract_negotiation_intent`, but still called directly by `pipeline/router.py`'s `_handle_qty_confirm_split`.
- `async detect_counter_offer(message, incoming, current_price_num=None, quantity=None, session_history=None) -> Optional[float]` — Uses `neg_detect_counter_prompt`; parses `UNIT:<n>` or `TOTAL:<n>` prefixed responses, converting TOTAL to per-unit.
- `async detect_more_discount_request(message, incoming, session_history=None) -> bool` — Uses `neg_more_discount_prompt`.
- `async detect_acceptance(message, incoming, session_history=None) -> bool` — First checks `utils.conversation_actions.is_quick_confirm` as a fast-path before falling back to `neg_detect_accept_prompt`. Called directly by `pipeline/router.py` for compound-intent detection ("add 1 unit AND confirm").

**Consolidated negotiation-intent extraction** (the modern replacement for the four detectors above being called sequentially):

- `dataclass NegotiationIntent` — Structured result: `intent` (`ACCEPTED|QTY_CHANGE|COUNTER_OFFER|MORE_DISCOUNT|NONE`), `matched_phrase`, `accepted`, `quantity_value`/`quantity_operation`, `counter_offer_value`/`counter_offer_price_type`, `more_discount`, `confidence`.
- `_apply_quantity_operation(current_qty, operation, value) -> Optional[int]` — Pure Python arithmetic: `SET` returns `value`, `ADD` returns `current_qty + value`, `REMOVE` returns `max(0, current_qty - value)`. Never done by the LLM — the LLM only classifies which operation was requested and the raw number.
- `_resolve_counter_offer_price(value, price_type, quantity) -> Optional[float]` — Converts a `TOTAL` price ask to per-unit by dividing by quantity.
- `async extract_negotiation_intent(message, product_name, current_price, current_qty, incoming, session_history=None) -> NegotiationIntent` — One LLM call replacing the 4 sequential detectors (~6-8s → ~2-3s), so a compound message like "I'll take 5 units if you can do 1500" is interpreted holistically. On any parse failure returns `intent="NONE"` with everything empty.

**Reply generators — DB-prompt-driven, each builds one class of negotiation reply:**

- `async _reply_no_discount(...)` — Used when the order doesn't qualify for any tier discount. Delegates the actual summary formatting to `ai.pricing.PricingResult.build(...).to_whatsapp_summary(...)`, then appends an upsell hint if a next tier exists.
- `async build_product_summary(incoming, product_data) -> str` — Generates an optional short product blurb (rating/reviews/warranty/features), used after quantity changes so the reply isn't just a bare price update. Returns `""` silently if nothing useful to show.
- `async _reply_first_offer(...)` — Builds the initial negotiated-offer reply, then appends either a next-tier upsell hint or a "maximum discount unlocked" celebration.
- `async _reply_counter_offer(...)` — Generates a counter-offer message, injecting whether this is the final round and any "customer moved their budget from X to Y" acknowledgment note.
- `async _reply_final_price(...)` — Generates the firm final-price message.

**Main handler:**

- `async handle_negotiation(incoming, product_name, price_num, regular_price, graphrag_discount_pct, session_history, negotiation_state, global_offers=None, product_data=None) -> dict` — The negotiation state machine, dispatched by round/state:
  - Computes/reuses `tiers` (cached in state to avoid re-parsing every turn), derives `floor_disc`/`floor_price`, and re-derives `floor_price` from a previously-saved `auto_offer_unit_price` if it's lower than `price_num` (so negotiation floors from the already-discounted starting point, not list price).
  - **Step 1 (awaiting_quantity=True)**: extracts quantity; if still missing, re-asks. Once obtained, computes `calculate_offer`; returns either a clean store-tier summary or the first negotiated offer.
  - **Step 2 (no quantity in state yet)**: same extraction logic, with a guard double-checking `rounds > 0 or is_negotiation_request(...)` before presenting a "Negotiated price" reply — a plain "I want 2 units" order gets a clean store-tier summary instead.
  - **Step 3 (ongoing negotiation)**: resolves `last_offer` with priority saved `last_offer_price` → `auto_offer_unit_price` (if below list price) → `price_num` — a documented bug fix (using `price_num` as the base collapsed the whole negotiation window into a single round). Calls `extract_negotiation_intent` once.
    - **ACCEPTED**: builds a `PricingResult`, calls `ai.order_service.complete_order` to create a `PENDING_PAYMENT` order, builds a fake `payment_link`, returns a payment/address-request message with `order_ready=True`.
    - **QTY_CHANGE**: applies `_apply_quantity_operation`, recalculates `calculate_offer`, applies the same `require_offer_disclosure` gate seen in `product_followup.py`, then runs 5 independent async operations concurrently via `asyncio.gather`.
    - **COUNTER_OFFER / MORE_DISCOUNT**: if neither matched, treats this as a possible off-topic interruption — calls `ai.handlers.classify_intent` and, if the classified intent looks off-topic, returns `{"reply": None, "defer_intent": <result>, ...}` with negotiation state preserved, letting the caller dispatch the real intent while negotiation can resume later. Otherwise returns a stalemate reply repeating the current offer.
    - Otherwise (a genuine counter or discount request): increments `rounds`, computes `is_final = rounds >= MAX_NEGOTIATION_ROUNDS`, and runs a stateful bargaining engine with dynamic escalating concession percentages per round (`STEP_PCTS = [0.15, 0.20, 0.25, 0.25, 0.15]`), a capped reactive move toward the customer's ask (`MAX_REACTIVE_MOVE = 50`), an early-exit if the customer's counter is within `CLOSE_THRESHOLD = 25` rupees of the current offer, and a `customer_offers` trajectory list persisted in state.

Gotcha: `handle_negotiation` here has no `negotiation_prompt` parameter — despite `CLAUDE.md`'s changelog describing one being added, the actual current signature only accepts `global_offers` and `product_data` as optional kwargs; all prompt selection still goes through `get_prompt`'s own per-tenant DB lookup keyed by prompt name.

### Example
`handle_negotiation(incoming, product_name="Romy 12W", price_num=1200, regular_price=1200, graphrag_discount_pct=0, session_history=[...], negotiation_state={"rounds": 1, "quantity": 10, "last_offer_price": 1100, ...}, global_offers="10% off above Rs 10000")` where the customer just said "can you do 1000?": `extract_negotiation_intent` classifies `COUNTER_OFFER`; the gap to `last_offer=1100` (100) exceeds `CLOSE_THRESHOLD` (25), so it proceeds into the dynamic-step branch, computes a new offer between `last_offer` and `floor_price`, and returns `{"reply": "<counter-offer text>", "state": {...rounds:2...}, "order_ready": False, "agreed_price": <new>, "quantity": 10}`.

### How it connects
- Imports from: `config`, `db.prompt_store.get_prompt`/`aget_prompt`, `ai.request_profiler`, and — internally, lazily — `db.session_store._get_client` (tier caching), `ai.pricing.PricingResult`, `ai.order_service.complete_order`, `ai.handlers.classify_intent`, `utils.conversation_actions.is_quick_confirm`.
- Called by: `ai/product_followup.py` (`is_negotiation_request`, `handle_negotiation`, and dynamically `parse_global_offer_tiers`/`get_next_tier`/`get_applicable_tier`), `pipeline/router.py` (`handle_negotiation` in both `_handle_qty_confirm_split` and `_resume_negotiation`, plus `detect_quantity_change` and `detect_acceptance` directly), `ai/pricing.py` (imports `get_applicable_tier`, `get_next_tier`).

---

## ai/pricing.py

### Why this file exists
Centralizes every monetary calculation (store discount, negotiation discount, subtotal, GST, total) into one dataclass, `PricingResult`, because previously the same math was duplicated independently in `product_followup.py`, `router.py`, `negotiator.py`, and `invoice_handler.py` with subtly different rounding — causing mismatches like an invoice showing Rs.2650.30 while the order summary showed Rs.2650. Without this file, every caller would recompute totals itself and risk drifting out of sync with what the invoice/DB actually records.

### How it works
`@dataclass PricingResult` holds raw inputs (`regular_unit_price`, `quantity`, `gst_rate`, `store_disc_pct`, `store_unit_price`, `negotiated_unit_price`, `negotiation_rounds`) plus derived fields defaulted to 0/empty and computed only inside `build()`.

- `PricingResult.build(cls, regular_unit_price, quantity, gst_rate=0.18, store_disc_pct=0, negotiated_unit_price=None, negotiation_rounds=0, tiers=None) -> PricingResult` — the only sanctioned constructor. If `tiers` is passed, calls `ai.negotiator.get_applicable_tier`/`get_next_tier` (deferred import to avoid circularity) to compute the current volume-discount tier and the next tier's threshold/discount and `remaining_amount` needed to reach it. Computes `store_unit_price = regular × (1 - store_disc_pct/100)`, `negotiated_unit_price` (defaults to `store_unit_price` if not given), then all discount amounts, `subtotal`, `gst_amount`, `total_payable`, each rounded to 2 decimals. Returns a fully populated instance — this is the single place all rounding happens.
- `was_negotiated` (property) -> bool — `True` only if `negotiation_rounds > 0` AND `negotiation_discount_amount > 0`.
- `gst_pct` (property) -> int — `gst_rate` as a whole-number percentage (e.g. 0.18 → 18).
- `async to_whatsapp_summary(self, product_name, sender_name, incoming=None) -> str` — renders the pre-confirm order summary message. Picks one of three DB prompt variants based on state (negotiated / store-discount-only / plain price) via an inner `_prompt(key, fallback, **kwargs)` helper that falls back to hardcoded English text on `RuntimeError` or if `incoming is None`. Also conditionally builds a savings line if `total_discount_amount > 0`. Body/savings/footer prompt fetches run concurrently via `asyncio.gather`.
- `to_invoice_fields(self) -> dict` — maps pricing fields onto DB/invoice column names: `original_amount`, `store_discount_pct`, `store_discount_amount` (None if 0), `negotiation_discount_amount` (None if 0). Used as `extra_fields` passed into `create_order`/invoice generation.
- `to_negotiation_prompt_vars(self) -> dict` — returns a dict of pre-formatted strings/ints for negotiation prompt templates to interpolate — returns structured named variables rather than a hardcoded "Rs.X → Rs.Y" string, so the prompt template controls phrasing.
- `from_neg_state(cls, neg_state: dict, gst_rate: float = 0.18) -> PricingResult` — reconstructs a `PricingResult` from a negotiation-state dict, pulling `price_num`, `quantity`, `auto_offer_disc_pct`, `last_offer_price` (or `auto_offer_unit_price`), `rounds`, and `_tiers`. Used wherever code only has the persisted negotiation dict and needs pricing derived from it (post-confirmation flows).

### Example
`PricingResult.build(regular_unit_price=1500, quantity=10, gst_rate=0.18, store_disc_pct=10, negotiated_unit_price=1300, negotiation_rounds=2)` produces `store_unit_price=1350.0`, `negotiated_unit_price=1300.0`, `store_discount_amount=1500.0`, `negotiation_discount_amount=500.0`, `total_discount_amount=2000.0`, `subtotal=13000.0`, `gst_amount=2340.0`, `total_payable=15340.0`.

### How it connects
- Imports (deferred, to dodge circular imports): `ai.negotiator.get_applicable_tier`/`get_next_tier` inside `build()`; `db.prompt_store.aget_prompt` inside `to_whatsapp_summary()`.
- Called by: `pipeline/router.py` (`PricingResult.from_neg_state(...)`, `.to_invoice_fields()` — 3 call sites), `ai/negotiator.py` (`PricingResult.build(...)`, `.to_whatsapp_summary(...)`, `.to_invoice_fields()` — 3 call sites).

---

## ai/invoice_handler.py

### Why this file exists
Owns everything related to detecting an invoice request, confirming it, and generating/returning the actual PDF link — logic that used to live in `main.py`. It exists as a separate module so `main.py` stays a thin dispatcher and so invoice-specific LLM prompt calls (inquiry detection, confirmation detection, multi-invoice count extraction) are grouped with the one function that actually produces invoices. Without it, `pipeline/router.py` would have no way to turn "send me the invoice" / "last 3 invoices" / "confirm" into a PDF URL or a formatted multi-order summary.

### How it works
Module-level: builds its own `AzureOpenAI` client (`_client`, 30s timeout, 0 retries), wrapped with `ai/request_profiler.wrap_llm_client`. `_get_invoice_prompt(key, incoming, **vars)` is a thin synchronous wrapper around `db.prompt_store.get_prompt`.

- `_get_invoice_prompt(key, incoming=None, **vars) -> str` — loads one prompt template by key from the tenant's DB config, substituting `**vars`. Raises `RuntimeError` if missing.
- `async _is_invoice_inquiry(incoming) -> bool` — asks the LLM (`invoice_inquiry_check_prompt`, max_tokens=5, temp=0) whether the customer's message is asking for an invoice/bill/receipt. Returns `True` only if response contains "YES". Any exception returns `False`.
- `async _generate_confirmation_prompt(reply_text, incoming) -> str` — generates a short "reply Confirm to get your invoice" CTA line via LLM (`generate_invoice_cta_prompt`). Falls back to `generate_invoice_cta_fallback` DB prompt, then a hardcoded English string as the last resort.
- `async _is_invoice_confirmation_request(incoming, session_history) -> bool` — determines if the customer's current message is confirming the *previous* bot message that asked them to confirm for an invoice. Looks only at the last assistant message from `session_history[-4:]`; returns `False` immediately if there's no history or no prior assistant message.
- `async _extract_invoice_limit(incoming) -> int` — LLM call (JSON mode) that parses how many invoices the customer wants ("last invoice" → 1, "last 3 invoices" → 3, "all my invoices" → a large cap like 20). Defaults to 1 on any exception.
- `async handle_invoice_request(incoming, negotiated_order=None) -> str` — the main entry point. Two paths:
  - **negotiated_order passed**: uses it directly (correct just-negotiated qty/price) and deletes any stale `pending_order` row so it doesn't override the correct order later.
  - **no negotiated_order**: first commits any `pending_order` still sitting in DB (fetches cached product price, calls `ai.order_service.complete_order`, deletes the pending row, fires a background task to clear post-order context). Then calls `_extract_invoice_limit`; if >1, fetches multiple orders and returns a formatted multi-invoice list. Otherwise falls through to the single latest order.
  After resolving `order`, if none found returns a "no orders found" reply. If found but `invoice_url` is missing, calls `utils.invoice.generate_and_upload_invoice` and persists the URL. Finally returns a success reply with the URL, or a "PDF failed" reply.

### Example
`await handle_invoice_request(incoming)` where the customer's session has a completed order but no `invoice_url` yet: generates the PDF, saves the URL back to the `orders` row, and returns a WhatsApp-formatted string with a download link.

### How it connects
- Imports: `config`, `db.session_store` (negotiation state, order lookups, invoice URL update, pending order CRUD), `adapter.whatsapp_adapter.send_whatsapp_reply`, `ai.order_service.OrderResult`/`complete_order`, `db.prompt_store.get_prompt`, `utils.invoice.generate_and_upload_invoice`, `ai.request_profiler.wrap_llm_client`.
- Called by: `pipeline/router.py` — `handle_invoice_request(incoming, negotiated_order=updated_order)` and three plain calls across different flows (post-negotiation confirm, standalone invoice request, order confirm-without-negotiation).

---

## ai/order_service.py

### Why this file exists
Both `pipeline/router.py` (`_confirm_negotiated_order`) and `ai/invoice_handler.py` (the "commit pending order" fallback) need to do the exact same two things whenever an order is actually created: write the order row, and save `product_context` to Postgres so "what did I order before?" queries have real data regardless of which conversational path got the customer there. The module header explicitly notes this is deliberately NOT built as an event bus — with only two call sites, that would solve a problem that doesn't exist yet.

### How it works
- `class OrderResult` — wraps `create_order()`'s raw dict return value rather than replacing it with a strict dataclass, because that dict is a full DB row (schema varies by tenant) plus arbitrary `extra_fields` merged in by each caller.
  - `__init__(self, data, saved_to_memory)` — stores `self._data` and `self.saved_to_memory`.
  - `order_id` (property) -> `Optional[str]`.
  - `get(self, key, default=None)` — delegates to `self._data.get`.
  - `__getitem__`, `__contains__`, `__bool__`, `__repr__` — dict-like passthroughs.
- `async complete_order(tenant_id, session_id, sender_name, items, gst_rate=0.18, extra_fields=None, shipping_address=None, status="CONFIRMED") -> Optional[OrderResult]` — calls `db.product_store.create_order(...)`. Returns `None` immediately if that fails. If items were passed, fires an inner `_save_order_context_async()` as a background `asyncio.create_task`: saves the first item's product name as a product view, then — if a discount/negotiation happened — saves offer history with `offer_tier` set to `"negotiated"` or `"store_offer"`. Sets `saved_to_memory = True` as soon as the background task is scheduled (not when it actually completes). Returns `OrderResult(new_order, saved_to_memory)`.

### Example
`await complete_order(tenant_id="tenant_inventaa_led_001", session_id="91XXXXXXXXXX", sender_name="Ravi", items=[{"product_name": "Mini Elena 10W", "quantity_value": 5, "quantity_unit": "units", "unit_price": 1350.0, "total_price": 6750.0}], gst_rate=0.18)` creates the order row, kicks off a background save of the product view + offer history, and returns an `OrderResult`.

### How it connects
- Imports: `db.product_store.create_order` (deferred), `db.customer_data_service.CustomerDataService` (deferred).
- Called by: `pipeline/router.py` (`_confirm_negotiated_order` flow — 2 call sites), `ai/invoice_handler.py` (pending-order-commit fallback), `ai/negotiator.py` (order confirmation flow).

---

## ai/customer_history_handler.py

### Why this file exists
Handles a specific class of customer question — "what did I order before", "what discount did I get last time" — that needs to query structured Postgres order/offer history rather than going through GraphRAG's product-catalog search. It exists separately because this is a classification + structured-DB-query + templated-reply flow, distinct from GraphRAG's semantic product search, and needs its own LLM classification step to decide if a message is even asking about history at all.

### How it works
Module-level: `_client` (AzureOpenAI, 15s timeout) wrapped by `wrap_llm_client`.

- `async handle_customer_history_query(incoming, session_history) -> Optional[str]` — single function, three stages:
  1. **Classify**: calls the LLM (JSON mode, `memory_query_classifier_prompt`, max_tokens=60, temp=0). Parses `memory_type` (`"order"`, `"offer"`, or `"other"`), `confidence`, and `limit` (clamped to max 20). Any exception returns `None` immediately.
  2. **Gate**: if `confidence < 0.6` or `memory_type == "other"`, returns `None` — caller falls back to GraphRAG.
  3. **Query + format**: `"order"` calls `get_order_history(limit=limit)` and builds a bullet list rendered via `memory_order_formatter_prompt`. `"offer"` calls `get_offer_history(limit=limit)` plus the tenant's currently-active store offers, rendered via `memory_offers_formatter_prompt`. Exceptions in either branch return `None`.

### Example
Customer sends "what did I order last time?" → classifier returns `{"memory_type": "order", "confidence": 0.88, "limit": 1}` → returns a rendered string like "Here's your recent order, Ravi: Order ORD-42: Mini Elena 10W x5 @ Rs.6,750".

### How it connects
- Imports: `config`, `db.prompt_store.get_prompt`, `db.customer_data_service.CustomerDataService` (deferred), `db.session_store.get_tenant_offers` (deferred), `ai.request_profiler.wrap_llm_client`.
- Called by: `pipeline/router.py` and `ai/product_followup.py` (both use the `None` return as a signal to fall through to GraphRAG).

---

## ai/knowledge_accessor.py

### Why this file exists
Provides a generic, tenant-configurable way to pull a specific "knowledge field" (installation guide, warranty text, product images, FAQ, etc.) out of the structured `knowledge_context` dict that `ContextBuilder` assembles, without hardcoding field-name-to-path mappings per tenant in Python. It exists so new knowledge fields/domains can be added purely via a `tenant_configurations` DB row (`knowledge_field_mappings`) rather than a code change — matching the project's DB-driven convention.

### How it works
`class KnowledgeAccessor` — both methods are `@staticmethod`.

- `async get(incoming, knowledge_context, domain, field_name) -> Optional[dict]` — returns `None` immediately if any input is falsy. Loads `mappings` via `get_tenant_config(tenant_id, "knowledge_field_mappings")`; if unset, uses a hardcoded default mapping scoped to the `"product"` domain covering `installation`, `manual`, `warranty`, `images`, `specifications`, `faq`, `videos`, `certifications`. If no explicit config exists for the field, falls back to a generic guess (candidate dotted paths + type inferred from substring heuristics on the field name). Walks `knowledge_context[domain_lower]` and tries each candidate path via `_traverse`, returning the first non-empty value found as `{"type": field_type, "value": val}`.
- `_traverse(data, path) -> Optional[Any]` — walks a dotted path through nested dicts one segment at a time; returns `None` as soon as any segment is missing or not a dict.

### Example
`await KnowledgeAccessor.get(incoming, arc.llm_context.knowledge_context, "product", "warranty")` with `knowledge_context = {"product": {"metadata": {"warranty": "2 years"}}}` returns `{"type": "text", "value": "2 years"}`.

### How it connects
- Imports: `db.session_store.get_tenant_config`.
- Called by: `pipeline/router.py`'s "Dynamic Knowledge Resolution Intercept Guard", gated on `RoutingDecision.needs_product_context` and a non-"NONE" `requested_knowledge_field`.

---

## ai/request_profiler.py

### Why this file exists
Answers "for THIS one request, how much time went to DB calls vs. LLM calls vs. GraphRAG vs. dispatch?" — a per-request category breakdown, distinct from `ai/perf_metrics.py` which aggregates per-prompt-name latency across many requests over time. It needs `contextvars`-based isolation (so concurrent asyncio requests don't clobber each other's running totals) and monkeypatches `asyncio.BaseEventLoop.run_in_executor` process-wide exactly once.

### How it works
Module-level: `_profile: ContextVar[Optional[dict]]` (default `None`) holds the current request's running totals, isolated per asyncio task via contextvars. `_run_in_executor_patched` guards idempotent patching.

- `_patch_run_in_executor()` — patches `run_in_executor` so it copies the calling context into the worker thread before running. This matters because every LLM call site in this codebase uses `run_in_executor(None, lambda: ...)` (never `to_thread`), and a bare `ContextVar.get()` inside a `run_in_executor` callable was verified to see the *default* value, not the caller's — without this patch, `llm_ms` timing would silently attribute to nothing. Called once automatically at import time.
- `start()` — resets `_profile` to zeroed counters. Called once at the top of a request.
- `add(category, ms)` — increments `_profile["{category}_ms"]` and `_profile["{category}_calls"]`. Silent no-op if profiling hasn't started.
- `snapshot() -> dict` — returns the current context's accumulated totals, rounded to 1 decimal.
- `wrap_llm_client(client)` — monkeypatches `client.chat.completions.create` in place, timing elapsed wall-clock time into the `"llm"` category.

### Example
`request_profiler.start()` at the top of `main.py::run_pipeline()`; every DB call reports into `"db"`; every AzureOpenAI client gets `wrap_llm_client(_client)` called on it at import time; `pipeline/router.py`'s GraphRAG calls are timed into `"graphrag"`. At the end, `request_profiler.snapshot()` gives e.g. `{"db_ms": 340.2, "llm_ms": 2103.5, "graphrag_ms": 890.1, "dispatch_ms": 15.0, ...}`.

### How it connects
- Imports: stdlib only (`asyncio`, `contextvars`, `time`).
- `wrap_llm_client` is invoked at import time in `ai/invoice_handler.py`, `ai/customer_history_handler.py`, `ai/negotiator.py`, `ai/handlers.py`, `ai/customer_profile_updater.py`. `add("db", ...)` is called from `db/db_utils.py`. `start()`/`snapshot()` are called from `main.py`. `pipeline/router.py` times its GraphRAG calls into `"graphrag"` via this module.

---

## ai/__init__.py

Empty package marker (single comment line `# ai package`) — makes `ai/` importable as a Python package with no re-exports; every submodule must be imported by its full dotted path (e.g. `from ai.pricing import PricingResult`).

---

## Dead / Unreachable AI modules (documented for reference)

The following files are fully implemented but have **zero live callers** in the running pipeline
(confirmed by repo-wide grep, not assumption — see the Dead Code appendix at the top of this document).

### ai/orchestrator.py — `AIOrchestrator` (dead)
Designed as the single entry point meant to replace ~30 lines of manual orchestration in `main.py` with a 3-line `create()`/`dispatch()`/`finalize()` pattern. `docs/LATENCY_ANALYSIS.md` itself confirms `AIOrchestrator` is "not called from `main.py:run_pipeline()`" — `main.py` builds `AIRequestContext` directly and inline, bypassing `AIOrchestrator.create()` entirely.
- `AIOrchestrator.create(cls, incoming, session_history) -> AIRequestContext` — runs `_load_neg_state` and `_classify_intent` concurrently, constructs `AIRequestContext`, tries to enrich it via `ContextBuilder(arc).build()`.
- `AIOrchestrator._load_neg_state(cls, incoming)` — fetches negotiation state; returns `None` on exception.
- `AIOrchestrator._classify_intent(cls, incoming, session_history)` — mirrors the negotiation-bypass optimization that also exists (separately) in `main.py`: if `rounds > 0` and not `awaiting_invoice_confirmation`, bypasses the LLM with a synthetic `_Bypass` object.
- `AIOrchestrator.finalize(cls, arc, response)` — runs `_save_conversation` and `_record_metrics` concurrently.
- `AIOrchestrator._save_conversation` — currently just prints a log line; a stub/no-op despite the name.
- `AIOrchestrator._record_metrics` — inserts a row into the `ai_metrics` Supabase table, wrapped in a bare `except: pass`.

### ai/prompt_builder.py — `PromptBuilder` (dead)
Designed as a cleaner abstraction over `db.prompt_store.get_prompt` — `PromptBuilder(arc).render("key", **extra)` would auto-inject `sender_name`/`biz_name`/context variables from the shared `AIRequestContext`. No other module uses it; the live pipeline calls `get_prompt`/`aget_prompt` directly everywhere.
- `PromptBuilder.__init__(self, arc)` — stores `self.arc`.
- `async render(self, prompt_name, version=None, **extra_vars) -> str` — merges four layers of variables (system vars → `llm_context.to_dict()` → request basics → caller kwargs), loads the raw prompt via `_load`, substitutes via `str.replace` (not `format_map`), validates via `_validate`.
- `_load(self, prompt_name, version=None) -> str` — three-tier resolution: in-memory cache → `prompt_templates` table → a flat column on `incoming` (legacy fallback via `PROMPT_KEYS`).
- `_validate(self, prompt_name, rendered)` — regex-scans for leftover `{placeholder}` text and prints a warning.

### ai/graphrag_request_builder.py — `GraphRAGRequestBuilder` (dead)
Meant to be the single place that decides whether/how to enrich a raw customer query before sending it to GraphRAG. A repo-wide grep for actual instantiation found zero results — the live GraphRAG call path goes directly through `ai.graphrag_handler.call_graphrag_api`, which builds its own payload internally.
- `__init__(self, incoming, session_history=None)` — stores tenant/session context.
- `async build_payload(self) -> Dict[str, Any]` — applies a chain of numbered "Rule N" query-transformation rules (category-index selection, product comparison, numeric product selection, personalized recommendation, pronoun resolution, attribute follow-up keywords, catalog-discovery keywords) before assembling a flat payload dict matching GraphRAG's schema.

### ai/customer_profile_updater.py (dead — but some internal logic is real)
Intended to generate LLM summaries of conversations and persist purchase/negotiation/preference history. A repo-wide grep for every function name in this file turns up zero external callers.
- `async generate_conversation_summary(...)` — builds a summary via LLM but only **prints** it; never saves anywhere (a documented no-op).
- `async save_purchase_summary(...)` — also a no-op; comment confirms "Postgres order history handles this."
- `async save_last_product(...)` — **not** a no-op: actually calls `CustomerDataService.save_product_view`.
- `async save_customer_preference(...)` — actually persists via `CustomerDataService.save_preference`.
- `async update_negotiation_profile(...)` — actually persists via `CustomerDataService.save_negotiation_outcome`.
- `async get_semantic_context(...)` — builds a throwaway duck-typed context object and calls `ContextBuilder(arc).build().to_dict()`.

### ai/perf_metrics.py (defined, functional, but not wired in)
Aggregates per-prompt-name latency across many requests — distinct from the actively-used `ai/request_profiler.py`, which does per-request breakdown. Exists specifically because `AIOrchestrator`/`ai_metrics` (the "proper" path) isn't wired into the live pipeline, but its own `record()`/`timed()` functions were not found called from any live path either.
- `record(prompt_name, latency_ms, tenant_id=None, workflow=None)` — appends an event dict, trims to `_MAX_EVENTS = 5000`.
- `async with timed(prompt_name, ...)` — async context manager timing a block and calling `record()`.
- `_aggregate_by(dimension)`, `get_snapshot()`, `get_tenant_snapshot()`, `get_workflow_snapshot()`, `get_full_snapshot()`, `reset()` — aggregation/reporting views over the in-memory `_events` list.

---

# Part 5 — Database Layer

## db/session_store.py

### Why this file exists
This is the largest and most central data-access module in the bot — it owns the Supabase client for the `messages`, `tenants`, `workflow_sessions`, `orders`, `product_cache`, and `tenant_offers` tables. It exists to keep every raw SQL/PostgREST query out of the AI and pipeline layers so those layers can stay focused on conversation logic while this module owns connection handling, caching, and table-shape details. Without it, every handler would need to know Supabase table/column names directly, and there would be no single place enforcing the "Save-First" audit rule, the 20/30-minute workflow-session expiry conventions, or tenant-scoping on every query.

### How it works

**Module-level state:**
- `_supabase: Optional[Client]` — lazy singleton Supabase client.
- `_tenant_cache = TTLCache(ttl_seconds=300)` — a 5-minute in-memory cache used for tenant profile lookups and `tenant_configurations` reads, since those are admin-edited and rarely change.

`_get_client() -> Client` — Lazily constructs (once) and returns the module-global Supabase client using `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` from `config`.

`resolve_tenant_id(phone_number_id) -> Optional[dict]` — Looks up a tenant's full profile row from the `tenants` table by `phone_number_id`, first checking the 5-minute `_tenant_cache`. Returns `None` (and logs) if no tenant is registered for that WhatsApp phone_number_id.

`get_session_history(tenant_id, session_id, limit=10) -> List[dict]` — Fetches the last `limit` rows from `messages` ordered newest-first, then reverses to chronological order and flattens each row into `{"role", "content"}` turns. Returns `[]` on any DB error.

`is_duplicate(message_id, tenant_id) -> bool` — Checks whether a `message_id` already exists (idempotency for webhook retries). Returns `False` on DB error.

`save_message(incoming) -> bool` — Inserts a full inbound-message row into `messages`, initializing all AI-derived columns to `None`. This is the "Save-First" step.

`update_intent(message_id, intent, confidence, tenant_id=None) -> bool` — Updates the `intent`/`confidence` columns after classification.

`update_entities(message_id, entities) -> bool` — Writes extracted entity fields back onto the `messages` row.

`update_reply(message_id, reply_text, replied_at, graphrag_response=None) -> bool` — Stores the bot's final reply text/timestamp; optionally stores the raw GraphRAG JSON response for later re-derivation of product context.

`get_latest_graphrag_response(tenant_id, session_id) -> Optional[str]` — Finds the most recent `messages` row with a non-null `graphrag_response`.

`save_outbound_message(tenant_id, session_id, message_id, text, media_url=None, original_type="text", region="india") -> bool` — Inserts an audit row for a bot-initiated/outbound message, generating its own `trace_id`.

`get_reply_by_message_id(tenant_id, message_id) -> Optional[str]` — Looks up either `text` or `reply_text` for a given `message_id` — resolves WhatsApp "quoted/replied-to" messages back to their content.

`save_pending_order(tenant_id, session_id, product_name, quantity_value, quantity_unit) -> bool` — Upserts a single-item `ORDER_PENDING` row into `workflow_sessions`, expiring in 20 minutes.

`get_pending_order(tenant_id, session_id) -> Optional[dict]` — Reads back the most recent non-expired `ORDER_PENDING` row.

`delete_pending_order(tenant_id, session_id) -> bool` — Deletes the `ORDER_PENDING` row(s) once confirmed/cancelled.

`get_last_order(tenant_id, session_id) -> Optional[dict]` — Fetches the most recent `WORKFLOW_ACTION` `messages` row with a product name. **No external callers found** — superseded by `get_last_order_from_orders`.

`get_last_n_orders(tenant_id, session_id, n=2) -> list` — Same idea, up to `n` rows. **Also no external callers found**.

`get_last_order_from_orders(tenant_id, session_id) -> Optional[dict]` — Fetches the most recent row from the `orders` table (order_id, invoice_url, total_with_gst).

`get_last_n_orders_from_orders(tenant_id, session_id, n=2) -> list` — Same, `n` most recent confirmed orders (`MULTI_ORDER_INQUIRY`).

`save_product_api_response(tenant_id, sku, api_response) -> bool` — Upserts one product's API response into `product_cache`, keyed on `(tenant_id, sku)`. SKU uppercased before storage.

`save_product_api_responses_batch(tenant_id, items) -> bool` — Batches many `{sku, api_response}` pairs into a **single** `upsert()` call — cuts a 100-product category search from 15-20+ seconds to ~200-500ms. Derives a lowercased `product_name` column per row. On exception, falls back to calling `save_product_api_response` once per item.

`get_product_api_response(tenant_id, sku, max_age_hours=24) -> Optional[list]` — Reads a cached product response newer than `now - max_age_hours`. Returns `None` on cache miss/stale.

`get_cached_product_by_name(tenant_id, product_name) -> Optional[dict]` — Two-path lookup: strips parenthetical suffixes, then (1) tries a server-side case-insensitive `ilike` filter, and (2) falls back to fetching all cached rows and scanning in Python (for older schemas lacking the `product_name` column). Attaches `_cached_at` onto the result.

`save_graphrag_product_selection(tenant_id, session_id, products) -> bool` — Serializes a list of GraphRAG search-result products into `items_json` and upserts a `PRODUCT_SELECTION` row, expiring in 20 minutes. Orders existing rows by `created_at desc` before updating (always updates the *same* row `get_graphrag_product_selection` will read) and defensively deletes any other stale rows.

`get_graphrag_product_selection(tenant_id, session_id) -> Optional[list]` — Reads back the non-expired `PRODUCT_SELECTION` row.

`save_category_selection(tenant_id, session_id, categories) -> bool` — Same save/upsert/dedup pattern, for category name strings under `CATEGORY_SELECTION`.

`get_category_selection(tenant_id, session_id) -> Optional[list]` — Reads back the non-expired category list.

`clear_category_selection(tenant_id, session_id) -> bool` — Marks the row `COMPLETED`.

`save_negotiation_state(tenant_id, session_id, state) -> bool` — Upserts a `NEGOTIATING` row with the full negotiation state JSON-serialized, expiring in 30 minutes (longer than 20, since haggling can take longer).

`get_negotiation_state(tenant_id, session_id) -> Optional[dict]` — Reads back the most recent non-expired `NEGOTIATING` state.

`clear_negotiation_state(tenant_id, session_id) -> bool` — Marks the row `COMPLETED`.

`clear_post_order_context(tenant_id, session_id) -> bool` — After a confirmed order, bulk-marks every stale conversational state (`LAST_DISCUSSED_PRODUCT`, `PRODUCT_SELECTION`, `ORDER_PENDING`, `WORKFLOW_PENDING`, `CATEGORY_SELECTION`) as `COMPLETED` in one call, so the next message starts clean.

`update_order_invoice_url(order_id, tenant_id, invoice_url) -> bool` — Updates `invoice_url` once a PDF has been generated.

`save_last_discussed_product(tenant_id, session_id, product_name) -> bool` — Upserts a `LAST_DISCUSSED_PRODUCT` row, expiring in **30 minutes** — shortened from a previous 24 hours because a stale product from a much earlier session was leaking into unrelated new conversations.

`get_last_discussed_product(tenant_id, session_id) -> Optional[str]` — Reads back the non-expired last-discussed product name.

`save_tenant_offers(tenant_id, offers_text, tiers_json=None) -> bool` — Upserts store-wide `global_offers` text (and optional pre-parsed discount tiers) into `tenant_offers`, keyed uniquely by `tenant_id`. No-ops if `offers_text` is empty.

`get_tenant_offers(tenant_id) -> Optional[dict]` — Fetches the stored `{offers_text, tiers_json}` for a tenant.

`get_tenant_config(tenant_id, key) -> Optional[dict]` — Fetches a JSONB config value from `tenant_configurations` by `(tenant_id, config_key)`, cached in the same 5-minute `_tenant_cache`.

### Example
```python
tenant = await resolve_tenant_id("104567890123456")
# -> {"tenant_id": "tenant_inventaa_led_001", "biz_name": "Inventaa", "gst_rate": 18, ...}

await save_graphrag_product_selection(
    tenant_id="tenant_inventaa_led_001",
    session_id="91987654xxxx",
    products=[{"name": "Mini Elena 10W Gate Light", "sku": "10C-2012", "price_num": 850}],
)
# Later, when customer replies "1":
options = await get_graphrag_product_selection("tenant_inventaa_led_001", "91987654xxxx")
# -> [{"product_name": "Mini Elena 10W Gate Light", "sku": "10C-2012", "list_price": 850.0, ...}]
```

### How it connects
- Imports `run_sync`/`TTLCache` from `db/db_utils.py`; imports `IncomingMessage`/`EntityResult` from `models/schemas.py`; reads `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` from `config`.
- `db/product_store.py` imports `get_product_api_response`/`save_product_api_response` for SKU price caching.
- `db/prompt_store.py` and `db/customer_data_service.py` import `_get_client` (shares the same Supabase client).
- Widely consumed across the AI/pipeline layers: `main.py`, `pipeline/setup.py`, `pipeline/router.py`, `ai/invoice_handler.py`, `ai/product_followup.py`, `ai/graphrag_handler.py`, `ai/context_builder.py`, `ai/product_context_resolver.py`, `ai/knowledge_accessor.py`, `ai/customer_history_handler.py`, `ai/handlers.py` all call into this module.
- `get_last_order` and `get_last_n_orders` (messages-table variants) currently have **no callers anywhere in the repo** — dead code.

---

## db/prompt_store.py

### Why this file exists
This is the single gateway for every AI/customer-facing prompt template in the system (~90 prompt keys covering intent classification, negotiation, GraphRAG replies, invoice messages, etc.). It exists so that prompt text lives in a per-tenant DB table (`prompt_templates`) rather than being hardcoded in Python. Without this module, every AI handler would need its own DB query + cache + fallback logic duplicated, and per-tenant customization would be impossible.

### How it works

**Module-level cache:** `_CACHE: dict` + `_CACHE_TTL = 300` (5 minutes) — a hand-rolled TTL cache keyed by `f"{tenant_id}::{name}::{lang}"`, with helpers `_cache_key`, `_cache_get`, `_cache_set`, `_cache_invalidate`.

`PromptTemplate` (dataclass) — Carries `name`, `template`, `source` (`"CACHE"|"DB"|"LEGACY"`), `version`, `language`, `tenant_id`.

`_PromptLoader.load(tenant_id, name, lang, incoming=None) -> Optional[PromptTemplate]` (staticmethod, sync) — Three-tier resolution: (1) in-memory cache; (2) `prompt_templates` DB table (primary source); (3) legacy fallback — reads a same-named attribute directly off the `incoming` object (works only for older prompt keys mapping to actual `tenants` table columns, per `PROMPT_KEYS`). Returns `None` if all three miss.

`_PromptLoader._from_db(tenant_id, name, lang) -> Optional[tuple]` (staticmethod, sync, blocking) — Directly queries `prompt_templates` for `status='active'`, ordered by `version desc`, limit 1.

`_PromptLoader.aload(tenant_id, name, lang, incoming=None) -> Optional[PromptTemplate]` (staticmethod, async) — Identical resolution order, offloaded through `run_sync` so it doesn't block the event loop — for concurrent multi-prompt fetches via `asyncio.gather`.

`_PromptRenderer.render(pt, **vars) -> str` (staticmethod) — Substitutes `{variable}` placeholders using **`str.replace()` only**, deliberately never `str.format_map()`, since prompts can contain literal JSON like `{"type":"X"}` which `format_map()` would crash trying to interpret. Converts literal `\n`/`\t` two-character sequences into real newlines/tabs first, because Postgres plain string literals don't interpret backslash-n as an escape. After substitution, regex-scans for leftover `{name}` placeholders and prints a `[PROMPT] WARNING` (not a raise) if any remain.

`get_raw_prompt(incoming, key) -> Optional[str]` — Loads a template's raw text with **no** variable rendering (used for JSON config payloads like `product_field_aliases`). Returns `None` on miss.

`get_prompt(incoming, key, **template_vars) -> str` — The primary sync public API: validates `key` is in `PROMPT_KEYS` (raises `RuntimeError` if not registered), loads and renders. Raises `RuntimeError` if the prompt isn't found — the intended fix is seeding `prompt_templates` via a migration, not adding a Python fallback string.

`aget_prompt(incoming, key, **template_vars) -> str` — Async twin, uses `_PromptLoader.aload` so several independent prompt loads can run concurrently.

`get_prompt_with_context(incoming, key, inject_mem0=True, **template_vars) -> str` (async) — Calls `get_prompt` first, then — only if the rendered text still contains one of a fixed set of context placeholders — lazily builds an `AIRequestContext`/`ContextBuilder` (caching onto `incoming._cached_arc`) and fills those placeholders in via string replace. Despite the parameter name `inject_mem0`, this now injects **Postgres-derived** semantic context (Mem0 has been removed) — the name is legacy.

`update_prompt_template(tenant_id, prompt_name, new_text, language="en", description="") -> bool` — Admin/write-path: archives the currently-active row, inserts a new row at `version+1`, invalidates the cache entry. Synchronous, unlike the rest of the read API.

`_load_from_db = _PromptLoader._from_db` — a module-level compatibility alias, documented as "used by prompt_builder.py".

**`PROMPT_KEYS: dict`** — the master registry (~90 entries) mapping every valid `prompt_name` to its legacy `IncomingMessage` attribute name. `get_prompt`/`aget_prompt` reject any `key` not present here.

### Example
```python
reply = get_prompt(
    incoming,
    "neg_first_offer_prompt",
    product_name="Mini Elena 10W Gate Light",
    price="850",
    discount_pct="5",
)
```

### How it connects
- Imports `_get_client` from `db/session_store.py` and `run_sync` from `db/db_utils.py`.
- `ai/context_builder.py`/`ai/request_context.py` are imported lazily inside `get_prompt_with_context` to avoid a circular import.
- Called from nearly every AI handler that produces customer-facing text: `ai/invoice_handler.py`, `ai/customer_history_handler.py`, `ai/product_followup.py`, `ai/negotiator.py`, `ai/handlers.py`, `ai/graphrag_handler.py`, `ai/pricing.py`, `pipeline/router.py`, `pipeline/setup.py`, `main.py`.
- `update_prompt_template` has no in-repo caller found via grep — implying it's invoked from an external admin script or `scratch/` tooling.

---

## db/product_store.py

### Why this file exists
This module owns SKU detection/extraction, the price-lookup routing logic (SKU → cache → external Products API), and confirmed-order persistence (`orders` + `order_items` tables). It's separate from `session_store.py` because it encapsulates a specific external integration (Products API over HTTP) and business rules (what counts as a valid SKU). The old Supabase `products` table has been dropped entirely — all pricing now flows exclusively through the cached/live Products API.

### How it works

**Module-level state:** own lazy `_supabase`/`_get_client()` singleton.

`_generate_order_id() -> str` — Returns a short unique order id like `INV#A1B2C`.

`_is_sku(text) -> bool` — Heuristic SKU detector: length 4–15 chars, no spaces, must contain at least one digit (rejects plain English words like "WANT"), must contain at least one letter (rejects pure numbers), every character must be in an allowed set (`A-Za-z0-9-()`).

`_extract_skus_from_text(text) -> list` — Splits the uppercased message on whitespace, strips specific leading/trailing punctuation (explicitly **not** parentheses, since `(` `)` are valid SKU characters like in `LOS06Y(M1)`), keeps unique tokens passing `_is_sku`.

`_fetch_from_products_api(skus) -> list` (async) — POSTs `{"skus": [...]}` to `PRODUCTS_API_URL` with a 10-second timeout. Returns `[]` on 403/non-200/exception — never raises. Augments each result with normalized keys: `product_name`, `list_price`, `floor_price` (**hardcoded as 85% of list price** — a fixed 15% negotiation floor baked into this function), `product_url`, `discount_pct`, `regular_price`.

`get_product_price(tenant_id, product_name) -> Optional[dict]` (async) — SKU-only price lookup: immediately returns `None` if `product_name` doesn't pass `_is_sku`. Checks DB cache first, falls back to `_fetch_from_products_api`, persists the result to cache.

`create_order(tenant_id, session_id, sender_name, items, gst_rate=0.18, extra_fields=None, shipping_address=None, status="CONFIRMED") -> Optional[dict]` (async) — Computes `total_price` and `total_with_gst = total_price * (1 + gst_rate)`. Builds an `orders` header row — single-item orders carry product/qty/price directly on the header; multi-item orders get a synthetic `"{N} products"` header with detail in `order_items`. Inserts the header first, then line items. Merges in any `extra_fields` (e.g. discount breakdown fields used only by the invoice PDF, never written to the DB) onto the in-memory returned dict.

`get_order_by_id(order_id, tenant_id) -> Optional[dict]` (async) — Simple fetch-by-id. **No callers found anywhere else in the repo** — appears unused/dead.

`update_order_status_and_address(order_id, tenant_id, status, shipping_address) -> Optional[dict]` (async) — Updates `status`/`shipping_address`, then fetches matching `order_items` rows and attaches them so the invoice generator has the full line-item breakdown.

### Example
```python
price = await get_product_price("tenant_inventaa_led_001", "10C-2012")
# -> {"product_name": "Mini Elena 10W Gate Light", "list_price": 850.0, "floor_price": 722.5, ...}

order = await create_order(
    tenant_id="tenant_inventaa_led_001", session_id="91987654xxxx", sender_name="Praveen",
    items=[{"product_name": "Mini Elena 10W Gate Light", "quantity_value": 10,
            "quantity_unit": "pcs", "unit_price": 850.0}],
    gst_rate=0.18,
)
# -> {"order_id": "INV#A1B2C", "total_price": 8500.0, "total_with_gst": 10030.0, "items": [...]}
```

### How it connects
- Imports `run_sync` from `db/db_utils.py`; imports `get_product_api_response`/`save_product_api_response` from `db/session_store.py` (lazily, to avoid a circular import); reads `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `PRODUCTS_API_URL` from `config`.
- `_is_sku` is also imported directly by `pipeline/router.py` and several `scratch/*.py` utility scripts; per `CLAUDE.md` CHANGE 2, `ai/entity_extractor.py` **used to** gate product names through `_is_sku` but that check was deliberately removed there (GraphRAG now handles fuzzy product-name matching instead) — `_is_sku` is still used for the narrower purpose of routing a message to the direct-price-lookup path.
- `create_order` is called from `ai/order_service.py`. `update_order_status_and_address` is called from `pipeline/router.py`.

---

## db/customer_data_service.py

### Why this file exists
Per its header comment, this is the dedicated service for writing/reading **structured** customer business data (product views, preferences, negotiation outcomes, offer history, order history) as a deliberate replacement for Mem0's unstructured conversational memory — everything here is typed rows in Postgres/Supabase rather than embedding-searched free text. It's organized as a class (rather than free functions like the other db/*.py files) because every method needs the same `(tenant_id, session_id)` pair, so the class constructor captures that once.

### How it works

`CustomerDataService.__init__(self, tenant_id, session_id)` — stores both as instance attributes.

- `save_product_view(self, product_name)` (async) — Inserts a row into `product_views`. Swallows all exceptions (logs "possibly missing table").
- `get_recent_product_views(self, limit=5)` (async) — Selects ordered by `viewed_at desc`. `[]` on error.
- `save_preference(self, pref_type, value)` (async) — Upserts into `customer_preferences` on conflict key `tenant_id,session_id,pref_type`.
- `get_preferences(self)` (async) — Selects all `(pref_type, value)` pairs, folds into a dict.
- `save_negotiation_outcome(self, product, opening_price, final_price, rounds, accepted, quantity)` (async) — Inserts into `negotiation_history`.
- `get_negotiation_history(self, limit=5)` (async) — Selects recent rows, newest first.
- `save_offer_history(self, product, offer_tier, discount_applied, threshold, accepted)` (async) — Inserts into `customer_offers`.
- `get_offer_history(self, limit=5)` (async) — Selects recent rows.
- `get_order_history(self, limit=5)` (async) — Selects from `orders`, newest first.
- `get_latest_order(self)` (async) — `get_order_history(limit=1)`, single row or `None`.
- `get_previous_offers`, `get_customer_preferences`, `get_recent_products`, `get_latest_negotiation`, `get_purchase_history` — thin aliases/wrappers around the above with different default limits.
- `get_latest_invoice(self)` (async) — Most recent `orders` row where `invoice_url IS NOT NULL`.
- `get_latest_ordered_product(self)` (async) — `get_latest_order()` then returns just `product_name`.
- `get_customer_summary(self) -> dict` (async) — The richest method — fetches `preferences`, `negotiation_history(limit=10)`, and `order_history(limit=10)` **concurrently** via `asyncio.gather`, then derives: favorite category, total order count, total amount spent, last purchase date, favorite product (mode of `product_name` across orders), and average accepted-negotiation discount percentage (computed per negotiation as `(initial_price - final_price) / initial_price * 100`, averaged only over `accepted=True` negotiations). Also exposes raw `_negotiations`/`_orders` lists so callers needing per-item detail (like `ContextBuilder`) can slice them without a second DB round-trip. Returns `{}` on any exception.

### Example
```python
cds = CustomerDataService("tenant_inventaa_led_001", "91987654xxxx")
summary = await cds.get_customer_summary()
# -> {"favorite_product": "Mini Elena 10W Gate Light", "total_orders": 3,
#     "total_spent": 24500.0, "avg_negotiation_discount_pct": 4.2, ...}
```

### How it connects
- Imports `_get_client` from `db/session_store.py` and `run_sync` from `db/db_utils.py`.
- Instantiated throughout the AI layer: `ai/customer_profile_updater.py`, `ai/customer_history_handler.py`, `ai/graphrag_request_builder.py`, `ai/context_builder.py`, `ai/graphrag_handler.py`, `ai/order_service.py`, `ai/product_context_resolver.py`, and `pipeline/router.py`.

---

## db/processing_lock.py

### Why this file exists
Per its header, this replaces an old in-memory `_processing_sessions` set with a distributed lock backed by Supabase, so that if the bot ever runs as multiple worker processes/instances, two workers can't process the same customer's message concurrently (which would risk duplicate replies or double-charged orders).

### How it works

**Module-level state:** own lazy `_supabase`/`_get_client()` singleton.

`acquire_lock(session_id, tenant_id) -> bool` (async) — Attempts an `INSERT` into `processing_locks` with `session_id` as the row's primary key. Because Postgres enforces primary-key uniqueness, only one concurrent `INSERT` for the same `session_id` can succeed — atomic without a separate `SELECT ... FOR UPDATE`. Returns `True` on success; catches the duplicate-key exception and returns `False`.

`release_lock(session_id, tenant_id=None) -> None` (async) — Deletes the lock row, optionally also filtered by `tenant_id` — guards against one tenant's `session_id` accidentally releasing a different tenant's lock (edge case: same phone number reused across two WABAs). Called from a `finally` block; failure here is logged but non-fatal.

`cleanup_stale_locks() -> None` (async) — Deletes every `processing_locks` row with `locked_at` older than 2 minutes. Called at the very start of handling each incoming message, to recover from the case where a server crashed mid-pipeline and `release_lock()` never ran. The 2-minute cutoff is chosen to be comfortably above the worst-case pipeline duration ("4 LLM calls * 30s timeout = 120s").

### Example
```python
await cleanup_stale_locks()
if not await acquire_lock("91987654xxxx", "tenant_inventaa_led_001"):
    return  # another worker is already handling this session
try:
    ...  # run the pipeline
finally:
    await release_lock("91987654xxxx", "tenant_inventaa_led_001")
```

### How it connects
- Imports `run_sync` from `db/db_utils.py`; reads `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` from `config`.
- `main.py` imports `release_lock`/`cleanup_stale_locks` directly. `pipeline/setup.py` imports `acquire_lock` and calls it to gate whether the pipeline proceeds, with an explicit comment warning never to call `release_lock()` after a failed/early return that never actually acquired the lock.

---

## db/db_utils.py

### Why this file exists
The `supabase-py` client library is synchronous/blocking network I/O, but the whole application is built on `asyncio`. Calling the client directly inside an `async def` would block the single event loop for the duration of every DB round-trip, stalling every other tenant's in-flight conversation, not just the caller's. This tiny shared module exists purely to fix that one structural problem in exactly one place, plus provide a single reusable TTL-cache primitive so every db/*.py file doesn't reinvent its own.

### How it works

`run_sync(fn: Callable[[], T]) -> T` (async) — Runs an arbitrary blocking callable (a Supabase query chain, but also reused for PDF rendering/storage uploads in `utils/invoice.py`) on a worker thread via `asyncio.to_thread(fn)`. Wraps the call in a `try/finally` that always records elapsed wall-clock time to `ai.request_profiler.add("db", ...)` regardless of success/failure — meaning this function is the single choke point for that latency metric, and for requests that touch invoice generation, the "db" bucket also silently absorbs that PDF/upload time rather than being pure DB latency.

`TTLCache` (class) — A generic in-memory TTL cache keyed by arbitrary strings, not tenant-/table-specific — callers build their own scoped key. Mirrors the bespoke cache pattern in `db/prompt_store.py`.
- `__init__(self, ttl_seconds=300)` — sets TTL and an empty dict.
- `get(self, key)` — returns the cached value if present and not expired.
- `set(self, key, value)` — stores `(value, now + ttl_seconds)`.
- `invalidate(self, key)` — removes a key immediately.

### Example
```python
cache = TTLCache(ttl_seconds=300)
cache.set("tenant::104567890123456", tenant_row)
cache.get("tenant::104567890123456")  # -> tenant_row, until 5 minutes pass

result = await run_sync(lambda: supabase_client.table("orders").select("*").execute())
```

### How it connects
- No project imports — leaf utility module with only stdlib dependencies, plus a lazy in-function import of `ai/request_profiler.py` inside `run_sync` (kept lazy to avoid a circular import).
- `run_sync` is imported and used by every other db/*.py file: `db/session_store.py`, `db/prompt_store.py`, `db/workflow_state.py`, `db/product_store.py`, `db/customer_data_service.py`, `db/processing_lock.py`.
- `TTLCache` is instantiated in `db/session_store.py` as `_tenant_cache`.
- `utils/invoice.py` also flows through `run_sync` for blocking PDF-render/storage-upload work.

---

## db/__init__.py

A one-line, effectively empty file (`# db package`) whose only job is to make `db/` an importable Python package so `from db.session_store import ...`-style imports work. No re-exports, no `__all__` — every module inside `db/` must be imported by its fully-qualified submodule path.

---

## db/workflow_state.py *(dead code — see Appendix)*

### Why this file was built
Per its own header comment, this module was built to manage a `WORKFLOW_PENDING` state — caching a partially-collected order (e.g. product given, quantity missing) so a later bare reply like "500gm" can be merged with the earlier context. A clean, single-purpose "5 functions" design built around a 20-minute expiry window. **No other file in the repository imports or calls any function from this module** (confirmed via repo-wide grep) — the equivalent functionality actually wired into the live pipeline is implemented directly against `workflow_sessions` inside `db/session_store.py` instead (`save_pending_order`/`get_pending_order`/`delete_pending_order`, plus the `PRODUCT_SELECTION`/`NEGOTIATING`/`LAST_DISCUSSED_PRODUCT` status rows).

### How it works
**Module-level state:** own separate lazy `_supabase` client singleton (not reused from `session_store.py`, though it connects to the same DB).

`_now_ist() -> datetime` — Returns current time shifted to IST via a fixed offset. Defined but not referenced anywhere else in this file — appears to be leftover/unused.

`get_pending_state(tenant_id, session_id) -> Optional[dict]` (async) — Queries `workflow_sessions` for a non-expired `WORKFLOW_PENDING` row.

`save_pending_state(tenant_id, session_id, product_name, quantity_value, quantity_unit, delivery_date, missing_fields, items=None) -> bool` (async) — Upserts a `WORKFLOW_PENDING` row with a 20-minute `expires_at`. If `items` is supplied, serializes to `items_json` to support multi-product partial orders.

`merge_state(cached_state, new_entities) -> EntityResult` (sync, pure function) — The most complex function in the file. Restores the full cached items list from `items_json` (falling back to a single-item list built from flat columns). Branches on how many items the new extraction returned: (1) exactly 1 new item → fills the first item missing a quantity (or product name); (2) same count as cached → positional zip-merge; (3) any other count → assumes a full re-statement and discards the cache. Recomputes a `missing` list across all merged items.

`complete_state(tenant_id, session_id) -> bool` (async) — Flips a row to `COMPLETED` once both product and quantity are known.

`expire_state(tenant_id, session_id) -> bool` (async) — Flips a row to `EXPIRED` when its `expires_at` has already passed.

### Example
```python
state = await get_pending_state("tenant_inventaa_led_001", "91987654xxxx")
if state:
    merged = merge_state(state, new_entities)
    if not merged.missing_entities:
        await complete_state("tenant_inventaa_led_001", "91987654xxxx")
```

### How it connects
- Imports `run_sync` from `db/db_utils.py`, `EntityResult`/`OrderItem` from `models/schemas.py`, `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` from `config`.
- Confirmed zero callers anywhere in the repo — dead/superseded code left in the tree.

---

# Part 6 — Utilities & Dev Tools

## utils/invoice.py

### Why this file exists
Generates the customer-facing PDF invoice for confirmed/negotiated orders and uploads it to Supabase Storage for delivery as a WhatsApp document link. It's kept separate from `ai/invoice_handler.py` (which handles the conversational logic of *when* to send an invoice) so that PDF layout/rendering concerns are isolated from business/conversation logic. The header comments emphasize "zero hardcoding" — every business detail (name, tagline, GSTIN, UPI ID, etc.) is a function parameter sourced from the `tenants` table, making this multi-tenant by construction, and it deliberately avoids external font files (built-in Helvetica, "Rs." instead of ₹) for cross-platform reliability.

### How it works

Module-level: `_supabase` lazy singleton. Color palette constants (`DARK_BLUE`, `MID_GRAY`, `LIGHT_GRAY`, `BORDER`, `WHITE`) and `R = "Rs."` currency prefix constant.

`_get_client() -> Client` — Lazy Supabase client singleton, identical pattern to `adapter/whatsapp_adapter.py`.

`generate_invoice_pdf(order, biz_name, tagline=None, city=None, support_email=None, support_phone=None, website=None, upi_id=None, account_name=None, sender_name=None, sender_phone=None, gst_rate=0.18, gstin=None) -> bytes` — Synchronous, CPU-bound ReportLab rendering. Builds a `SimpleDocTemplate` in an in-memory `io.BytesIO` buffer, A4 page size. Renders, in order: business header with a horizontal rule; "TAX INVOICE" section title; a meta-info table (Order ID, Invoice Date computed in IST, Customer name/phone — using `sender_name`/`sender_phone` parameter overrides in preference to `order` dict fields to fix an "N/A" bug when GraphRAG-created orders had missing fields — Status, Address); an items table supporting either multiple line items or synthesizing a single-item row, with alternating row background colors; a totals table with conditional rows for `original_amount` (only if it exceeds `total_price`), `store_discount_amount`, `negotiation_discount_amount` (both green with a `-` prefix), then always Subtotal/GST/`TOTAL PAYABLE` (bold), and a "You Saved" row if total savings > 0; a payment-details section (UPI ID / Account Name, only if provided); and a footer with a thank-you line and the tenant's website. Calls `doc.build(elements)`, returns the buffer's raw `bytes`.

`async def upload_invoice_to_storage(pdf_bytes, order_id, tenant_id) -> Optional[str]` — Sanitizes `order_id` by replacing `#` with `_`, builds path `invoices/{tenant_id}/{safe_id}.pdf`. Uploads via `db.db_utils.run_sync` with `upsert: "true"`. Catches duplicate-upload errors specifically and treats them as success (idempotent — re-generating the same invoice doesn't fail), re-raising any other upload exception.

`async def generate_and_upload_invoice(order, biz_name="", tagline=None, city=None, support_email=None, support_phone=None, website=None, upi_id=None, account_name=None, sender_name=None, sender_phone=None, gst_rate=0.18, gstin=None) -> Optional[str]` — The full public flow: offloads `generate_invoice_pdf` to a worker thread via `run_sync` (CPU-bound ReportLab rendering would otherwise stall the shared asyncio event loop for every other tenant's in-flight conversation), defaulting `biz_name` to `"Order Tracking AI"` if falsy. Calls `upload_invoice_to_storage`.

### Example
`await generate_and_upload_invoice(order={"order_id": "ORD-1042", "tenant_id": "tenant_inventaa_led_001", "total_price": 2500, "total_with_gst": 2950, "items": [...]}, biz_name="Inventaa LED", gst_rate=0.18, upi_id="inventaa@upi")` renders a PDF with the Inventaa branding, uploads it to `invoices/tenant_inventaa_led_001/ORD-1042.pdf`, and returns the public URL.

### How it connects
- Imports: `reportlab` (pagesizes, units, colors, platypus flowables, styles, enums), `supabase.create_client`/`Client`, `config` (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_STORAGE_BUCKET`), `db.db_utils.run_sync`; type-only import of `ai.order_service.OrderResult` under `TYPE_CHECKING`.
- `generate_and_upload_invoice` is called by `ai/invoice_handler.py` — the module that decides *when* to send an invoice, which calls into this module to build it and deliver the resulting URL.

---

## utils/conversation_actions.py

### Why this file exists
A single shared helper used by both `pipeline/router.py` and `ai/negotiator.py` to detect exact-phrase "quick action" matches (e.g. "yes", "confirm", "cancel") without invoking the LLM, for deterministic, low-latency short-circuiting of common conversational actions. Centralizing it avoids two different modules implementing subtly different matching rules for the same underlying config (`tenants.quick_actions` JSONB column).

### How it works
No module-level state (the frozenset normalization happens once in `pipeline/setup.py._apply_tenant`, not here).

`def is_quick_action(incoming, action, message=None) -> bool` — Reads `incoming.quick_actions` (a dict of `{action_key: frozenset(casefolded_phrases)}`); returns `False` if unset or the action key isn't present. Otherwise checks whether `(message or incoming.text).casefold().strip()` is an **exact** member of that frozenset — no regex, no substring/`startswith` matching, no fuzzy logic (deliberately — "yes" matches, "yes please" does not and falls through to the LLM). Emoji are intentionally excluded from configured phrase lists because Unicode modifier sequences make byte-level equality unreliable.

`def is_quick_confirm(incoming, message=None) -> bool` — Convenience wrapper for `is_quick_action(incoming, "ORDER_CONFIRM", message)`.

`def is_quick_cancel(incoming, message=None) -> bool` — Convenience wrapper for `is_quick_action(incoming, "ORDER_CANCEL", message)`.

### Example
`is_quick_confirm(incoming)` where `incoming.text == "Yes"` and quick_actions includes `{"ORDER_CONFIRM": frozenset({"yes", "confirm", "done", "place order"})}` returns `True` with zero LLM calls. With text `"yes please"` it returns `False` — falls through to `pipeline/router.py`'s LLM-based fallback.

### How it connects
- Imports: only `typing.Optional` — no project-internal imports.
- `is_quick_confirm` is called by `pipeline/router.py` (`_check_fast_confirm`, as the zero-cost first check before the LLM fallback).
- `is_quick_action`/`is_quick_cancel` are used by `ai/negotiator.py`.
- The frozenset data it reads is populated by `pipeline/setup.py._apply_tenant` from `tenants.quick_actions`.

---

## utils/alerting.py *(dead code — see Appendix)*

### Why this file was built
Provides a lightweight, fail-silent Slack notification mechanism for two operational concerns: pipeline crashes and Mem0 degradation (falling back to Postgres). Built as a true no-op (zero behavior, zero risk) when `SLACK_WEBHOOK_URL` isn't configured, without callers needing to check a flag themselves.

### How it works
`async def alert_pipeline_error(error, incoming) -> None` — No-ops immediately if `config.SLACK_WEBHOOK_URL` is falsy. Otherwise builds a Slack message with tenant ID, masked phone (last 4 digits), truncated message text, and the error type/message, tagged with `APP_ENV`. Posts via a 5s-timeout `httpx.AsyncClient`, wrapped in its own `try/except` — alerting must never crash the thing it's alerting about.

`async def alert_mem0_degraded(error, operation) -> None` — Same no-op-if-unconfigured pattern, naming the failed operation and noting the fallback to Postgres with no data loss.

### Example
`await alert_pipeline_error(ValueError("neg_state is None"), incoming)` — if configured, posts a formatted Slack message; if unset, does nothing.

### How it connects
- Imports: `httpx`, `config` (`SLACK_WEBHOOK_URL`, `APP_ENV`).
- **Neither function is actually imported/called anywhere else in the codebase** (confirmed via repo-wide grep). This contradicts `CLAUDE.md`'s "CHANGE 5" entry, which documents `alert_pipeline_error` as wired into `main.py`'s `run_pipeline()` — but the actual `main.py::run_pipeline()` has no `try/except` wrapping the pipeline body that would call this, only a `try/finally` for lock release, and no import of `utils.alerting` at all.

---

## check_intent_prompt.py

### Why this file exists
A standalone CLI diagnostic script (not imported by the running application) for developers/ops to inspect what a given tenant's active DB-stored prompt currently reads, without needing to query Supabase manually. Useful when debugging why a tenant's intent classification (or any other DB-driven prompt) is behaving unexpectedly.

### How it works
`def main()` — Parses CLI args via `argparse`: `--tenant-id` (default `"tenant_inventaa_led_001"`), `--prompt-name` (default `"intent_system_prompt"`), `--language` (default `"en"`). Uses `db.session_store._get_client()` directly (bypassing `db.prompt_store`'s caching/fallback logic — a raw read). Queries `prompt_templates` filtered by tenant/name/language/`status="active"`, ordered by `version desc`, limit 1. Prints `prompt_name`, `tenant_id`, `version`, `updated_at`, and the full `prompt_text`, or a "not found" message.

`if __name__ == "__main__": main()` — Standard script entry guard.

### Example
`python check_intent_prompt.py --tenant-id tenant_inventaa_led_001 --prompt-name greeting_system_prompt` prints the exact active `greeting_system_prompt` text for that tenant, version number, and last-updated timestamp.

### How it connects
- Imports: `argparse`, `db.session_store._get_client`.
- Not imported by any other module — a manually-run developer tool, separate from the FastAPI application's import graph.

---

## Package `__init__.py` files (summary)

- **`pipeline/__init__.py`** — empty. Groups `pipeline/setup.py` and `pipeline/router.py`; both imported directly, nothing re-exported.
- **`utils/__init__.py`** — empty. Groups `utils/invoice.py`, `utils/alerting.py`, `utils/conversation_actions.py`; each imported by full dotted path elsewhere.
- **`adapter/__init__.py`** — one comment line. Groups `adapter/whatsapp_adapter.py`; no re-exports.
- **`messaging/__init__.py`** — one line: `from messaging.sender import send_reply, send_image`. Unlike the other `__init__.py` files, this one **does** re-export, which is why callers write `from messaging import send_reply` rather than reaching into `messaging.sender` directly.
- **`models/__init__.py`** — one comment line. Groups `models/schemas.py`; callers use `from models.schemas import IncomingMessage` (full path).
- **`ai/__init__.py`** — one comment line. Every `ai/` submodule imported by full dotted path.
- **`db/__init__.py`** — one comment line. Every `db/` submodule imported by full dotted path.

---

# Part 7 — Local Test Tooling (Streamlit UI & Regression Script)

*Note: `interface/` and `regression_test.py` are both listed in `.gitignore` — untracked local dev
tooling. Neither is imported by any production module; both talk to the running FastAPI server
purely over HTTP (`/chat`, `/reset`), exactly like a real WhatsApp webhook would (minus Meta's
envelope).*

## interface/api.py

### Why this file exists
This is the sole HTTP bridge between the local Streamlit test UI and the real FastAPI backend (`main.py`). It exists so the Streamlit process never touches pipeline/DB code directly — it only ever talks over HTTP to `/chat` and `/reset`. Without it, `app.py` would have no way to drive `run_pipeline()` and get replies back.

### How it works
Module-level constants: `BACKEND_URL` (default `http://localhost:8000`), `DEFAULT_PHONE_ID`, `DEFAULT_TENANT_ID`, `DEFAULT_SENDER_NAME` — all read from env vars with hardcoded fallbacks for zero-config local dev.

- `send_message_to_backend(phone, message, sender_name=DEFAULT_SENDER_NAME, phone_number_id=DEFAULT_PHONE_ID) -> Optional[Dict[str, Any]]` — POSTs `{phone, message, sender_name, phone_number_id}` to `{BACKEND_URL}/chat` using a 45s-timeout `httpx.Client`. On HTTP 200 returns the parsed JSON. On a non-200 status it swallows the error and fabricates a synthetic reply payload (`debug.intent = "ERROR"`) so the UI still has a well-formed object to render. On any exception (e.g. backend not running) does the same with `debug.intent = "CONNECTION_ERROR"`. Never raises — callers can always safely call `.get("replies")` / `.get("debug")`.
- `reset_session_on_backend(phone, tenant_id=DEFAULT_TENANT_ID) -> bool` — POSTs `{phone, tenant_id}` to `{BACKEND_URL}/reset` with a 10s timeout. Returns `True` only on exact status 200.

### Example
```python
resp = send_message_to_backend(phone="918897726664", message="hi")
# resp == {"replies": [{"type": "text", "body": "Hello! ..."}],
#          "debug": {"intent": "GREETING", "confidence": 0.9, "latency": 0.4, ...}}
```

### How it connects
- Imports: stdlib `os` and third-party `httpx`.
- Calls out to: `main.py`'s `/chat` and `/reset` FastAPI routes over real HTTP.
- Called by: `interface/app.py` (main chat loop) and `interface/components/sidebar.py` (reset button).

---

## interface/app.py

### Why this file exists
This is the Streamlit entrypoint — the page run with `streamlit run interface/app.py`. It gives developers a WhatsApp-lookalike chat window for manually exercising the pipeline without a real WhatsApp Business number or ngrok tunnel. It wires together every other `interface/` module into one runnable script.

### How it works
Top of file: inserts the project root into `sys.path`, then calls `st.set_page_config(...)` (must run before any other Streamlit call).

The file has no functions/classes — a linear script executed top-to-bottom on every Streamlit rerun:
1. `inject_whatsapp_css()` — injects the WhatsApp-Web-styled CSS.
2. `render_sidebar()` — draws customer profile inputs, reset button, dev-mode toggle, debug panel.
3. Initializes `st.session_state.messages`/`debug_info` if not already present.
4. Renders the chat header and messages inside a `.chat-container` div.
5. `st.chat_input(...)` captures new user text. If present, appends a user message and calls `st.rerun()` immediately — this is what makes the user's own bubble appear before the bot's reply is fetched (a two-phase render pattern needed because Streamlit reruns the whole script top-to-bottom on every interaction).
6. On the rerun, since the last message is from the user, calls `render_typing_indicator()` then `send_message_to_backend(...)` synchronously (blocking until the backend responds). On success stores `debug_info` and appends the assistant message, then reruns again.

Edge case: because step 6 keys off "last message is from user," if `send_message_to_backend` ever returned `None`/falsy (should not normally happen) no assistant message would be appended, leaving the typing indicator stuck until the next click.

### Example
`streamlit run interface/app.py`, type "hi" → user bubble appears instantly, typing dots show, then the bot's greeting bubble appears once `/chat` responds.

### How it connects
- Imports: `interface.styles.css.inject_whatsapp_css`, `interface.components.sidebar.render_sidebar`, `interface.components.chat.render_chat_header`/`render_chat_messages`, `interface.components.typing.render_typing_indicator`, `interface.api.send_message_to_backend`.
- Not imported by anything else — the top-level script.

---

## interface/components/cards.py

### Why this file exists
Plain WhatsApp text messages don't convey rich content well (PDF invoices, product images), so this module post-processes bot reply text/attachments into WhatsApp-style visual cards, matching what a real WhatsApp client would render for a document or image message.

### How it works
- `parse_and_render_invoice(text) -> bool` — Detects an invoice-delivery message via the literal substring `"Download Invoice PDF"`. If found, regex-extracts the first URL and an order ID (`\*(ORD_[a-f0-9]+|\d+)\*`), plus an optional business name. Renders a styled `.invoice-card` div with two buttons linking to the URL. Returns `True` if rendered, `False` otherwise (caller falls back to a plain text bubble). Note: if the substring matches but no URL is found, silently returns `False` anyway.
- `render_image_bubble(img_url, caption)` — Renders a bot-style bubble containing an `<img>` tag plus an optional caption line.

### Example
```python
if not parse_and_render_invoice(reply_body):
    st.markdown(reply_body)  # plain fallback
render_image_bubble("https://cdn.example.com/light.jpg", "Mini Elena 10W Gate Light")
```

### How it connects
- Imports: `os`, `re`, `streamlit`.
- Called by: `interface/components/chat.py`'s `render_chat_messages()`.
- The regexes are tightly coupled to the backend's exact invoice message wording — if that copy changes, this parser silently stops matching.

---

## interface/components/sidebar.py

### Why this file exists
Centralizes all "test harness controls" — who the simulated customer is, resetting conversation state, and toggling a live pipeline-telemetry panel — into one sidebar so the main chat column stays visually clean.

### How it works
- `render_sidebar()` (the file's only function) — Renders a branded header. Initializes `st.session_state.customer_name`/`customer_phone` on first run only, bound to two text inputs. Renders a "Reset Conversation" button that calls `reset_session_on_backend`, clears session state, and reruns on success. Renders a "Enable Developer Mode" toggle (default `True`) stored as `st.session_state.dev_mode`. If on, renders a "Live Debug Panel" showing intent/confidence/latency/route/tenant_id from the last response, or a placeholder if nothing sent yet.

### Example
Toggling Developer Mode off hides both the sidebar debug panel and the inline per-message debug blob under bot replies — useful for demoing to non-technical stakeholders.

### How it connects
- Imports: `streamlit`, `interface.api.reset_session_on_backend`.
- Sets `customer_name`, `customer_phone`, `dev_mode`, read by `interface/app.py` and `interface/components/chat.py`.
- Called by: `interface/app.py`.

---

## interface/components/chat.py

### Why this file exists
The visual heart of the simulator — turns the abstract `st.session_state.messages` list into WhatsApp-styled chat bubbles (header bar, user/bot bubbles, invoice/image cards, debug annotations).

### How it works
- `render_chat_header(biz_name=os.getenv("DEFAULT_BIZ_NAME", "Inventaa LED Lights"))` — Renders the top green WhatsApp-Web-style bar with an avatar (first char of `biz_name`), name, and a static "online" status. Note: the default-argument expression evaluates `os.getenv(...)` once at *module import time*, not per-call — harmless here since it's only ever called with an explicit `biz_name`.
- `render_chat_messages()` — Ensures `st.session_state.messages` exists. If empty, renders a centered "encrypted" system banner (mimicking WhatsApp's first-time notice). Otherwise iterates every message: `role=="user"` renders a right-aligned green bubble with a render-time (not send-time) timestamp; `role=="assistant"` iterates `msg["replies"]`, first trying `parse_and_render_invoice` for text replies, falling back to a plain white bubble, and delegating to `render_image_bubble` for image replies. After all replies, if `dev_mode` is on and `debug` is present, renders an inline monospace debug box.

### Example
A message dict with a reply body containing "Download Invoice PDF" renders as an invoice card (via `cards.py`), not a plain bubble.

### How it connects
- Imports: `os`, `streamlit`, `datetime.datetime`, `interface.components.cards.parse_and_render_invoice`/`render_image_bubble`.
- Reads `st.session_state.messages`/`dev_mode`. Called by `interface/app.py`.

---

## interface/components/typing.py

### Why this file exists
A one-function module dedicated to the "bot is typing…" affordance shown while `app.py` blocks on the synchronous backend HTTP call.

### How it works
- `render_typing_indicator()` — Renders a left-aligned bot-style bubble containing three `<span class="typing-dot">` elements. The bounce animation itself lives in `interface/styles/css.py`'s `@keyframes bounce`; this function only emits the markup.

### Example
Called right before the blocking backend call in `app.py`: the dots appear, then get replaced (via `st.rerun()`) by the actual bot bubble once the response returns.

### How it connects
- Imports: `streamlit` only. Called by `interface/app.py`.

---

## interface/styles/css.py

### Why this file exists
Streamlit's default theme looks nothing like WhatsApp. This module is the single source of every custom CSS class referenced by every other `interface/components/*.py` file.

### How it works
- `inject_whatsapp_css()` — the file's only function. Emits one large `<style>` block via `st.markdown(..., unsafe_allow_html=True)`. No parameters, no branching — a static string constant wrapped in a function so it can be called once at a controlled point in `app.py`'s startup sequence.

Notable rules: hides Streamlit's own chrome; WhatsApp grey-cream background; `.chat-container` (centered, max-width); `.whatsapp-header*`; `.message-bubble`/`.message-bot`/`.message-user` (using `float:left`/`float:right` + `clear:both` for alignment without Streamlit's column layout); `.debug-container`; `.product-card*` (**dead CSS** — no component currently emits HTML using these classes, apparently written for a product-card component that was never built or was removed); `.invoice-card*`; `.typing-dot` + `@keyframes bounce` (staggered animation delays via `:nth-child`).

### Example
```python
inject_whatsapp_css()  # must run before any bubble rendering, else classes are undefined
```

### How it connects
- Imports: `streamlit` only. Called by `interface/app.py` as the first rendering step.
- Every `interface/components/*.py` file implicitly depends on this module's class names matching what they hardcode in their own HTML strings — there's no shared constant, so a rename here would silently break rendering elsewhere.

---

## interface/__init__.py, interface/components/__init__.py, interface/styles/__init__.py

Each is a near-empty package marker (a single comment line) making `interface`, `interface.components`, and `interface.styles` importable as Python packages so statements like `from interface.styles.css import inject_whatsapp_css` resolve correctly.

---

## regression_test.py

### Why this file exists
Per its own module docstring, every prior bug fix in this project was verified by hand (one Streamlit message at a time), which is how two "file-lineage" regressions previously slipped through undetected. This script is a repeatable, scriptable substitute: it drives the real running FastAPI server over HTTP through a chained, stateful conversation and asserts structural properties of each reply, so a developer can answer "does the whole conversation still work?" in one command instead of manual re-testing. It deliberately does NOT assert exact reply wording (reply text is DB-configurable per tenant) — it asserts *structural* signals instead (HTTP status, `debug.intent`, `debug.route`, absence of error markers, presence of expected data patterns like invoice IDs or currency amounts).

### How it works
Module constant: `DEFAULT_TENANT_ID = "tenant_inventaa_led_001"`.

**Dataclasses:**
- `TestResult(name, passed, detail="")` — a single check's outcome.
- `Session(base_url, phone, phone_number_id=None, verbose=False, last_response={})` — wraps one phone number's ongoing `/chat` conversation. `_normalize_reply` wraps a bare string reply into `{"type": "text", "body": rep}`. `send(message, sender_name="Test User")` POSTs to `/chat` (60s timeout), parses JSON (falls back to raw text on parse failure), stores `last_response`, prints diagnostics if verbose or non-200. `reply_text()` joins all text reply bodies. `debug()` returns the debug dict.
- `Suite(base_url, phone, tenant_id, verbose, phone_number_id=None)` — the top-level test runner. `reset(phone=None)` POSTs to `/reset`, warns (non-fatal) on failure. `check(name, condition, detail="")` appends a `TestResult` and prints `[PASS]`/`[FAIL]` immediately. `new_session(phone=None)` constructs a shared-config `Session`. `summary()` prints pass/total and returns `True` iff all passed (drives the process exit code).

**Shared assertion helpers:**
- `ERROR_MARKERS` constant (traceback, pipeline error, runtimeerror, keyerror, attributeerror, nonetype, [none]).
- `looks_clean(text) -> bool` — checks for `ERROR_MARKERS` or a bare word-boundary `"none"` (guards against an unfilled template variable leaking into a customer-facing reply — "the workflow_context bug fixed in migration 020").
- `has_amount(text) -> bool` — regex-checks for a currency-looking pattern.

**Scenario functions** (14 total, each calling `suite.check(...)` repeatedly):
- `scenario_greeting` — sends "hi"; aborts the whole run early if the very first call isn't HTTP 200 (avoids 13 cascading guaranteed failures).
- `scenario_browse_and_list` — sends a search then a category name; checks a numbered product list appears. Returns the `Session` for later scenarios to continue.
- `scenario_comparison` — sends "compare 1,2"; checks the reply doesn't leak into the quantity-ask branch.
- `scenario_select_and_followup` — sends a list-position selection then a follow-up question; checks for the exact "migration 020" regression (stray "1 units" pattern).
- `scenario_offers` — sends an offers inquiry.
- `scenario_order_and_qty_update` — sends an order then a quantity increase; checks ADD semantics (2+3→"5") — the exact "migration 017" regression.
- `scenario_negotiation` — drives a full haggle → confirm → mid-address renegotiation → re-confirm sequence.
- `scenario_confirmation_and_invoice` — sends a shipping address; checks for an invoice ID pattern and a PDF link.
- `scenario_fresh_conversation_isolation` — verifies one phone number's quantity state doesn't leak into a different phone number's session.
- `scenario_memory_recall` — sends a personalized-recommendation request; only confirms it doesn't crash.
- `scenario_interruption` — interrupts mid-negotiation with an unrelated question; checks the pipeline doesn't error out.
- `scenario_escalation` — sends an urgent human-agent request; checks `debug.intent == "HUMAN_ESCALATION"`.
- `main()` — builds an `argparse` CLI, runs all 14 scenarios in sequence, threading the shared `Session` through the ordering/negotiation/invoice scenarios. Exits 1 early if greeting fails; catches `ConnectionError` with a friendly hint. Ends with `suite.summary()` and a process exit code reflecting overall pass/fail.

### Example
```
uvicorn main:app --reload --port 8000          # in one terminal
python regression_test.py --phone-number-id 1124766240726230 --verbose
```
Runs all 14 scenarios against the live local server and prints a final `RESULTS: N/M checks passed` summary with a non-zero exit code on any failure.

### How it connects
- Imports: stdlib (`argparse`, `json`, `re`, `sys`, `time`, `uuid`, `dataclasses`, `typing`) plus third-party `requests` (deliberately not `httpx`, so this script is runnable standalone).
- Calls out to: `main.py`'s `/chat` and `/reset` endpoints over HTTP — same contract as `interface/api.py`, but scripted and assertion-driven.
- Gitignored, not imported by any other module — indirectly exercises nearly every backend module (intent router, negotiator, GraphRAG matching, invoice generation) without importing any of them directly, validating the system as a black box through HTTP only.
