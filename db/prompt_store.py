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
            print(f"[PROMPT] DB read failed for '{name}': {e}")
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
        result = pt.template
        for k, v in vars.items():
            result = result.replace("{" + k + "}", str(v))
        remaining = re.findall(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', result)
        if remaining:
            print(f"[PROMPT] WARNING prompt='{pt.name}' tenant='{pt.tenant_id}' "
                  f"source='{pt.source}' v={pt.version}: "
                  f"unreplaced {remaining} — pass as kwargs to get_prompt()")
        return result


# ── Public API ─────────────────────────────────────────────────────────────────

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


async def get_prompt_with_context(
    incoming: Any, key: str, inject_mem0: bool = True, **template_vars: Any,
) -> str:
    """Async variant that also injects Mem0 semantic context variables."""
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
        from db.memory_store import get_relevant_context
        ctx = await get_relevant_context(
            tenant_id  = getattr(incoming, "tenant_id", ""),
            session_id = getattr(incoming, "session_id", ""),
            query      = str(template_vars.get("product_name","") or getattr(incoming,"text","")),
        )
        ctx_str = " | ".join(m.get("content","") for m in ctx if m.get("content"))
        for var in needs:
            base = base.replace("{" + var + "}", ctx_str or "")
    except Exception as e:
        print(f"[PROMPT] Mem0 injection failed (non-critical): {e}")
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
        print(f"[PROMPT] Updated '{prompt_name}' → v{nv}")
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
}