# pipeline/setup.py — Pipeline Setup Steps 1-5
#
# Loads ALL prompt columns from tenants table into incoming object.
# No defaults, no fallbacks — every prompt must be set in DB.

from models.schemas import IncomingMessage
from db.session_store import (
    resolve_tenant_id, is_duplicate, get_session_history, save_message,
)
from db.processing_lock import acquire_lock
from db.memory_store import get_relevant_context

# Every prompt column that must exist in tenants table
PROMPT_COLUMNS = [
    "intent_system_prompt",
    "greeting_system_prompt",
    "entity_system_prompt",
    "escalation_prompt",
    "unknown_prompt",
    "invoice_inquiry_prompt",
    "invoice_confirm_prompt",
    "invoice_order_confirm_prompt",
    "invoice_confirmation_prompt",
    "neg_is_request_prompt",
    "neg_extract_qty_prompt",
    "neg_detect_counter_prompt",
    "neg_more_discount_prompt",
    "neg_detect_accept_prompt",
    "neg_detect_qty_change_prompt",
    "neg_no_discount_prompt",
    "neg_first_offer_prompt",
    "neg_counter_offer_prompt",
    "neg_final_price_prompt",
    "fast_confirm_prompt",
    "product_summary_recommendation_prompt",
    "acceptance_keywords",
    "acceptance_exact_words",
    "new_order_trigger_phrases",
]


async def setup_pipeline(incoming: IncomingMessage) -> tuple:
    """
    Returns (True, session_history) to continue, or (False, None) to abort.

    LOCK OWNERSHIP INVARIANT (important for callers):
        If this returns (True, ...), the caller now owns the processing lock
        for incoming.session_id/tenant_id and is responsible for releasing it.
        If this returns (False, ...) OR raises, the lock is NOT held by the
        caller — either it was never acquired (unknown tenant, duplicate
        message, or another worker already holds it), or it WAS acquired
        internally but an error occurred before returning, in which case
        this function releases it itself before propagating. Callers must
        never call release_lock() after a (False, ...) return or after an
        exception from this function — doing so can delete a DIFFERENT,
        currently-active request's lock, since processing_locks is keyed
        only by session_id.
    """
    tenant_info = await resolve_tenant_id(incoming.phone_number_id)
    if tenant_info is None:
        print(f"[PIPELINE] Unknown phone_number_id={incoming.phone_number_id} — skipping")
        return False, None

    _apply_tenant(incoming, tenant_info)
    _resolve_media_unsupported_marker(incoming)
    _log_incoming(incoming)

    await _resolve_quoted_caption(incoming)

    if await is_duplicate(incoming.message_id, incoming.tenant_id):
        print(f"[PIPELINE] Duplicate — skipping {incoming.message_id}")
        return False, None

    if not await acquire_lock(incoming.session_id, incoming.tenant_id):
        print(f"[PIPELINE] Already processing {incoming.session_id} — skipping")
        return False, None

    # From this point on, THIS request holds the lock. Anything that fails
    # below must release it before returning/raising — otherwise it leaks
    # until cleanup_stale_locks() catches it (up to ~2 minutes later).
    try:
        session_history = await _get_history(incoming)
        await save_message(incoming)
        return True, session_history
    except Exception:
        from db.processing_lock import release_lock as _release_lock
        try:
            await _release_lock(incoming.session_id, incoming.tenant_id)
            print(f"[PIPELINE] Released lock after setup failure for {incoming.session_id}")
        except Exception as release_err:
            print(f"[PIPELINE] Failed to release lock after setup failure: {release_err}")
        raise


def _apply_tenant(incoming: IncomingMessage, info: dict) -> None:
    """Copies every tenant DB column onto the incoming object."""
    incoming.tenant_id    = info["tenant_id"]
    incoming.biz_name     = info.get("biz_name")      or incoming.biz_name
    incoming.timezone     = info.get("timezone")      or incoming.timezone
    incoming.region       = info.get("region")        or incoming.region
    incoming.language     = info.get("language")      or incoming.language
    incoming.tagline      = info.get("tagline")
    incoming.city         = info.get("city")
    incoming.support_email= info.get("support_email")
    incoming.support_phone= info.get("support_phone")
    incoming.website      = info.get("website")
    incoming.upi_id       = info.get("upi_id")
    incoming.account_name = info.get("account_name")
    incoming.gstin        = info.get("gstin")
    incoming.state_code   = info.get("state_code")
    incoming.gst_rate     = float(info.get("gst_rate") or 18) / 100
    incoming.max_negotiation_rounds = int(info["max_negotiation_rounds"]) if info.get("max_negotiation_rounds") else None
    incoming.neg_floor_disc_pct     = int(info["neg_floor_disc_pct"]) if info.get("neg_floor_disc_pct") else None
    incoming.neg_floor_multiplier   = float(info["neg_floor_multiplier"]) if info.get("neg_floor_multiplier") else None
    incoming.intent_min_confidence  = float(info["intent_min_confidence"]) if info.get("intent_min_confidence") else None
    incoming.require_offer_disclosure = bool(info["require_offer_disclosure"]) if info.get("require_offer_disclosure") is not None else False
    incoming.max_image_products     = int(info["max_image_products"]) if info.get("max_image_products") else None
    incoming.valid_intents    = info.get("valid_intents") or None
    incoming.graphrag_api_url = info.get("graphrag_api_url") or None
    incoming.products_api_url = info.get("products_api_url") or None
    incoming.access_token     = info.get("access_token") or None

    # Load every prompt column — None if not set in DB
    for col in PROMPT_COLUMNS:
        setattr(incoming, col, info.get(col) or None)

    # Log which prompts are missing (warning only — RuntimeError happens at use time)
    missing = [col for col in PROMPT_COLUMNS if not getattr(incoming, col, None)]
    if missing:
        print(f"[PIPELINE] ⚠️ Missing prompts for {incoming.tenant_id}: {missing}")
        print(f"[PIPELINE] Run migrations/003_all_prompts_dynamic.sql to fix.")


def _resolve_media_unsupported_marker(incoming: IncomingMessage) -> None:
    """
    whatsapp_adapter.py sets incoming.text to an internal marker for image/
    audio messages (OCR/STT aren't implemented yet) because tenant_id isn't
    resolved at parse time. Now that it is, render the tenant's own
    media_unsupported_prompt so the actual customer-facing wording is
    DB content — never a hardcoded English string baked into the adapter.
    """
    marker_to_media_type = {
        "__MEDIA_UNSUPPORTED_IMAGE__": "image",
        "__MEDIA_UNSUPPORTED_AUDIO__": "audio",
    }
    media_type = marker_to_media_type.get(incoming.text)
    if not media_type:
        return
    try:
        from db.prompt_store import get_prompt
        incoming.text = get_prompt(
            incoming, "media_unsupported_prompt",
            media_type=media_type, sender_name=incoming.sender_name,
        )
    except RuntimeError as e:
        # Prompt not seeded for this tenant yet — fall back to a neutral,
        # non-branded placeholder rather than crash the pipeline.
        print(f"[PIPELINE] media_unsupported_prompt missing for {incoming.tenant_id}: {e}")
        incoming.text = f"[{media_type.capitalize()} received — not yet supported]"


def _log_incoming(incoming: IncomingMessage) -> None:
    masked = f"...{incoming.sender_phone[-4:]}" if incoming.sender_phone else "unknown"
    name   = incoming.sender_name.split()[0] if incoming.sender_name else "unknown"
    print(f"\n{'-'*60}")
    print(f"[{incoming.trace_id}] {name} ({masked})")
    print(f"[TENANT]   {incoming.tenant_id}")
    print(f"[MESSAGE]  {incoming.text}")


async def _resolve_quoted_caption(incoming: IncomingMessage) -> None:
    if not incoming.quoted_message_id:
        return
    try:
        from db.session_store import get_reply_by_message_id
        quoted = await get_reply_by_message_id(incoming.tenant_id, incoming.quoted_message_id)
        if quoted:
            preview = quoted.strip()[:200]
            incoming.quoted_caption = preview
            incoming.text = f"[Quoting: {preview}]\n{incoming.text}"
            print(f"[ADAPTER] Quoted caption resolved")
    except Exception as e:
        print(f"[ADAPTER] Quoted message lookup failed: {e}")


async def _get_history(incoming: IncomingMessage) -> list:
    mem0 = await get_relevant_context(
        incoming.tenant_id, incoming.session_id, incoming.text, limit=6,
    )
    if mem0:
        print(f"[DB] Context from Mem0 — {len(mem0)} memories")
        return mem0
    pg = await get_session_history(incoming.tenant_id, incoming.session_id, limit=10)
    print(f"[DB] History from Postgres — {len(pg)} turns")
    return pg