# ai/prompt_builder.py — PromptBuilder
#
# ROLE:
#   Owns prompt rendering: load → inject context → validate → return.
#   Replaces direct calls to get_prompt() + manual variable spreading.
#
# BEFORE (scattered everywhere):
#   prompt = get_prompt(incoming, "neg_counter_offer_prompt",
#                       new_offer="2422", quantity=7,
#                       sender_name=incoming.sender_name,
#                       biz_name=incoming.biz_name)
#
# AFTER (clean, contextual):
#   prompt = await PromptBuilder(arc).render(
#       "neg_counter_offer_prompt",
#       new_offer="2422", quantity=7,
#   )
#   # Context, sender_name, biz_name injected automatically from arc
#
# VERSIONING:
#   prompt = await PromptBuilder(arc).render("neg_counter_offer_prompt", version=3)
#
# VALIDATION:
#   If required variables are missing, logs a clear warning (no silent placeholders).

from __future__ import annotations
from typing import Optional, TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ai.request_context import AIRequestContext


class PromptBuilder:
    """
    Renders prompts with context injection, versioning, and validation.

    Usage:
        pb = PromptBuilder(arc)

        # Simple render
        prompt = await pb.render("greeting_system_prompt")

        # With extra variables
        prompt = await pb.render("neg_counter_offer_prompt",
                                 new_offer="2422", quantity=7)

        # Specific version (A/B testing)
        prompt = await pb.render("neg_counter_offer_prompt", version=2)
    """

    def __init__(self, arc: "AIRequestContext"):
        self.arc = arc

    async def render(
        self,
        prompt_name: str,
        version:     Optional[int] = None,
        **extra_vars,
    ) -> str:
        """
        Loads and renders a prompt with layered variable injection.

        Variable injection layers (later layers override earlier ones):
          Layer 1 — System: tenant_id, language, current timestamp
          Layer 2 — LLM context: product_context, negotiation_profile, etc. (from Mem0)
          Layer 3 — Request: sender_name, biz_name, intent, workflow
          Layer 4 — Extra: caller-supplied variables (highest priority)

        This layering ensures:
          - Prompts always have basic system variables
          - Context from Mem0 enriches all prompts automatically
          - Caller-supplied vars always win (override context if same key)
        """
        import time as _time

        # Layer 1 — System variables (always available)
        system_vars = {
            "tenant_id":   self.arc.tenant_id,
            "language":    getattr(self.arc.incoming, "language", "en") or "en",
            "current_date": _time.strftime("%Y-%m-%d"),
        }

        # Layer 2 — LLM context (from ContextBuilder → MemoryManager)
        context_vars = self.arc.llm_context.to_dict()

        # Layer 3 — Request basics
        request_vars = {
            "sender_name": self.arc.sender_name,
            "biz_name":    getattr(self.arc.incoming, "biz_name", ""),
            "intent":      self.arc.intent,
            "workflow":    self.arc.workflow,
        }

        # Layer 4 — Caller-supplied (highest priority, overrides everything)
        # Merge all layers — later layers override earlier
        variables = {**system_vars, **context_vars, **request_vars, **extra_vars}

        # Load prompt text
        prompt_text = self._load(prompt_name, version)

        # Substitute variables (str.replace — never format_map)
        for var, val in variables.items():
            prompt_text = prompt_text.replace("{" + var + "}", str(val))

        # Validate — warn on unreplaced {variables}
        self._validate(prompt_name, prompt_text)

        return prompt_text

    def _load(self, prompt_name: str, version: Optional[int] = None) -> str:
        """
        Loads prompt from:
          1. prompt_templates table (versioned, multi-language)
          2. Tenant column fallback (backward compat)

        Raises RuntimeError if not found anywhere.
        """
        tenant_id = self.arc.tenant_id
        language  = getattr(self.arc.incoming, "language", "en") or "en"

        # Try cache first (5-min TTL in PromptStore)
        from db.prompt_store import _cache_get
        cached = _cache_get(tenant_id, prompt_name, language)
        if cached and not version:
            return cached

        # Try prompt_templates table
        try:
            from db.session_store import _get_client
            q = (
                _get_client()
                .table("prompt_templates")
                .select("prompt_text")
                .eq("tenant_id",   tenant_id)
                .eq("prompt_name", prompt_name)
                .eq("language",    language)
                .eq("status",      "active")
            )
            if version:
                q = q.eq("version", version)
            else:
                q = q.order("version", desc=True)
            result = q.limit(1).execute()
            if result.data:
                row = cast(dict, result.data[0])
                text = cast(str, row["prompt_text"])
                if not version:
                    from db.prompt_store import _cache_set
                    _cache_set(tenant_id, prompt_name, language, text)
                return text
        except Exception as e:
            print(f"[PROMPT] DB load failed for '{prompt_name}': {e}")

        # Fallback to tenant column on IncomingMessage
        from db.prompt_store import PROMPT_KEYS
        attr = PROMPT_KEYS.get(prompt_name, prompt_name)
        text = getattr(self.arc.incoming, attr, None)
        if text:
            from db.prompt_store import _cache_set
            _cache_set(tenant_id, prompt_name, language, text)
            return text

        raise RuntimeError(
            f"[PROMPT] '{prompt_name}' not found for tenant '{tenant_id}'. "
            f"Add it to prompt_templates table."
        )

    def _validate(self, prompt_name: str, rendered: str) -> None:
        """Warns if any {variable} placeholders remain after substitution."""
        import re
        remaining = re.findall(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', rendered)
        if remaining:
            print(f"[PROMPT] WARNING '{prompt_name}' for '{self.arc.tenant_id}': "
                  f"unreplaced variables {remaining}. Pass them as extra_vars.")