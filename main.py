import os
# main.py — FastAPI Application Entry Point

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
from ai.handlers import classify_intent
from db.session_store import update_intent, update_reply, save_outbound_message
from db.processing_lock import release_lock, cleanup_stale_locks
from db.memory_store import add_conversation_turn
from pipeline.setup import setup_pipeline
from pipeline.router import dispatch
from messaging import send_reply
from utils.alerting import alert_pipeline_error

_pipeline_semaphore = asyncio.Semaphore(50)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = asyncio.create_task(_periodic_lock_cleanup())
    print("[STARTUP] Periodic lock cleanup task started")
    yield
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


@app.get("/webhook")
async def verify_webhook(request: Request):
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
        try:
            async with _pipeline_semaphore:
                incoming = await parse_webhook(data)
                if incoming:
                    await run_pipeline(incoming)
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                await alert_pipeline_error(e, locals().get("incoming"))
            except Exception:
                pass

    asyncio.create_task(_guarded())
    return JSONResponse(content={"status": "ok"})


@app.post("/chat")
async def chat_endpoint(payload: dict):
    """Streamlit/web testing interface — constructs IncomingMessage directly."""
    phone           = payload.get("phone")
    message         = payload.get("message")
    phone_number_id = payload.get("phone_number_id") or os.getenv("DEFAULT_PHONE_NUMBER_ID")
    sender_name     = payload.get("sender_name", "Test User")

    if not phone or not message:
        return JSONResponse(content={"error": "phone and message are required"}, status_code=400)
    if not phone_number_id:
        return JSONResponse(
            content={"error": "phone_number_id not provided and DEFAULT_PHONE_NUMBER_ID not set in .env — "
                               "refusing to guess a tenant's phone_number_id"},
            status_code=400,
        )

    incoming = IncomingMessage(
        trace_id        = f"trace_web_{uuid.uuid4().hex[:8]}",
        message_id      = f"msg_web_{uuid.uuid4().hex[:12]}",
        session_id      = phone,
        channel         = "web",
        timestamp       = int(time.time()),
        tenant_id       = "UNRESOLVED",
        waba_id         = "web_waba",
        phone_number_id = phone_number_id,
        biz_name        = os.getenv("DEFAULT_BIZ_NAME", "Business"),  # neutral — overwritten by tenant resolution
        region          = "unresolved",  # overwritten by tenant resolution in setup.py
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
        traceback.print_exc()  # full detail stays server-side only
        return JSONResponse(
            content={
                "replies": [{"type": "text", "body":
                    "Sorry, something went wrong on our end. Please try again in a moment, "
                    "or contact support if this keeps happening."}],
                "debug": {"intent": "ERROR", "confidence": 0.0,
                          "latency": 0.0, "route": "Error Handler", "tenant_id": "None"},
            },
            status_code=500,
        )


@app.post("/reset")
async def reset_endpoint(payload: dict):
    """Clears all session data for a phone number — dev/testing use."""
    phone     = payload.get("phone")
    tenant_id = payload.get("tenant_id") or os.getenv("DEFAULT_TENANT_ID")

    if not phone:
        return JSONResponse(content={"error": "phone is required"}, status_code=400)
    if not tenant_id:
        # SAFETY: this endpoint deletes data. Never guess a real tenant_id —
        # require it explicitly (or DEFAULT_TENANT_ID set in .env for local
        # dev convenience only).
        return JSONResponse(
            content={"error": "tenant_id is required (or set DEFAULT_TENANT_ID in .env for local dev) — "
                               "refusing to guess which tenant's data to delete"},
            status_code=400,
        )

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


def _get_safe_error_reply(incoming: IncomingMessage) -> str:
    sender_name = getattr(incoming, "sender_name", None) or "there"
    try:
        from db.prompt_store import get_prompt
        return get_prompt(
            incoming, "pipeline_error_reply",
            sender_name = sender_name,
            biz_name    = getattr(incoming, "biz_name", None) or "our team",
        )
    except Exception:
        return (
            f"Sorry {sender_name}, something went wrong on our end. 🙏 "
            f"Our team has been notified and is looking into it. "
            f"Please try again in a few minutes."
        )


async def run_pipeline(incoming: IncomingMessage) -> dict:
    _t0 = time.monotonic()
    _lock_owned_by_this_request = False

    try:
        ok, session_history = await setup_pipeline(incoming)
        if not ok:
            # setup_pipeline() guarantees: a (False, ...) return means this
            # request never held the lock (unknown tenant, duplicate message,
            # or another worker already holds it) — nothing to release.
            return _empty_result()
        # setup_pipeline() succeeded: this request now genuinely owns the
        # processing lock and is responsible for releasing it below.
        _lock_owned_by_this_request = True

        from db.session_store import get_negotiation_state as _gns
        incoming._cached_neg_state = await _gns(incoming.tenant_id, incoming.session_id)
        _neg = incoming._cached_neg_state

        _t_intent = time.monotonic()
        _in_active_neg = (
            _neg is not None
            and int(_neg.get("rounds", 0)) > 0
            and not _neg.get("awaiting_invoice_confirmation", False)
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

        await update_intent(incoming.message_id, result.intent, result.confidence_score, incoming.tenant_id)

        reply = await dispatch(incoming, result, session_history)

        sent_wamid = await _send_reply_chunked(incoming, reply)

        if sent_wamid:
            replied_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            await update_reply(
                incoming.message_id, reply, replied_at,
                getattr(incoming, "_graphrag_raw", None),
            )
            await save_outbound_message(
                tenant_id  = incoming.tenant_id,
                session_id = incoming.session_id,
                message_id = sent_wamid,
                text       = reply,
                region     = incoming.region,
            )
            asyncio.create_task(_save_mem0_turn(
                tenant_id  = incoming.tenant_id,
                session_id = incoming.session_id,
                user_text  = incoming.text,
                bot_reply  = reply,
            ))
            if "invoice" in reply.lower() or "INV#" in reply:
                asyncio.create_task(_save_conversation_summary(
                    tenant_id       = incoming.tenant_id,
                    session_id      = incoming.session_id,
                    session_history = session_history,
                    incoming        = incoming,
                ))

        latency = round(time.monotonic() - _t0, 2)
        print(f"[TIMING] TOTAL pipeline time: {latency}s")
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
            },
        }

    except Exception as e:
        import traceback
        traceback.print_exc()

        try:
            await alert_pipeline_error(e, incoming)
        except Exception as alert_err:
            print(f"[SAFETY NET] alert_pipeline_error itself failed: {alert_err}")

        try:
            safe_reply = _get_safe_error_reply(incoming)
            await send_reply(incoming, safe_reply)
        except Exception as send_err:
            print(f"[SAFETY NET] Failed to send safe reply: {send_err}")

        latency = round(time.monotonic() - _t0, 2)
        print(f"[SAFETY NET] Pipeline error handled in {latency}s: {e}")
        return {
            "replies": incoming.captured_replies,
            "debug": {
                "intent":     "ERROR",
                "confidence": 0.0,
                "latency":    latency,
                "route":      "Safety Net",
                "tenant_id":  getattr(incoming, "tenant_id", "UNKNOWN"),
                "error":      str(e),
            },
        }

    finally:
        # Only release if THIS request actually acquired the lock (see
        # setup_pipeline()'s ownership invariant). Releasing unconditionally
        # whenever tenant_id was resolved — the previous logic — would also
        # fire for "duplicate message" and "lock held by another worker",
        # neither of which means this request holds the lock; the second
        # case would actively delete a different, currently-active
        # request's lock (processing_locks is keyed by session_id alone).
        if _lock_owned_by_this_request:
            tenant_id = getattr(incoming, "tenant_id", None)
            if tenant_id and tenant_id != "UNRESOLVED":
                await release_lock(incoming.session_id, tenant_id)


async def _save_mem0_turn(tenant_id: str, session_id: str,
                           user_text: str, bot_reply: str) -> None:
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


def _get_route(result) -> str:
    from ai.handlers import DEFAULT_INTENT_MIN_CONFIDENCE
    if result.intent == "GREETING":
        return "Greeting Handler"
    if result.intent == "HUMAN_ESCALATION":
        return "Human Handoff Escalation"
    if result.intent in ("FAQ_KNOWLEDGE", "WORKFLOW_ACTION") or result.confidence_score < DEFAULT_INTENT_MIN_CONFIDENCE:
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


from ai.graphrag_handler import call_graphrag_api, _send_structured_product_list, _coerce_pythonic_dict
from ai.product_followup import _try_resolve_product_followup, _parse_followup_message