# db/prompt_store.py — Prompt Loader
#
# FIX APPLIED:
#   format_map() was replaced with targeted str.replace() substitution.
#
#   THE BUG:
#     Prompts stored in DB contain literal JSON examples like:
#       {"type": "GENERAL", "reply": "..."}
#     str.format_map() treats EVERY {...} as a placeholder — including
#     {"type": "GENERAL", ...} — and crashes with KeyError('"type"')
#     because that's not a variable we passed in.
#
#   THE FIX:
#     Only replace the EXACT template variables we were given as kwargs
#     (e.g. {sender_name}, {time_greeting}) using simple str.replace().
#     Everything else in the prompt — including literal JSON braces —
#     is left completely untouched.
#
# DESIGN:
#   All LLM prompts live in the tenants table in Supabase.
#   No prompt text exists anywhere in Python code.
#
#   get_prompt(incoming, key) → returns prompt string from incoming object
#
# USAGE:
#   from db.prompt_store import get_prompt
#   prompt = get_prompt(incoming, "intent_system_prompt")
#   # Raises RuntimeError if not set in DB — forces you to configure it

import time
from typing import Optional, Any
from models.schemas import IncomingMessage

# ── In-memory prompt cache ────────────────────────────────────────────────────
# Key: (tenant_id, prompt_name, language)  Value: (prompt_text, expires_at)
# TTL: 5 minutes — prompts rarely change; avoids DB hit on every message.
_CACHE: dict = {}
_CACHE_TTL   = 300  # seconds

def _cache_key(tenant_id: str, prompt_name: str, language: str) -> str:
    return f"{tenant_id}::{prompt_name}::{language}"

def _cache_get(tenant_id: str, prompt_name: str, language: str) -> Optional[str]:
    key   = _cache_key(tenant_id, prompt_name, language)
    entry = _CACHE.get(key)
    if entry and entry[1] > time.monotonic():
        return entry[0]
    return None

def _cache_set(tenant_id: str, prompt_name: str, language: str, text: str):
    _CACHE[_cache_key(tenant_id, prompt_name, language)] = (text, time.monotonic() + _CACHE_TTL)

def _load_from_db(tenant_id: str, prompt_name: str, language: str) -> Optional[str]:
    """Reads the active prompt from prompt_templates table."""
    try:
        from db.session_store import _get_client
        result = (
            _get_client()
            .table("prompt_templates")
            .select("prompt_text")
            .eq("tenant_id",   tenant_id)
            .eq("prompt_name", prompt_name)
            .eq("language",    language)
            .eq("status",      "active")
            .order("version", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            from typing import cast as _cast
            row = _cast(dict, result.data[0])
            return row["prompt_text"]
        return None
    except Exception as e:
        print(f"[PROMPT] DB read failed for '{prompt_name}': {e}")
        return None



# Maps prompt key → attribute name on IncomingMessage
PROMPT_KEYS = {
    # Core handlers
    "intent_system_prompt":        "intent_system_prompt",
    "greeting_system_prompt":      "greeting_system_prompt",
    "entity_system_prompt":        "entity_system_prompt",
    "escalation_prompt":           "escalation_prompt",
    "unknown_prompt":              "unknown_prompt",

    # Invoice handler
    "invoice_inquiry_prompt":      "invoice_inquiry_prompt",
    "invoice_confirm_prompt":      "invoice_confirm_prompt",
    "invoice_order_confirm_prompt":"invoice_order_confirm_prompt",
    "invoice_confirmation_prompt": "invoice_confirmation_prompt",

    # Negotiation — detection
    "neg_is_request_prompt":       "neg_is_request_prompt",
    "neg_extract_qty_prompt":      "neg_extract_qty_prompt",
    "neg_detect_counter_prompt":   "neg_detect_counter_prompt",
    "neg_more_discount_prompt":    "neg_more_discount_prompt",
    "neg_detect_accept_prompt":    "neg_detect_accept_prompt",
    "neg_detect_qty_change_prompt":"neg_detect_qty_change_prompt",

    # Negotiation — reply generators
    "neg_no_discount_prompt":      "neg_no_discount_prompt",
    "neg_first_offer_prompt":      "neg_first_offer_prompt",
    "neg_counter_offer_prompt":    "neg_counter_offer_prompt",
    "neg_final_price_prompt":      "neg_final_price_prompt",

    # Fast confirm
    "fast_confirm_prompt":         "fast_confirm_prompt",

    # Product summary + recommendation (shown after quantity updates)
    "product_summary_recommendation_prompt": "product_summary_recommendation_prompt",

    # ── Migrated from hardcoded (Migration 014) ───────────────────────────────
    # negotiator.py
    "parse_global_offer_tiers_prompt":           "parse_global_offer_tiers_prompt",
    # invoice_handler.py
    "invoice_inquiry_check_prompt":              "invoice_inquiry_check_prompt",
    "order_confirmation_reply_check_prompt":     "order_confirmation_reply_check_prompt",
    "generate_invoice_cta_prompt":               "generate_invoice_cta_prompt",
    "invoice_confirmation_request_check_prompt": "invoice_confirmation_request_check_prompt",
    # router.py
    "fast_order_confirm_check_prompt":           "fast_order_confirm_check_prompt",
    # graphrag_handler.py
    "category_matcher_prompt":                   "category_matcher_prompt",
    # product_followup.py (13 prompts)
    "pf_data_extraction_prompt":                 "pf_data_extraction_prompt",
    "pf_history_resolver_prompt":                "pf_history_resolver_prompt",
    "pf_offer_inquiry_check_prompt":             "pf_offer_inquiry_check_prompt",
    "pf_neg_product_change_check_prompt":        "pf_neg_product_change_check_prompt",
    "pf_vague_reference_check_prompt":           "pf_vague_reference_check_prompt",
    "pf_vague_reference_rewriter_prompt":        "pf_vague_reference_rewriter_prompt",
    "pf_offer_inquiry_check_l2_prompt":          "pf_offer_inquiry_check_l2_prompt",
    "pf_named_product_extractor_prompt":         "pf_named_product_extractor_prompt",
    "pf_offers_formatter_prompt":                "pf_offers_formatter_prompt",
    "pf_vague_pronoun_resolver_l2_prompt":       "pf_vague_pronoun_resolver_l2_prompt",
    "pf_comparison_prompt":                      "pf_comparison_prompt",
    "pf_image_installation_intent_prompt":       "pf_image_installation_intent_prompt",
    "pf_main_followup_prompt":                   "pf_main_followup_prompt",
    "pf_new_search_followup_classifier_prompt":  "pf_new_search_followup_classifier_prompt",
    "pf_comparison_prompt":                      "pf_comparison_prompt",
}


def get_prompt(incoming: Any, key: str, **template_vars) -> str:
    """
    Gets a prompt from the incoming object (loaded from tenants table).
    Raises RuntimeError if the prompt is not set in DB — this is intentional.
    No hardcoded fallback — if it's missing, fix it in the DB.

    Template variables are replaced using targeted str.replace() — NOT
    str.format_map(). This is critical: prompts often contain literal JSON
    examples like {"type": "GENERAL", "reply": "..."} which are NOT template
    placeholders. format_map() would try to resolve every {...} in the prompt
    and crash on literal JSON. str.replace() only touches the exact
    {variable_name} placeholders we were explicitly given as kwargs.

        get_prompt(incoming, "neg_extract_qty_prompt", product_name="Gate Light")
        → replaces literal substring "{product_name}" with "Gate Light"
        → leaves {"type": "GENERAL", ...} completely untouched

    Args:
        incoming:      IncomingMessage with tenant prompts loaded
        key:           prompt key (must be in PROMPT_KEYS)
        **template_vars: values to substitute into the prompt template

    Returns:
        str — the prompt with template variables substituted

    Raises:
        RuntimeError — if key is invalid or prompt is not set in DB
    """
    if key not in PROMPT_KEYS:
        raise RuntimeError(
            f"[PROMPT] Unknown prompt key: '{key}'. "
            f"Valid keys: {list(PROMPT_KEYS.keys())}"
        )

    attr = PROMPT_KEYS[key]
    prompt = getattr(incoming, attr, None)

    if not prompt or not prompt.strip():
        raise RuntimeError(
            f"[PROMPT] '{key}' is not set for tenant '{getattr(incoming, 'tenant_id', 'UNKNOWN')}'. "
            f"Run migrations/003_all_prompts_dynamic.sql and set this prompt in the tenants table."
        )

    # ── Targeted substitution — NOT format_map() ──────────────────────────────
    # Only replaces {key} for each kwarg we were explicitly given.
    # Any other {...} in the prompt (e.g. literal JSON examples) is left as-is.
    for var_name, var_value in template_vars.items():
        placeholder = "{" + var_name + "}"
        prompt = prompt.replace(placeholder, str(var_value))

    # ── Variable validation — warn on unreplaced placeholders ─────────────────
    # Catches cases where the prompt references {variable} but the caller
    # forgot to pass it. Previously these silently stayed as literal text
    # in the prompt, confusing the LLM. Now they log a clear warning.
    import re as _re
    remaining = _re.findall(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', prompt)
    if remaining:
        tenant = getattr(incoming, "tenant_id", "UNKNOWN")
        print(f"[PROMPT] WARNING: '{key}' for tenant '{tenant}' has "
              f"unreplaced variables: {remaining}. "
              f"Pass them as kwargs to get_prompt().")

    return prompt


async def get_prompt_with_context(
    incoming, key: str,
    inject_mem0: bool = True,
    **template_vars,
) -> str:
    """
    Enhanced get_prompt() that automatically injects relevant Mem0 memories
    into the prompt as named template variables.

    Adds these variables if the prompt template contains them:
      {customer_preferences}  — long-term preferences (colour, budget, category)
      {product_context}       — current product specs (enables no-GraphRAG answers)
      {negotiation_profile}   — customer negotiation history
      {workflow_context}      — current session state summary

    Usage:
        # Old — no memory injection
        prompt = get_prompt(incoming, "neg_counter_offer_prompt", new_offer="2422")

        # New — automatic memory injection (backwards compatible)
        prompt = await get_prompt_with_context(
            incoming, "neg_counter_offer_prompt", new_offer="2422"
        )

    All existing get_prompt() call sites work unchanged — this is an opt-in
    upgrade. If inject_mem0=False (or MEM0_API_KEY not set) it behaves
    identically to get_prompt().

    Zero workflow impact: if memory retrieval fails the prompt is returned
    as-is without the context variables (they just stay empty strings).
    """
    # Get the base prompt with caller-supplied vars
    base_prompt = get_prompt(incoming, key, **template_vars)

    if not inject_mem0:
        return base_prompt

    # Only fetch Mem0 context if the prompt template actually uses it —
    # avoids unnecessary async calls for prompts that don't need memory.
    import re as _re
    mem0_vars = {"customer_preferences", "product_context",
                 "negotiation_profile", "workflow_context"}
    used_vars = set(_re.findall(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', base_prompt))
    needs_context = used_vars & mem0_vars

    if not needs_context:
        return base_prompt

    # Fetch relevant Mem0 memories in parallel
    try:
        from db.memory_store import get_context_for_prompt
        context = await get_context_for_prompt(
            tenant_id  = incoming.tenant_id,
            session_id = incoming.session_id,
            query      = template_vars.get("product_name", "") or incoming.text,
        )
        # Inject only the context variables the prompt actually uses
        for var in needs_context:
            val = context.get(var, "")
            base_prompt = base_prompt.replace("{" + var + "}", val)
    except Exception as e:
        print(f"[PROMPT] Mem0 context injection failed (non-critical): {e}")

    return base_prompt