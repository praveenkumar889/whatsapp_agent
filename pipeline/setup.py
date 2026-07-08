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
]


async def setup_pipeline(incoming: IncomingMessage) -> tuple:
    """
    Returns (True, session_history) to continue, or (False, None) to abort.
    """
    tenant_info = await resolve_tenant_id(incoming.phone_number_id)
    if tenant_info is None:
        print(f"[PIPELINE] Unknown phone_number_id={incoming.phone_number_id} — skipping")
        return False, None

    _apply_tenant(incoming, tenant_info)
    _log_incoming(incoming)

    await _resolve_quoted_caption(incoming)

    if await is_duplicate(incoming.message_id, incoming.tenant_id):
        print(f"[PIPELINE] Duplicate — skipping {incoming.message_id}")
        return False, None

    if not await acquire_lock(incoming.session_id, incoming.tenant_id):
        print(f"[PIPELINE] Already processing {incoming.session_id} — skipping")
        return False, None

    session_history = await _get_history(incoming)
    await save_message(incoming)
    return True, session_history


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