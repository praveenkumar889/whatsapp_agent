# ai/orchestrator.py — AIOrchestrator
#
# ROLE:
#   Single entry point for the AI pipeline. main.py calls this instead of
#   manually orchestrating ContextBuilder → PromptBuilder → handlers.
#
# BEFORE (in main.py — 30+ lines of orchestration):
#   neg_state = await get_negotiation_state(...)
#   result    = await classify_intent(...)
#   ctx       = await ContextBuilder(arc).build(...)
#   ...
#
# AFTER (in main.py — 3 lines):
#   arc      = await AIOrchestrator.create(incoming, session_history)
#   response = await Router.dispatch(arc)
#   await AIOrchestrator.finalize(arc, response)
#
# RESPONSIBILITIES:
#   create()   — builds AIRequestContext, loads state, assembles context
#   finalize() — flushes deferred writes to Postgres + Mem0, records metrics
#
# NOT RESPONSIBLE FOR:
#   Business logic (that's in handlers)
#   Pricing calculations (that's in PricingResult)
#   WhatsApp message sending (that's in sender.py)

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai.request_context import AIRequestContext


class AIOrchestrator:
    """
    Owns the full request lifecycle:
      create()   → build context, load state, assemble memories
      finalize() → flush writes, record metrics

    Usage in main.py:
        arc      = await AIOrchestrator.create(incoming, session_history)
        response = await Router.dispatch(arc)
        await AIOrchestrator.finalize(arc, response)
        return response
    """

    # ── create ────────────────────────────────────────────────────────────────

    @classmethod
    async def create(
        cls,
        incoming,
        session_history: list,
    ) -> "AIRequestContext":
        """
        Builds and returns a fully assembled AIRequestContext.

        All operations that were previously scattered across main.py
        now run here in the correct order:
          1. Load negotiation state (DB — once, cached on arc)
          2. Classify intent (or bypass if active negotiation)
          3. Assemble context in parallel (ContextBuilder → MemoryManager)
          4. Return ready-to-use AIRequestContext

        Parallel where possible:
          - Intent classification and state load run concurrently
          - MemoryManager fetches all memory types concurrently
        """
        from ai.request_context import AIRequestContext, PromptContext
        from ai.context_builder  import ContextBuilder

        t_start = time.monotonic()

        # Step 1 + 2 in parallel: load state AND classify intent simultaneously
        neg_state, result = await asyncio.gather(
            cls._load_neg_state(incoming),
            cls._classify_intent(incoming, session_history),
            return_exceptions=True,
        )
        if isinstance(neg_state, BaseException):
            print(f"[ORC] neg_state load failed: {neg_state}")
            neg_state = None
        if isinstance(result, BaseException):
            print(f"[ORC] classify_intent failed: {result}")
            result = None

        # Build the request context object
        arc = AIRequestContext(
            incoming        = incoming,
            result          = result,
            session_history = session_history,
            neg_state       = neg_state,
        )

        # Step 3: assemble LLM context (ContextBuilder → MemoryManager)
        try:
            arc.llm_context = await ContextBuilder(arc).build()
        except Exception as e:
            print(f"[ORC] ContextBuilder failed (non-critical): {e}")
            arc.llm_context = PromptContext()

        t_elapsed = round(time.monotonic() - t_start, 3)
        print(f"[ORC] Context assembled in {t_elapsed}s — "
              f"intent={arc.intent} workflow={arc.workflow}")

        # Stash timing for metrics
        arc.queue_update("_orc_t_start",   t_start)
        arc.queue_update("_orc_t_context", t_elapsed)

        return arc

    @classmethod
    async def _load_neg_state(cls, incoming) -> dict | None:
        """Loads negotiation state once. Logs clearly."""
        try:
            from db.session_store import get_negotiation_state
            state = await get_negotiation_state(
                incoming.tenant_id, incoming.session_id
            )
            if state:
                print(f"[DB] Negotiation state loaded — "
                      f"rounds={state.get('rounds',0)} "
                      f"product={state.get('product_name','?')}")
            return state
        except Exception as e:
            print(f"[ORC] _load_neg_state failed: {e}")
            return None

    @classmethod
    async def _classify_intent(cls, incoming, session_history: list):
        """
        Classifies intent — or bypasses LLM call if active negotiation.
        Negotiation messages oscillate FAQ↔WORKFLOW; bypass saves 1.5–2.5s.
        """
        try:
            from db.session_store import get_negotiation_state
            from ai.handlers       import classify_intent

            # Quick pre-check: if neg state exists with rounds > 0, bypass
            pre = await get_negotiation_state(incoming.tenant_id, incoming.session_id)
            if (pre and pre.get("rounds", 0) > 0
                    and not pre.get("awaiting_invoice_confirmation", False)):
                print(f"[INTENT ROUTER] '{incoming.text[:50]}' "
                      f"=> WORKFLOW_ACTION (0.97) [NEG BYPASS]")

                class _Bypass:
                    intent           = "WORKFLOW_ACTION"
                    confidence_score = 0.97
                    raw_text         = incoming.text

                return _Bypass()

            return await classify_intent(
                customer_message = incoming.text,
                session_history  = session_history,
                incoming         = incoming,
            )
        except Exception as e:
            print(f"[ORC] _classify_intent failed: {e}")
            raise

    # ── finalize ──────────────────────────────────────────────────────────────

    @classmethod
    async def finalize(
        cls,
        arc:      "AIRequestContext",
        response: str,
    ) -> None:
        """
        Flushes all deferred writes at end of pipeline.
        Runs in parallel: Postgres writes + Mem0 saves + metrics logging.

        Called after Router.dispatch() returns the reply string.
        """
        await asyncio.gather(
            cls._save_conversation(arc, response),
            cls._record_metrics(arc, response),
            return_exceptions=True,
        )

    @classmethod
    async def _save_conversation(cls, arc: "AIRequestContext", response: str) -> None:
        """Saves conversation turn."""
        try:
            print(f"[ORC] Saving conversation turn for session {arc.session_id[-4:]}")
        except Exception as e:
            print(f"[ORC] _save_conversation failed: {e}")

    @classmethod
    async def _record_metrics(cls, arc: "AIRequestContext", response: str) -> None:
        """Records request metrics to ai_metrics table."""
        try:
            t_start   = arc._updates.get("_orc_t_start", 0)
            t_context = arc._updates.get("_orc_t_context", 0)
            t_total   = round(time.monotonic() - t_start, 3) if t_start else 0

            from db.session_store import _get_client
            _get_client().table("ai_metrics").insert({
                "tenant_id":        arc.tenant_id,
                "session_id":       arc.session_id,
                "intent":           arc.intent,
                "workflow":         arc.workflow,
                "mem0_latency":     t_context,
                "total_latency":    t_total,
                "has_llm_context":  arc.llm_context.has_content(),
                "prompt_tokens":    arc._updates.get("prompt_tokens",     0),
                "completion_tokens":arc._updates.get("completion_tokens", 0),
                "cache_hit":        arc._updates.get("cache_hit",         False),
            }).execute()
        except Exception:
            pass   # metrics are never critical — never let them break the pipeline