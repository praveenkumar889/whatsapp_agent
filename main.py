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

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse

from config import VERIFY_TOKEN, WEBHOOK_SECRET, APP_ENV, AZURE_OPENAI_DEPLOYMENT
from adapter.whatsapp_adapter import parse_webhook
from models.schemas import IncomingMessage
from ai.handlers import update_dialogue_state
from db.session_store import update_intent, update_reply, save_outbound_message
from db.processing_lock import release_lock, cleanup_stale_locks
from db.memory_store import add_conversation_turn
from pipeline.setup import setup_pipeline
from pipeline.router import dispatch
from messaging import send_reply
from ai.mcp_client import get_taxonomy_hints_mcp

# ── Concurrency guard — max 50 simultaneous pipeline runs ─────────────────────
_pipeline_semaphore = asyncio.Semaphore(50)


# ══════════════════════════════════════════════════════════════════════════════
# LIFESPAN (replaces deprecated @app.on_event)
# ══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    cleanup_task = asyncio.create_task(_periodic_lock_cleanup())
    print("[STARTUP] Periodic lock cleanup task started")

    yield  # Application runs here

    # ── Shutdown ─────────────────────────────────────────────────────────────
    cleanup_task.cancel()
    print("[SHUTDOWN] Periodic lock cleanup task cancelled")


app = FastAPI(title="WhatsApp AI Agent", lifespan=lifespan)


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
        tenant_id       = "tenant_inventaa_led_001",
        waba_id         = "web_waba",
        phone_number_id = phone_number_id,
        biz_name        = os.getenv("DEFAULT_BIZ_NAME",           "Web Business"),
        region          = "india",
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

async def run_pipeline(incoming: IncomingMessage) -> dict:
    """
    Core 8-step pipeline. Lightweight orchestrator — no business logic here.
    All logic lives in pipeline/setup.py, pipeline/router.py, and ai/ modules.
    """
    _t0 = time.monotonic()

    # ── Steps 1-2: Setup (tenant, dedup, lock) ──────────────────────────────
    ok, _ = await setup_pipeline(incoming)
    if not ok:
        return _empty_result()

    try:
        # ── Step 2.5: Parallelize History, Message Save, State Load & MCP Taxonomy ────
        from pipeline.setup import get_history as _gh, save_message as _sm
        from db.session_store import get_dialogue_state as _gds

        _txt_lower = incoming.text.strip().lower()
        _skip_taxonomy = (
            not _txt_lower.isdigit()
            and _txt_lower in {
                "hi", "hello", "hey", "hii", "hiii", "thanks",
                "thank you", "thankyou", "bye", "good morning",
                "good afternoon", "good evening", "👋", "🙏", "👍",
            }
        )
        incoming._skip_taxonomy = _skip_taxonomy

        _t_prep = time.monotonic()
        if _skip_taxonomy:
            print(f"[TAXONOMY] Skipping MCP taxonomy for trivial message: '{_txt_lower}'")
            session_history, _, incoming._cached_state = await asyncio.gather(
                _gh(incoming),
                _sm(incoming),
                _gds(incoming.tenant_id, incoming.session_id),
            )
            taxonomy_hints = {}
        else:
            session_history, _, incoming._cached_state, taxonomy_hints = await asyncio.gather(
                _gh(incoming),
                _sm(incoming),
                _gds(incoming.tenant_id, incoming.session_id),
                get_taxonomy_hints_mcp(incoming.text),
            )
        print(f"[TIMING] Parallel setup (history/save/state/taxonomy): {time.monotonic() - _t_prep:.2f}s")

        # ── Step 3: Update Dialogue State ───────────────────────
        # Fast-path: skip LLM intent classification for obvious greetings
        if _skip_taxonomy:
            from models.schemas import DialogueState
            state = DialogueState(
                followup='no', negotiation_status='none',
                category='', product_name='', product_skus=[], intent='GREETING',
            )
            print(f"[TIMING] update_dialogue_state: 0.00s (fast-path GREETING)")
        else:
            _t_intent = time.monotonic()
            state = await update_dialogue_state(
                customer_message = incoming.text,
                previous_state   = incoming._cached_state,
                session_history  = session_history,
                taxonomy_hints   = taxonomy_hints,
                incoming         = incoming,
            )
            print(f"[TIMING] update_dialogue_state: {time.monotonic() - _t_intent:.2f}s")
        print(f"[STATE]   {state.intent} | {state.product_name}")

        from db.session_store import save_dialogue_state as _sds
        asyncio.create_task(_sds(incoming.tenant_id, incoming.session_id, state.__dict__))
        asyncio.create_task(update_intent(incoming.message_id, state.intent, 1.0, incoming.tenant_id))

        # ── Step 5: Dispatch to handler ──────────────────────────────────────
        reply = await dispatch(incoming, state, session_history)

        # ── Step 6: Send reply ───────────────────────────────────────────────
        sent_wamid = await _send_reply_chunked(incoming, reply)

        # ── Step 7: Store reply (Mem0 + DB) in background tasks ─────────────
        if sent_wamid:
            replied_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            asyncio.create_task(update_reply(
                incoming.message_id, reply, replied_at,
                getattr(incoming, "_graphrag_raw", None),
            ))
            asyncio.create_task(save_outbound_message(
                tenant_id  = incoming.tenant_id,
                session_id = incoming.session_id,
                message_id = sent_wamid,
                text       = reply,
                region     = incoming.region,
            ))
            # Step 10: Fire-and-forget background tasks — never block response.
            # 1. Save raw turn to Mem0 (conversation memory)
            asyncio.create_task(_save_mem0_turn(
                tenant_id  = incoming.tenant_id,
                session_id = incoming.session_id,
                user_text  = incoming.text,
                bot_reply  = reply,
            ))
            # 2. If invoice was just generated, trigger conversation summary
            if "invoice" in reply.lower() or "INV#" in reply:
                asyncio.create_task(_save_conversation_summary(
                    tenant_id       = incoming.tenant_id,
                    session_id      = incoming.session_id,
                    session_history = session_history,
                    incoming        = incoming,
                ))

        # ── Step 8: Return debug info ────────────────────────────────────────
        latency = round(time.monotonic() - _t0, 2)
        print(f"[TIMING] TOTAL pipeline time: {latency}s")
        return {
            "replies": incoming.captured_replies,
            "debug": {
                "intent":     state.intent,
                "confidence": 1.0,
                "latency":    latency,
                "route":      _get_route(state),
                "tenant_id":  incoming.tenant_id,
            },
        }

    finally:
        await release_lock(incoming.session_id, incoming.tenant_id)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def _save_mem0_turn(tenant_id: str, session_id: str,
                           user_text: str, bot_reply: str) -> None:
    """
    Fire-and-forget Mem0 conversation turn save.
    Runs after HTTP response sent — never blocks the pipeline.
    """
    try:
        await add_conversation_turn(
            tenant_id  = tenant_id,
            session_id = session_id,
            user_text  = user_text,
            bot_reply  = bot_reply,
        )
    except Exception as e:
        print(f"[MEM0] Background save failed (non-critical): {e}")


async def _save_conversation_summary(
    tenant_id: str, session_id: str,
    session_history: list, incoming,
) -> None:
    """
    Step 5 + Step 10: Generate and save semantic conversation summary to Mem0.
    Triggered after invoice generation — captures the full conversation lifecycle.
    Fire-and-forget — never blocks response.
    """
    try:
        from ai.summary_generator import generate_and_save_conversation_summary
        await generate_and_save_conversation_summary(
            tenant_id  = tenant_id,
            session_id = session_id,
            messages   = session_history,
            incoming   = incoming,
        )
    except Exception as e:
        print(f"[MEM0] Conversation summary failed (non-critical): {e}")


async def _send_reply_chunked(incoming: IncomingMessage, reply: str) -> Optional[str]:
    """Sends reply, splitting into chunks if over WhatsApp's 4096 char limit."""
    if not reply:
        return None

    MAX_LEN = 4000
    if len(reply) <= MAX_LEN:
        return await send_reply(incoming, reply)

    chunks = [reply[i:i+MAX_LEN] for i in range(0, len(reply), MAX_LEN)]
    last_wamid = None
    for chunk in chunks:
        wamid = await send_reply(incoming, chunk)
        if wamid:
            last_wamid = wamid
    return last_wamid


def _get_route(state) -> str:
    """Returns a human-readable route label for debug output."""
    if state.intent == "GREETING":
        return "Greeting Handler"
    if state.intent == "HUMAN_ESCALATION":
        return "Human Handoff Escalation"
    return f"GraphRAG / Catalog ({state.intent})"


def _empty_result() -> dict:
    return {
        "replies": [],
        "debug": {"intent": "SKIPPED", "confidence": 0.0,
                  "latency": 0.0, "route": "Skipped", "tenant_id": "UNKNOWN"},
    }


# ── Module imports at bottom — avoids circular import issues ──────────────────
from ai.graphrag_handler import call_graphrag_api, _send_structured_product_list, _coerce_pythonic_dict
from ai.product_followup import _try_resolve_product_followup, _parse_followup_message