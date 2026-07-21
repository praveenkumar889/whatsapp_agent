import os
# main.py — FastAPI Application Entry Point
#
# ARCHITECTURE:
#   This file is the ORCHESTRATOR only — no business logic lives here.
#   All logic is in specialist modules:
#
#   pipeline/setup.py      → tenant resolve, dedup, lock, history, save
#   pipeline/router.py     → neg guard, invoice guard, intent dispatch
#   ai/intent_router.py    → LLM intent classification
#   db/session_store.py    → all Supabase DB operations
#   db/memory_store.py     → Mem0 context + workflow state
#
# PIPELINE (8 steps):
#   1. Parse webhook        → IncomingMessage
#   2. Setup pipeline       → tenant, dedup, lock, history, save
#   3. Classify intent      → LLM classification
#   4. Update intent in DB  → audit
#   5. Dispatch             → neg guard → invoice guard → intent routing
#   6. Send reply           → WhatsApp / mock channel
#   7. Store reply          → Mem0 turn + DB audit
#   8. Return debug info

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse

from config import VERIFY_TOKEN, WEBHOOK_SECRET, APP_ENV, AZURE_OPENAI_DEPLOYMENT
from adapter.whatsapp_adapter import parse_webhook
from models.schemas import IncomingMessage
from ai.handlers import classify_intent
from db.session_store import update_intent, update_reply, save_outbound_message
from db.processing_lock import release_lock, cleanup_stale_locks
from pipeline.setup import setup_pipeline
from pipeline.router import dispatch
from messaging import send_reply

app = FastAPI(title="WhatsApp AI Agent")

# ── Concurrency guard — max 50 simultaneous pipeline runs ─────────────────────
_pipeline_semaphore = asyncio.Semaphore(50)


# ══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    asyncio.create_task(_periodic_lock_cleanup())
    print("[STARTUP] Periodic lock cleanup task started")


async def _periodic_lock_cleanup():
    while True:
        try:
            await asyncio.sleep(60)
            await cleanup_stale_locks()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[CLEANUP] {e}")


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/webhook")
async def verify_webhook(request: Request):
    """Meta webhook verification — runs once during setup."""
    params    = dict(request.query_params)
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("[WEBHOOK] Verified by Meta")
        return PlainTextResponse(content=challenge)
    return PlainTextResponse(content="Forbidden", status_code=403)


@app.post("/webhook")
async def receive_message(request: Request):
    """
    Receives WhatsApp messages from Meta.
    Returns HTTP 200 immediately, processes in background.
    """
    if WEBHOOK_SECRET:
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        body_bytes = await request.body()
        expected   = "sha256=" + hmac.new(
            WEBHOOK_SECRET.encode(), body_bytes, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            print("[WEBHOOK] HMAC mismatch — rejected")
            return JSONResponse(content={"error": "invalid signature"}, status_code=403)
        data = json.loads(body_bytes)
    else:
        if APP_ENV == "production":
            print("[WEBHOOK] WARNING: WEBHOOK_SECRET not set")
        data = await request.json()

    async def _guarded():
        async with _pipeline_semaphore:
            incoming = await parse_webhook(data)
            if incoming:
                await run_pipeline(incoming)

    asyncio.create_task(_guarded())
    return JSONResponse(content={"status": "ok"})


@app.post("/chat")
async def chat_endpoint(payload: dict):
    """Streamlit/web testing interface — constructs IncomingMessage directly."""
    phone           = payload.get("phone")
    message         = payload.get("message")
    phone_number_id = payload.get("phone_number_id", os.getenv("DEFAULT_PHONE_NUMBER_ID", "1124766240726230"))
    sender_name     = payload.get("sender_name", "Test User")

    if not phone or not message:
        return JSONResponse(content={"error": "phone and message are required"}, status_code=400)

    incoming = IncomingMessage(
        trace_id        = f"trace_web_{uuid.uuid4().hex[:8]}",
        message_id      = f"msg_web_{uuid.uuid4().hex[:12]}",
        session_id      = phone,
        channel         = "web",
        timestamp       = int(time.time()),
        tenant_id       = "UNRESOLVED",
        waba_id         = "web_waba",
        phone_number_id = phone_number_id,
        biz_name        = os.getenv("DEFAULT_BIZ_NAME",           "Web Business"),
        region          = os.getenv("DEFAULT_REGION", "india"),
        timezone        = os.getenv("DEFAULT_TIMEZONE",           "Asia/Kolkata"),
        language        = "en",
        sender_name     = sender_name,
        sender_phone    = phone,
        text            = message,
        original_type   = "text",
        received_at     = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        raw             = payload,
    )

    try:
        return await run_pipeline(incoming)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            content={
                "replies": [{"type": "text", "body": f"⚠️ Pipeline error: {str(e)}"}],
                "debug": {"intent": "ERROR", "confidence": 0.0,
                          "latency": 0.0, "route": "Error Handler", "tenant_id": "None"},
            },
            status_code=500,
        )


@app.post("/reset")
async def reset_endpoint(payload: dict):
    """Clears all session data for a phone number — dev/testing use."""
    phone     = payload.get("phone")
    tenant_id = payload.get("tenant_id", os.getenv("DEFAULT_TENANT_ID",        "tenant_inventaa_led_001"))

    if not phone:
        return JSONResponse(content={"error": "phone is required"}, status_code=400)

    try:
        from db.session_store import _get_client
        client = _get_client()
        client.table("messages").delete().eq("tenant_id", tenant_id).eq("session_id", phone).execute()
        client.table("processing_locks").delete().eq("tenant_id", tenant_id).eq("session_id", phone).execute()
        client.table("workflow_sessions").delete().eq("tenant_id", tenant_id).eq("session_id", phone).execute()
        client.table("orders").delete().eq("tenant_id", tenant_id).eq("session_id", phone).execute()
        print(f"[RESET] Cleared session {phone} (tenant: {tenant_id})")
        return {"status": "ok", "message": f"Reset session {phone}"}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ══════════════════════════════════════════════════════════════════════════════
# CORE PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

async def _verify_prompt_keys_on_startup(tenant_id: str) -> None:
    """
    Item 2 (architect rec): fail fast if any required prompt key is missing from DB.
    Called once at startup — catches misconfigured tenants before first customer message.
    """
    from db.prompt_store import PROMPT_KEYS, _load_from_db
    missing = []
    for key in PROMPT_KEYS:
        if _load_from_db(tenant_id, key, "en") is None:
            missing.append(key)
    if missing:
        print(f"[STARTUP] ⚠️  Missing {len(missing)} prompt keys for tenant '{tenant_id}':")
        for k in missing:
            print(f"  - {k}")
        print("[STARTUP] Add these keys to prompt_templates table before serving traffic.")
    else:
        print(f"[STARTUP] ✅ All {len(PROMPT_KEYS)} prompt keys verified for '{tenant_id}'")


async def run_pipeline(incoming: IncomingMessage) -> dict:
    """
    Core 8-step pipeline. Lightweight orchestrator — no business logic here.
    All logic lives in pipeline/setup.py, pipeline/router.py, and ai/ modules.
    """
    from ai import request_profiler
    request_profiler.start()
    _t0 = time.monotonic()

    # ── Steps 1-5: Setup (tenant, dedup, lock, history, save, neg_state) ───
    # setup_pipeline now returns neg_state too — fetched in parallel with
    # save_message so we don't pay an extra DB round-trip here.
    ok, session_history, _neg = await setup_pipeline(incoming)
    if not ok:
        return _empty_result()

    try:
        # neg_state already loaded + cached on incoming by setup_pipeline
        # (parallel with save_message — no extra DB call needed here)

        # ── Step 3: Classify intent — with NEG BYPASS ───────────────────────
        # FIX: During active negotiation the intent classifier oscillates between
        # FAQ_KNOWLEDGE and WORKFLOW_ACTION wasting 2.5-3s per message.
        # Bypass saves that time and keeps intent consistent.
        #
        # Also bypasses while awaiting_quantity=True — the bot just asked one
        # specific question ("how many units?"), so the reply is overwhelmingly
        # the answer to that, not a topic change. This is deliberately narrow:
        # both conditions describe a single well-defined state the negotiation
        # state machine is waiting on (like awaiting_invoice_confirmation is
        # deliberately EXCLUDED below — that state is money-adjacent and kept
        # on full classification). It is NOT extended to the broader
        # PRODUCT_SELECTION browsing state (customer looking at a shown list,
        # no negotiation started yet) — that state is genuinely open-ended
        # (the customer might ask about installation, warranty, specs on any
        # item) and skipping classify_intent there would break the
        # requested_knowledge_field routing that "tell me the features/
        # installation/warranty of X" depends on.
        #
        # Trade-off: if a customer asks something unrelated to quantity while
        # awaiting_quantity=True (rare — the bot just asked a narrow
        # question), the routing decision that would normally catch that is
        # skipped. product_followup.py's own classifiers (offer-inquiry,
        # installation-intent, etc.) still run independently of routing and
        # can still catch it — this only loses the fast dynamic-knowledge
        # intercept shortcut, not the underlying handling.
        _t_intent = time.monotonic()
        _in_active_neg = (
            _neg is not None
            and not _neg.get("awaiting_invoice_confirmation", False)
            and (int(_neg.get("rounds", 0)) > 0 or _neg.get("awaiting_quantity", False))
        )
        if _in_active_neg:
            class _BypassResult:
                intent           = "WORKFLOW_ACTION"
                confidence_score = 0.97
                raw_text         = incoming.text
            result = _BypassResult()
            print(f"[INTENT ROUTER] '{incoming.text[:55]}' => WORKFLOW_ACTION (0.97) [NEG BYPASS]")
        else:
            result = await classify_intent(
                customer_message = incoming.text,
                session_history  = session_history,
                incoming         = incoming,
            )
        print(f"[TIMING] classify_intent: {time.monotonic() - _t_intent:.2f}s")
        print(f"[INTENT]   {result.intent}  confidence={result.confidence_score}")
        incoming._routing = getattr(result, "routing", None)

        # ── Step 4: Update intent in DB ──────────────────────────────────────
        # Fire-and-forget: this is an audit-log write nothing downstream reads,
        # so it shouldn't block the customer's reply waiting on it.
        asyncio.create_task(update_intent(incoming.message_id, result.intent, result.confidence_score, incoming.tenant_id))

        # ── Step 4.5: Build AIRequestContext ─────────────────────────────────
        from ai.request_context import AIRequestContext
        arc = AIRequestContext(
            incoming        = incoming,
            result          = result,
            session_history = session_history,
            neg_state       = _neg,
        )
        incoming._cached_arc = arc

        # ── Step 5: Dispatch to handler ──────────────────────────────────────
        _t_dispatch = time.monotonic()
        reply = await dispatch(incoming, result, session_history)
        request_profiler.add("dispatch", (time.monotonic() - _t_dispatch) * 1000)

        # ── Step 6: Send reply ───────────────────────────────────────────────
        sent_wamid = await _send_reply_chunked(incoming, reply)

        # ── Step 7: Store reply (DB) — parallel writes ──────────────────────
        if sent_wamid:
            replied_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            await asyncio.gather(
                update_reply(
                    incoming.message_id, reply, replied_at,
                    getattr(incoming, "_graphrag_raw", None),
                ),
                save_outbound_message(
                    tenant_id  = incoming.tenant_id,
                    session_id = incoming.session_id,
                    message_id = sent_wamid,
                    text       = reply,
                    region     = incoming.region,
                ),
            )

        # ── Step 8: Return debug info ────────────────────────────────────────
        latency = round(time.monotonic() - _t0, 2)
        _profile = request_profiler.snapshot()
        print(f"[TIMING] TOTAL pipeline time: {latency}s")
        print(f"[PROFILE] db={_profile.get('db_ms', 0)}ms ({_profile.get('db_calls', 0)} calls)  "
              f"llm={_profile.get('llm_ms', 0)}ms ({_profile.get('llm_calls', 0)} calls)  "
              f"graphrag={_profile.get('graphrag_ms', 0)}ms ({_profile.get('graphrag_calls', 0)} calls)  "
              f"dispatch={_profile.get('dispatch_ms', 0)}ms")
        # If the negotiator deferred (message wasn't negotiation-related —
        # e.g. an escalation request arriving mid-negotiation), the ACTUAL
        # intent used for routing differs from result.intent (which may
        # still be the NEG_BYPASS stub). Report the real one.
        _reported_intent = getattr(incoming, "_deferred_intent", None) or result.intent
        return {
            "replies": incoming.captured_replies,
            "debug": {
                "intent":     _reported_intent,
                "confidence": result.confidence_score,
                "latency":    latency,
                "route":      _get_route(result) if _reported_intent == result.intent
                              else _get_route_for_intent(_reported_intent),
                "tenant_id":  incoming.tenant_id,
                "profile":    _profile,
            },
        }

    finally:
        await release_lock(incoming.session_id, incoming.tenant_id)


async def _send_reply_chunked(incoming: IncomingMessage, reply: str) -> Optional[str]:
    """Sends reply, splitting into chunks if over WhatsApp's character limit."""
    if not reply:
        return None

    # Load character limit dynamically from DB with fallback
    try:
        from db.prompt_store import get_prompt
        limit_str = get_prompt(incoming, "whatsapp_max_message_limit")
        max_len = int(limit_str.strip())
    except Exception:
        max_len = 5000

    if len(reply) <= max_len:
        return await send_reply(incoming, reply)

    # 1. Split by predefined MSG_SPLIT tags if present
    if "⟨MSG_SPLIT⟩" in reply:
        chunks = [c.strip() for c in reply.split("⟨MSG_SPLIT⟩") if c.strip()]
    else:
        # 2. Split by line boundaries dynamically
        chunks = []
        lines = reply.split("\n")
        current_chunk = ""
        for line in lines:
            candidate = current_chunk + "\n" + line if current_chunk else line
            if len(candidate) > max_len:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                # If a single line exceeds max_len, split it by characters
                if len(line) > max_len:
                    temp_line = line
                    while len(temp_line) > max_len:
                        chunks.append(temp_line[:max_len])
                        temp_line = temp_line[max_len:]
                    current_chunk = temp_line
                else:
                    current_chunk = line
            else:
                current_chunk = candidate
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

    last_wamid = None
    for chunk in chunks:
        wamid = await send_reply(incoming, chunk)
        if wamid:
            last_wamid = wamid
    return last_wamid


def _get_route(result) -> str:
    """Returns a human-readable route label for debug output."""
    from ai.handlers import DEFAULT_INTENT_MIN_CONFIDENCE
    if result.intent == "GREETING":
        return "Greeting Handler"
    if result.intent == "HUMAN_ESCALATION":
        return "Human Handoff Escalation"
    _graphrag_intents = ("FAQ_KNOWLEDGE", "WORKFLOW_ACTION", "FIND_PRODUCT", "BROWSE_CATEGORY", "GET_PRODUCT_INFO", "GET_ADVICE", "CHECK_POLICY")
    if result.intent in _graphrag_intents or result.confidence_score < DEFAULT_INTENT_MIN_CONFIDENCE:
        return "GraphRAG / Catalog"
    return "Unknown Intent Handler"


def _get_route_for_intent(intent: str) -> str:
    """Same mapping as _get_route(), for the deferred-intent case where we
    only have the intent string, not a full IntentResult with a confidence score."""
    if intent == "GREETING":
        return "Greeting Handler"
    if intent == "HUMAN_ESCALATION":
        return "Human Handoff Escalation"
    return "GraphRAG / Catalog"


def _empty_result() -> dict:
    return {
        "replies": [],
        "debug": {"intent": "SKIPPED", "confidence": 0.0,
                  "latency": 0.0, "route": "Skipped", "tenant_id": "UNKNOWN"},
    }


# ── Module imports at bottom — avoids circular import issues ──────────────────
from ai.graphrag_handler import call_graphrag_api, _send_structured_product_list, _coerce_pythonic_dict
from ai.product_followup import _try_resolve_product_followup, _parse_followup_message