# ai/context_builder.py — ContextBuilder v2
#
# ROLE:
#   Orchestrates the assembly of LLMContext by:
#     1. Reading memory_strategy from DB (which memory types for this intent+workflow)
#     2. Calling MemoryManager to fetch+rank those memory types (in parallel)
#     3. Parsing raw Mem0 results into structured LLMContext fields
#     4. Caching the result on AIRequestContext (not on incoming)
#
# KEY DESIGN:
#   - ContextBuilder does NOT call Mem0 directly (that's MemoryManager's job)
#   - ContextBuilder does NOT build prompts (that's PromptBuilder's job)
#   - Strategy is DB-driven (memory_strategy table) with code fallback
#   - Fully parallel: all memory types fetched with asyncio.gather()

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ai.request_context import AIRequestContext, LLMContext

# ── Strategy cache (in-memory, 5 min TTL) ────────────────────────────────────
_STRATEGY_CACHE: dict = {}
_STRATEGY_TTL   = 300

# ── Hardcoded fallback strategy ───────────────────────────────────────────────
# Only used if memory_strategy table doesn't exist yet.
_DEFAULT_STRATEGY: dict[tuple[str, str], list[str]] = {
    ("FAQ_KNOWLEDGE",   "NEGOTIATING"):      ["product_context", "workflow_snapshot", "conversation"],
    ("FAQ_KNOWLEDGE",   "COUNTER_PRESENTED"):["product_context", "workflow_snapshot"],
    ("FAQ_KNOWLEDGE",   "*"):                ["product_context", "conversation"],
    ("WORKFLOW_ACTION", "NEGOTIATING"):      ["workflow_snapshot", "product_context", "negotiation_outcome"],
    ("WORKFLOW_ACTION", "ORDERING"):         ["workflow_snapshot", "product_context", "customer_preference"],
    ("WORKFLOW_ACTION", "BROWSING"):         ["product_context", "customer_preference", "conversation"],
    ("WORKFLOW_ACTION", "*"):                ["workflow_snapshot", "product_context", "conversation"],
    ("NEGOTIATION",     "*"):                ["negotiation_outcome", "workflow_snapshot", "product_context"],
    ("GREETING",        "*"):                ["customer_preference", "conversation"],
    ("HUMAN_ESCALATION","*"):                ["conversation", "workflow_snapshot", "product_context"],
    ("*",               "*"):                ["conversation", "product_context", "workflow_snapshot",
                                             "customer_preference"],
}


def _load_strategy_from_db(tenant_id: str) -> dict:
    """
    Loads memory_strategy table once per 5 minutes per tenant.
    Returns dict of {(intent, workflow): [mem_types]}.
    """
    cached = _STRATEGY_CACHE.get(tenant_id)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    try:
        from db.session_store import _get_client
        result = (
            _get_client()
            .table("memory_strategy")
            .select("intent,workflow,memory_types,max_results,enabled")
            .eq("tenant_id", tenant_id)
            .eq("enabled", True)
            .order("priority")
            .execute()
        )
        strategy: dict = {}
        for row in (result.data or []):
            row_dict = cast(dict, row)
            key   = (row_dict["intent"], row_dict["workflow"])
            types = [t.strip() for t in cast(str, row_dict["memory_types"]).split(",") if t.strip()]
            strategy[key] = types
        _STRATEGY_CACHE[tenant_id] = (strategy, time.monotonic() + _STRATEGY_TTL)
        return strategy
    except Exception:
        return {}


def _get_memory_types(tenant_id: str, intent: str, workflow: str) -> list[str]:
    """
    Returns the ordered list of memory types for (intent, workflow).
    DB strategy overrides code defaults; most specific match wins.
    """
    db   = _load_strategy_from_db(tenant_id)
    combined = {**_DEFAULT_STRATEGY, **db}
    for key in [(intent, workflow), (intent, "*"), ("*", workflow), ("*", "*")]:
        if key in combined:
            return combined[key]
    return _DEFAULT_STRATEGY[("*", "*")]


class ContextBuilder:
    """
    Assembles LLMContext for a request by:
      1. Determining which memory types to fetch (from memory_strategy)
      2. Delegating all fetching/ranking to MemoryManager
      3. Parsing results into LLMContext strings

    Example:
        arc = AIRequestContext(incoming=..., neg_state=neg_state)
        arc.llm_context = await ContextBuilder(arc).build()
    """

    def __init__(self, arc: "AIRequestContext"):
        self.arc        = arc
        self.tenant_id  = arc.tenant_id
        self.session_id = arc.session_id

    async def build(self, max_results: int = 3) -> "LLMContext":
        """
        Builds and returns LLMContext. All memory fetches run in parallel.
        If already built for this request, returns cached result immediately.
        """
        from ai.request_context import LLMContext
        from ai.memory_manager  import MemoryManager

        # Per-request cache key based on intent + workflow
        cache_key = f"_ctx_{self.arc.intent}_{self.arc.workflow}"
        if hasattr(self.arc, cache_key):
            return getattr(self.arc, cache_key)

        mem_types = _get_memory_types(
            self.tenant_id, self.arc.intent, self.arc.workflow
        )
        query = self.arc.current_product or self.arc.text

        mm      = MemoryManager(self.tenant_id, self.session_id)
        raw     = await mm.search(mem_types, query=query, max_results=max_results)
        profile = {}

        # If negotiation or preference types needed, also fetch customer profile
        if any(t in mem_types for t in ["negotiation_outcome", "customer_preference"]):
            profile = await mm.get_customer_profile()

        ctx = self._parse(raw, profile)

        if ctx.has_content():
            non_empty = sum(1 for v in ctx.to_dict().values() if v)
            print(f"[CTX] intent={self.arc.intent} workflow={self.arc.workflow} "
                  f"types={mem_types} → {non_empty} fields")

        # Cache on arc for this request
        setattr(self.arc, cache_key, ctx)
        return ctx

    def _parse(self, raw: dict, profile: dict) -> "LLMContext":
        from ai.request_context import LLMContext

        product_str  = self._parse_product(raw.get("product_context", []))
        workflow_str = self._parse_workflow(raw.get("workflow_snapshot", []))
        conv_str     = self._parse_conversation(raw.get("conversation", []))

        # Preferences
        prefs = profile.get("preferences", {})
        prefs_str = ""
        if prefs:
            parts = [f"{k}: {v}" for k, v in prefs.items() if v]
            prefs_str = "Preferences — " + ", ".join(parts) if parts else ""

        # Negotiation profile
        neg = profile.get("negotiation", {})
        neg_str = ""
        if neg and neg.get("avg_discount_pct") is not None:
            neg_str = f"Typically accepts {neg['avg_discount_pct']}% discount"
            if neg.get("typical_rounds"):
                neg_str += f" in {neg['typical_rounds']} rounds"
            if neg.get("budget_range"):
                neg_str += f", budget {neg['budget_range']}"

        return LLMContext(
            product_context      = product_str,
            customer_preferences = prefs_str,
            negotiation_profile  = neg_str,
            workflow_context     = workflow_str,
            conversation_summary = conv_str,
        )

    def _parse_product(self, memories: list) -> str:
        for r in memories:
            text = r.get("memory", "")
            if "PRODUCT_CONTEXT:" in text:
                try:
                    p = json.loads(text.split("PRODUCT_CONTEXT:", 1)[1].strip())
                    return (f"{p.get('name','')} | Rs.{p.get('price','')} | "
                            f"Warranty: {p.get('warranty','')} | "
                            f"Waterproof: {p.get('waterproof','')}")
                except Exception:
                    pass
        return ""

    def _parse_workflow(self, memories: list) -> str:
        for r in memories:
            text = r.get("memory", "")
            if "WORKFLOW_SNAPSHOT:" in text:
                try:
                    w   = json.loads(text.split("WORKFLOW_SNAPSHOT:", 1)[1].strip())
                    out = f"State: {w.get('state','')}"
                    if w.get("product"):
                        out += f" — {w['product']} x{w.get('quantity','')}"
                    if w.get("offer_price"):
                        out += f" @ Rs.{w['offer_price']}"
                    return out
                except Exception:
                    pass
        return ""

    def _parse_conversation(self, memories: list) -> str:
        parts = []
        for r in memories[:2]:
            text = r.get("memory", "")
            if (text and "PRODUCT_CONTEXT:" not in text
                    and "WORKFLOW_SNAPSHOT:" not in text
                    and "NEG_OUTCOME:" not in text):
                parts.append(text[:100])
        return " | ".join(parts)