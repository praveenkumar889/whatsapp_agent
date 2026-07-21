# db/prompt_store.py — PromptStore
#
# ARCHITECTURE (3 separated responsibilities):
#
#   _PromptLoader.load()     — finds prompt text
#     1. In-memory cache (5-min TTL)
#     2. prompt_templates table (primary source — all 40+ prompts)
#     3. IncomingMessage attribute (LEGACY — tenants columns, deprecating)
#     4. Returns None → caller raises RuntimeError
#
#   _PromptRenderer.render() — substitutes {variables} using str.replace()
#     Never format_map(): prompts contain literal JSON {"type":"X"} that
#     format_map() crashes on. str.replace() only touches explicit kwargs.
#
#   get_prompt()             — public API: load + render
#
# DEPRECATION PATH:
#   Once all tenants use prompt_templates, IncomingMessage only needs
#   tenant_id + language (no prompt text). Remove step 3 + PROMPT_KEYS then.

from __future__ import annotations
import re
import time
from typing import Any, Optional

# ── Cache ──────────────────────────────────────────────────────────────────────
_CACHE: dict = {}
_CACHE_TTL   = 300   # 5 minutes

def _cache_key(tenant_id: str, name: str, lang: str) -> str:
    return f"{tenant_id}::{name}::{lang}"

def _cache_get(tenant_id: str, name: str, lang: str) -> Optional[str]:
    e = _CACHE.get(_cache_key(tenant_id, name, lang))
    return e[0] if e and e[1] > time.monotonic() else None

def _cache_set(tenant_id: str, name: str, lang: str, text: str) -> None:
    _CACHE[_cache_key(tenant_id, name, lang)] = (text, time.monotonic() + _CACHE_TTL)

def _cache_invalidate(tenant_id: str, name: str, lang: str) -> None:
    _CACHE.pop(_cache_key(tenant_id, name, lang), None)


# ── Loader ─────────────────────────────────────────────────────────────────────

from dataclasses import dataclass

@dataclass
class PromptTemplate:
    """
    Rich result from PromptLoader — carries metadata alongside the template text.
    Enables structured logging (source, version, language) and monitoring.
    PromptRenderer only uses .template; callers can inspect .source for debugging.
    """
    name:     str
    template: str
    source:   str    # "CACHE" | "DB" | "LEGACY"
    version:  int    = 0
    language: str    = "en"
    tenant_id:str    = ""


class _PromptLoader:
    """Finds the prompt — separated from rendering. Returns PromptTemplate."""

    @staticmethod
    def load(tenant_id: str, name: str, lang: str,
             incoming: Any = None) -> Optional[PromptTemplate]:
        """
        Resolution order:
          1. CACHE   — in-memory, 5-min TTL, fastest
          2. DB      — prompt_templates table, primary source for all 40+ prompts
          3. LEGACY  — IncomingMessage attribute (tenants columns, deprecating)
          Returns None only if all three miss.
        """
        # 1. Cache
        cached = _cache_get(tenant_id, name, lang)
        if cached:
            print(f"[PROMPT] source=CACHE  tenant={tenant_id}  prompt={name}  lang={lang}")
            return PromptTemplate(name=name, template=cached, source="CACHE",
                                  language=lang, tenant_id=tenant_id)

        # 2. DB — prompt_templates table
        db_result = _PromptLoader._from_db(tenant_id, name, lang)
        if db_result:
            text, version = db_result
            _cache_set(tenant_id, name, lang, text)
            print(f"[PROMPT] source=DB     tenant={tenant_id}  prompt={name}"
                  f"  lang={lang}  version={version}")
            return PromptTemplate(name=name, template=text, source="DB",
                                  version=version, language=lang, tenant_id=tenant_id)

        # 3. LEGACY — IncomingMessage attribute (tenants table columns)
        #    Only old prompt names that pipeline/setup.py loads exist here.
        #    New prompts (pf_*, invoice_*_check_*, etc.) won't be here.
        #    DEPRECATING: remove once all tenants use prompt_templates fully.
        if incoming is not None:
            attr   = PROMPT_KEYS.get(name, name)
            legacy = getattr(incoming, attr, None)
            if legacy:
                _cache_set(tenant_id, name, lang, legacy)
                print(f"[PROMPT] source=LEGACY tenant={tenant_id}  prompt={name}"
                      f"  (migrate to prompt_templates)")
                return PromptTemplate(name=name, template=legacy, source="LEGACY",
                                      language=lang, tenant_id=tenant_id)

        return None

    @staticmethod
    def _from_db(tenant_id: str, name: str, lang: str) -> Optional[tuple]:
        """Returns (prompt_text, version) or None."""
        import time
        max_retries = 3
        retry_delay = 0.1  # seconds
        for attempt in range(max_retries):
            try:
                from db.session_store import _get_client
                result = (
                    _get_client()
                    .table("prompt_templates")
                    .select("prompt_text, version")
                    .eq("tenant_id",   tenant_id)
                    .eq("prompt_name", name)
                    .eq("language",    lang)
                    .eq("status",      "active")
                    .order("version",  desc=True)
                    .limit(1)
                    .execute()
                )
                if result.data:
                    from typing import cast
                    row = cast(dict, result.data[0])
                    return row["prompt_text"], int(row.get("version", 1))
                return None
            except Exception as e:
                print(f"[PROMPT] DB read attempt {attempt + 1} failed for '{name}': {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    return None

    @staticmethod
    async def aload(tenant_id: str, name: str, lang: str,
                     incoming: Any = None) -> Optional[PromptTemplate]:
        """
        Async variant of load() — identical resolution order (CACHE → DB →
        LEGACY), but the DB read is offloaded to a worker thread via
        db.db_utils.run_sync instead of blocking the event loop. Use this
        (via aget_prompt()) when loading several independent prompts that
        can be fetched concurrently with asyncio.gather.
        """
        cached = _cache_get(tenant_id, name, lang)
        if cached:
            print(f"[PROMPT] source=CACHE  tenant={tenant_id}  prompt={name}  lang={lang}")
            return PromptTemplate(name=name, template=cached, source="CACHE",
                                  language=lang, tenant_id=tenant_id)

        from db.db_utils import run_sync
        db_result = await run_sync(lambda: _PromptLoader._from_db(tenant_id, name, lang))
        if db_result:
            text, version = db_result
            _cache_set(tenant_id, name, lang, text)
            print(f"[PROMPT] source=DB     tenant={tenant_id}  prompt={name}"
                  f"  lang={lang}  version={version}")
            return PromptTemplate(name=name, template=text, source="DB",
                                  version=version, language=lang, tenant_id=tenant_id)

        if incoming is not None:
            attr   = PROMPT_KEYS.get(name, name)
            legacy = getattr(incoming, attr, None)
            if legacy:
                _cache_set(tenant_id, name, lang, legacy)
                print(f"[PROMPT] source=LEGACY tenant={tenant_id}  prompt={name}"
                      f"  (migrate to prompt_templates)")
                return PromptTemplate(name=name, template=legacy, source="LEGACY",
                                      language=lang, tenant_id=tenant_id)

        return None


# ── Renderer ───────────────────────────────────────────────────────────────────

class _PromptRenderer:
    """Renders template variables into a PromptTemplate — separated from loading."""

    @staticmethod
    def render(pt: PromptTemplate, **vars: Any) -> str:
        """
        Substitutes {variable} placeholders using str.replace() only.
        NEVER format_map(): prompts contain literal JSON {"type":"X"} that
        format_map() would crash on. str.replace() only touches explicit kwargs.
        """
        # Normalize literal \n / \t to real newline/tab characters. PostgreSQL
        # plain '...' string literals do NOT interpret \n as an escape sequence
        # (only E'...' strings do) — any prompt authored with \n notation
        # instead of an actual line break — including a tenant admin typing
        # it directly into Supabase Studio, which is the natural expectation —
        # stores the literal two-character sequence, reaching customers as
        # visible "\n" text instead of a line break. Safe for every tenant:
        # a prompt with a genuine actual newline is completely unaffected;
        # this only touches the literal backslash-n/backslash-t sequence.
        template = pt.template.replace("\\n", "\n").replace("\\t", "\t")
        result = template
        for k, v in vars.items():
            result = result.replace("{" + k + "}", str(v))
        remaining = re.findall(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', result)
        if remaining:
            print(f"[PROMPT] WARNING prompt='{pt.name}' tenant='{pt.tenant_id}' "
                  f"source='{pt.source}' v={pt.version}: "
                  f"unreplaced {remaining} — pass as kwargs to get_prompt()")
        return result


# ── Public API ─────────────────────────────────────────────────────────────────

def get_raw_prompt(incoming: Any, key: str) -> Optional[str]:
    """
    Load a prompt template text WITHOUT rendering any variables.
    Used for JSON config payloads (e.g. product_field_aliases) where the text
    IS the data and must not be passed through the renderer.
    Returns None if not found; does NOT raise.
    Does not require the key to be in PROMPT_KEYS.
    """
    tenant_id = getattr(incoming, "tenant_id", "")
    language  = getattr(incoming, "language",  "en") or "en"
    pt = _PromptLoader.load(tenant_id, key, language, incoming)
    return pt.template if pt else None


def get_prompt(incoming: Any, key: str, **template_vars: Any) -> str:
    """
    Load prompt + render variables. Primary public API.

    incoming only needs .tenant_id and .language — no longer needs prompt text.
    Raises RuntimeError if prompt not found anywhere — fix it in prompt_templates.
    """
    tenant_id = getattr(incoming, "tenant_id", "")
    language  = getattr(incoming, "language",  "en") or "en"

    if key not in PROMPT_KEYS:
        raise RuntimeError(
            f"[PROMPT] Unknown key: '{key}'. "
            f"Add it to PROMPT_KEYS and to prompt_templates in DB."
        )

    pt = _PromptLoader.load(tenant_id, key, language, incoming)
    if not pt:
        raise RuntimeError(
            f"[PROMPT] '{key}' not found for tenant '{tenant_id}' (lang={language}). "
            f"Run 014_migrate_hardcoded_prompts.sql to seed prompt_templates."
        )

    return _PromptRenderer.render(pt, **template_vars)


async def aget_prompt(incoming: Any, key: str, **template_vars: Any) -> str:
    """
    Async variant of get_prompt() — load + render, same behavior and same
    RuntimeError-on-missing contract, but non-blocking on a DB cache-miss.

    Use this (instead of get_prompt()) specifically when a caller needs to
    load several independent prompts for one reply and can fetch them
    concurrently, e.g.:

        body, footer = await asyncio.gather(
            aget_prompt(incoming, "some_body_prompt", **body_vars),
            aget_prompt(incoming, "some_footer_prompt"),
        )

    For single, standalone prompt loads, the existing synchronous
    get_prompt() is unchanged and still the simpler choice.
    """
    tenant_id = getattr(incoming, "tenant_id", "")
    language  = getattr(incoming, "language",  "en") or "en"

    if key not in PROMPT_KEYS:
        raise RuntimeError(
            f"[PROMPT] Unknown key: '{key}'. "
            f"Add it to PROMPT_KEYS and to prompt_templates in DB."
        )

    pt = await _PromptLoader.aload(tenant_id, key, language, incoming)
    if not pt:
        raise RuntimeError(
            f"[PROMPT] '{key}' not found for tenant '{tenant_id}' (lang={language}). "
            f"Run 014_migrate_hardcoded_prompts.sql to seed prompt_templates."
        )

    return _PromptRenderer.render(pt, **template_vars)


async def get_prompt_with_context(
    incoming: Any, key: str, inject_mem0: bool = True, **template_vars: Any,
) -> str:
    """Async variant that also injects Postgres semantic context variables."""
    base  = get_prompt(incoming, key, **template_vars)
    if not inject_mem0:
        return base
    mem0_vars = {"customer_preferences","product_context",
                 "negotiation_profile","workflow_context","conversation_summary"}
    used  = set(re.findall(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', base))
    needs = used & mem0_vars
    if not needs:
        return base
    try:
        from ai.context_builder import ContextBuilder
        from ai.request_context import AIRequestContext
        from models.schemas import IntentResult

        arc = getattr(incoming, "_cached_arc", None)
        if not arc:
            arc = AIRequestContext(
                incoming = incoming,
                result = IntentResult(intent="UNKNOWN", confidence_score=0.0, raw_text=getattr(incoming, "text", "")),
                session_history = []
            )
            incoming._cached_arc = arc

        cb = ContextBuilder(arc)
        ctx = await cb.build()
        ctx_dict = ctx.to_dict()
        for var in needs:
            val = ctx_dict.get(var) or ""
            base = base.replace("{" + var + "}", str(val))
    except Exception as e:
        print(f"[PROMPT] Context injection failed (non-critical): {e}")
    return base


def update_prompt_template(
    tenant_id: str, prompt_name: str, new_text: str,
    language: str = "en", description: str = "",
) -> bool:
    """Insert new version, archive old, invalidate cache."""
    try:
        from db.session_store import _get_client
        c = _get_client()
        r = (c.table("prompt_templates").select("version")
              .eq("tenant_id",tenant_id).eq("prompt_name",prompt_name)
              .eq("language",language).order("version",desc=True).limit(1).execute())
        nv = 1
        if r.data:
            from typing import cast
            row = cast(dict, r.data[0])
            nv = int(row.get("version", 0)) + 1
        c.table("prompt_templates").update({"status":"archived"})             .eq("tenant_id",tenant_id).eq("prompt_name",prompt_name)             .eq("language",language).eq("status","active").execute()
        c.table("prompt_templates").insert({
            "tenant_id":tenant_id,"prompt_name":prompt_name,"language":language,
            "version":nv,"status":"active","prompt_text":new_text,"description":description,
        }).execute()
        _cache_invalidate(tenant_id, prompt_name, language)
        print(f"[PROMPT] Updated '{prompt_name}' -> v{nv}")
        return True
    except Exception as e:
        print(f"[PROMPT] update failed: {e}")
        return False


# Compatibility alias used by prompt_builder.py
_load_from_db = _PromptLoader._from_db


# ── Key registry ───────────────────────────────────────────────────────────────
# Maps prompt_name → IncomingMessage attribute (legacy tenants column fallback).
# Entries for new prompts (014+) have no matching column — they always resolve
# via prompt_templates (step 2) and never reach the legacy fallback (step 3).

PROMPT_KEYS: dict = {
    # Old prompts — tenants table columns exist
    "intent_system_prompt":        "intent_system_prompt",
    "greeting_system_prompt":      "greeting_system_prompt",
    "entity_system_prompt":        "entity_system_prompt",
    "escalation_prompt":           "escalation_prompt",
    "unknown_prompt":              "unknown_prompt",
    "invoice_inquiry_prompt":      "invoice_inquiry_prompt",
    "invoice_confirm_prompt":      "invoice_confirm_prompt",
    "invoice_order_confirm_prompt":"invoice_order_confirm_prompt",
    "invoice_confirmation_prompt": "invoice_confirmation_prompt",
    "neg_is_request_prompt":       "neg_is_request_prompt",
    "neg_extract_qty_prompt":      "neg_extract_qty_prompt",
    "neg_detect_counter_prompt":   "neg_detect_counter_prompt",
    "neg_more_discount_prompt":    "neg_more_discount_prompt",
    "neg_detect_accept_prompt":    "neg_detect_accept_prompt",
    "neg_detect_qty_change_prompt":"neg_detect_qty_change_prompt",
    "neg_no_discount_prompt":      "neg_no_discount_prompt",
    "neg_first_offer_prompt":      "neg_first_offer_prompt",
    "neg_counter_offer_prompt":    "neg_counter_offer_prompt",
    "neg_final_price_prompt":      "neg_final_price_prompt",
    "fast_confirm_prompt":                   "fast_confirm_prompt",
    "product_summary_recommendation_prompt": "product_summary_recommendation_prompt",

    # New prompts (014+) — resolved via prompt_templates, no tenants column
    "parse_global_offer_tiers_prompt":           "parse_global_offer_tiers_prompt",
    "invoice_inquiry_check_prompt":              "invoice_inquiry_check_prompt",
    "order_confirmation_reply_check_prompt":     "order_confirmation_reply_check_prompt",
    "generate_invoice_cta_prompt":               "generate_invoice_cta_prompt",
    "invoice_confirmation_request_check_prompt": "invoice_confirmation_request_check_prompt",
    "fast_order_confirm_check_prompt":           "fast_order_confirm_check_prompt",
    "category_matcher_prompt":                   "category_matcher_prompt",
    "pf_data_extraction_prompt":                 "pf_data_extraction_prompt",
    "pf_history_resolver_prompt":                "pf_history_resolver_prompt",
    "pf_offer_inquiry_check_prompt":             "pf_offer_inquiry_check_prompt",
    "pf_offer_inquiry_check_l2_prompt":          "pf_offer_inquiry_check_l2_prompt",
    "pf_neg_product_change_check_prompt":        "pf_neg_product_change_check_prompt",
    "pf_vague_reference_check_prompt":           "pf_vague_reference_check_prompt",
    "pf_vague_reference_rewriter_prompt":        "pf_vague_reference_rewriter_prompt",
    "pf_named_product_extractor_prompt":         "pf_named_product_extractor_prompt",
    "pf_offers_formatter_prompt":                "pf_offers_formatter_prompt",
    "pf_vague_pronoun_resolver_l2_prompt":       "pf_vague_pronoun_resolver_l2_prompt",
    "pf_new_search_followup_classifier_prompt":  "pf_new_search_followup_classifier_prompt",
    "pf_comparison_prompt":                      "pf_comparison_prompt",
    "pf_image_installation_intent_prompt":       "pf_image_installation_intent_prompt",
    "pf_main_followup_prompt":                   "pf_main_followup_prompt",
    "neg_extract_negotiation_intent_prompt":     "neg_extract_negotiation_intent_prompt",
    "conversation_summary_prompt":               "conversation_summary_prompt",
    "neg_no_discount_fallback":                  "neg_no_discount_fallback",
    "neg_first_offer_fallback":                  "neg_first_offer_fallback",
    "neg_counter_offer_fallback":                "neg_counter_offer_fallback",
    "neg_final_price_fallback":                  "neg_final_price_fallback",

    # Migration 019 — hardcoded customer-facing strings moved to DB prompts
    "media_unsupported_prompt":                  "media_unsupported_prompt",
    "generate_invoice_cta_fallback":              "generate_invoice_cta_fallback",
    "invoice_no_orders_found_prompt":             "invoice_no_orders_found_prompt",
    "invoice_success_reply_prompt":               "invoice_success_reply_prompt",
    "delivery_estimation_prompt":                  "delivery_estimation_prompt",
    "invoice_pdf_failed_prompt":                  "invoice_pdf_failed_prompt",
    "neg_still_bargaining_prompt":                "neg_still_bargaining_prompt",
    "neg_ask_quantity_prompt":                    "neg_ask_quantity_prompt",
    "neg_ask_quantity_retry_prompt":              "neg_ask_quantity_retry_prompt",
    "whatsapp_max_message_limit":                 "whatsapp_max_message_limit",
    "graphrag_max_products_limit":                "graphrag_max_products_limit",
    "graphrag_no_url_configured_prompt":          "graphrag_no_url_configured_prompt",
    "graphrag_403_error_prompt":                  "graphrag_403_error_prompt",
    "graphrag_http_error_prompt":                 "graphrag_http_error_prompt",
    "graphrag_product_list_header_prompt":        "graphrag_product_list_header_prompt",
    "graphrag_product_list_footer_prompt":        "graphrag_product_list_footer_prompt",
    "graphrag_category_clarify_default":          "graphrag_category_clarify_default",
    "graphrag_category_clarify_greeting_prompt":  "graphrag_category_clarify_greeting_prompt",
    "graphrag_category_clarify_footer_prompt":    "graphrag_category_clarify_footer_prompt",
    "graphrag_empty_results_prompt":              "graphrag_empty_results_prompt",
    "graphrag_retry_failed_prompt":                "graphrag_retry_failed_prompt",
    "graphrag_exception_fallback_prompt":         "graphrag_exception_fallback_prompt",

    # Pipeline-wide safety net (main.py::run_pipeline / _get_safe_error_reply).
    # Tenant-customizable via DB, but the caller MUST catch RuntimeError/any
    # exception and fall back to a hardcoded string — this prompt existing in
    # the DB is a nice-to-have, not something the safety net can depend on to
    # fire correctly (a safety net that depends on the very systems it's
    # meant to catch failures in defeats its purpose).
    "pipeline_error_reply":                      "pipeline_error_reply",

    # Migration 022 — negotiator remaining hardcoded strings
    "neg_accepted_confirmation_prompt":           "neg_accepted_confirmation_prompt",
    "neg_qty_update_with_discount_prompt":        "neg_qty_update_with_discount_prompt",
    "neg_qty_update_no_discount_prompt":          "neg_qty_update_no_discount_prompt",
    "neg_qty_update_footer_prompt":               "neg_qty_update_footer_prompt",
    "neg_stalemate_reply_prompt":                 "neg_stalemate_reply_prompt",

    # Migration 023 — remaining hardcoded strings
    "neg_upsell_hint_prompt":                     "neg_upsell_hint_prompt",
    "neg_max_discount_unlocked_prompt":           "neg_max_discount_unlocked_prompt",
    "neg_already_confirmed_prompt":               "neg_already_confirmed_prompt",
    "pf_invalid_number_prompt":                   "pf_invalid_number_prompt",
    "pf_no_product_name_prompt":                  "pf_no_product_name_prompt",

    # Migration 028 — memory-aware GraphRAG queries
    "graphrag_query_builder_prompt":               "graphrag_query_builder_prompt",
    "no_active_workflow_prompt":                   "no_active_workflow_prompt",

    # Migration 021 — order summary (pipeline/router.py::_build_order_summary)
    "order_summary_full_discount_prompt":         "order_summary_full_discount_prompt",
    "order_summary_store_discount_only_prompt":   "order_summary_store_discount_only_prompt",
    "order_summary_plain_price_prompt":           "order_summary_plain_price_prompt",
    "order_summary_savings_line_prompt":          "order_summary_savings_line_prompt",
    "order_summary_footer_prompt":                "order_summary_footer_prompt",

    # Migration 024 — pricing.py::to_whatsapp_summary (last Category-2 item)
    "pricing_order_summary_full_discount_prompt":       "pricing_order_summary_full_discount_prompt",
    "pricing_order_summary_store_discount_only_prompt": "pricing_order_summary_store_discount_only_prompt",
    "pricing_order_summary_plain_price_prompt":         "pricing_order_summary_plain_price_prompt",
    "pricing_order_summary_savings_breakdown_prompt":   "pricing_order_summary_savings_breakdown_prompt",
    "pricing_order_summary_savings_prompt":             "pricing_order_summary_savings_prompt",
    "pricing_order_summary_footer_prompt":              "pricing_order_summary_footer_prompt",

    # Custom Dynamic Pipeline Prompts
    "knowledge_asset_answer_prompt":                    "knowledge_asset_answer_prompt",
    "followup_installation_footer_prompt":              "followup_installation_footer_prompt",
    "followup_installation_header_prompt":              "followup_installation_header_prompt",
    "followup_installation_pdf_prompt":                 "followup_installation_pdf_prompt",
    "followup_installation_video_prompt":               "followup_installation_video_prompt",
    "followup_installation_manual_prompt":              "followup_installation_manual_prompt",
    "followup_installation_steps_header_prompt":        "followup_installation_steps_header_prompt",
    "memory_query_classifier_prompt":                   "memory_query_classifier_prompt",
    "memory_no_orders_found_prompt":                    "memory_no_orders_found_prompt",
    "memory_order_formatter_prompt":                    "memory_order_formatter_prompt",
    "memory_offers_formatter_prompt":                   "memory_offers_formatter_prompt",
    "neg_payment_and_address_request_prompt":           "neg_payment_and_address_request_prompt",
    "validate_shipping_address_prompt":                 "validate_shipping_address_prompt",

    # Migration 030 — dynamic product cache field alias map (JSON config)
    # Maps intent-classifier field names to actual product_cache column names.
    # Stored as JSON in prompt_templates; loaded by router.py at runtime.
    "product_field_aliases":                            "product_field_aliases",
}