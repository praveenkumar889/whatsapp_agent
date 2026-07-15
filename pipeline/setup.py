# pipeline/setup.py — Pipeline Setup Steps 1-5
#
# Loads ALL prompt columns from tenants table into incoming object.
# No defaults, no fallbacks — every prompt must be set in DB.

import asyncio

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
    "parse_global_offer_tiers_prompt",
    "invoice_inquiry_check_prompt",
    "order_confirmation_reply_check_prompt",
    "generate_invoice_cta_prompt",
    "invoice_confirmation_request_check_prompt",
    "fast_order_confirm_check_prompt",
    "category_matcher_prompt",
    "pf_data_extraction_prompt",
    "pf_history_resolver_prompt",

    "pf_offer_inquiry_check_prompt",
    "pf_neg_product_change_check_prompt",
    "pf_vague_reference_check_prompt",
    "pf_vague_reference_rewriter_prompt",
    "pf_offer_inquiry_check_l2_prompt",
    "pf_named_product_extractor_prompt",
    "pf_offers_formatter_prompt",
    "pf_vague_pronoun_resolver_l2_prompt",
    "pf_comparison_prompt",
    "pf_image_installation_intent_prompt",
    "pf_main_followup_prompt",
    "pf_new_search_followup_classifier_prompt",
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

    # is_duplicate and acquire_lock are independent — run them concurrently.
    dup, locked = await asyncio.gather(
        is_duplicate(incoming.message_id, incoming.tenant_id),
        acquire_lock(incoming.session_id, incoming.tenant_id),
    )

    if dup:
        print(f"[PIPELINE] Duplicate — skipping {incoming.message_id}")
        return False, None

    if not locked:
        print(f"[PIPELINE] Already processing {incoming.session_id} — skipping")
        return False, None

    return True, None


async def get_history(incoming: IncomingMessage) -> list:
    return await _get_history(incoming)


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
    # Postgres history (last N turns, in order) is the fast, complete source of truth
    # for conversation context. Mem0 semantic search is slower (~1-2s) and returns
    # out-of-order recall that hurts dialogue coherence — it's only used as a fallback
    # when Postgres has no history. Product follow-up resolution does its own Mem0
    # lookup separately, so this change does not affect follow-up accuracy.
    pg = await get_session_history(incoming.tenant_id, incoming.session_id, limit=10)
    if pg:
        print(f"[DB] History from Postgres — {len(pg)} turns")
        return pg
    mem0 = await get_relevant_context(
        incoming.tenant_id, incoming.session_id, incoming.text, limit=6,
    )
    print(f"[DB] Context from Mem0 (Postgres empty) — {len(mem0 or [])} memories")
    return mem0 or []