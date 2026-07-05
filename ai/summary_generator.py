# ai/summary_generator.py — Semantic Summary Generator
#
# ROLE:
#   Generates rich semantic summaries at conversation lifecycle events and
#   saves them to Mem0. Never blocks the response path — always fire-and-forget.
#
# WHEN TO GENERATE (Step 11 from architecture):
#   - Order/invoice completed → purchase_summary + negotiation_profile
#   - Product selected        → last_product + preference
#   - Conversation timeout    → conversation_summary
#
# WHAT GOES TO MEM0 (semantic, not transactional):
#   - conversation_summary   — what happened, what was discussed
#   - purchase_summary       — what they bought, at what price
#   - negotiation_profile    — negotiation behavior pattern
#   - customer_preference    — product preferences, budget signals
#   - last_product           — most recent product context
#
# WHAT STAYS IN POSTGRES (transactional, never in Mem0):
#   - orders, order_items, messages, workflow_sessions, product_cache

from __future__ import annotations
import asyncio
import json
from typing import Optional

from config import (
    AZURE_AI_ENDPOINT, AZURE_AI_API_KEY,
    AZURE_OPENAI_DEPLOYMENT, AZURE_AI_API_VERSION,
)
from openai import AzureOpenAI
from models.schemas import IncomingMessage

_client = AzureOpenAI(
    azure_endpoint = AZURE_AI_ENDPOINT,
    api_key        = AZURE_AI_API_KEY,
    api_version    = AZURE_AI_API_VERSION,
    timeout        = 20.0,
    max_retries    = 0,
)


async def generate_and_save_conversation_summary(
    tenant_id:       str,
    session_id:      str,
    messages:        list[dict],   # [{role, content}, ...] from Postgres
    incoming: Optional[IncomingMessage] = None,
) -> None:
    """
    Step 10 + Step 5 from architecture:
    Generates a semantic summary of the full conversation and saves to Mem0.

    Called as fire-and-forget background task — never blocks the user response.
    Triggered at: invoice confirmed, conversation timeout, or explicit end.

    The summary captures WHAT happened (products discussed, decisions made,
    concerns raised) — not the raw text. This is what makes retrieval useful:
    "Need another 2" → Mem0 returns "Purchased Yash 12W, 4 units" → LLM understands.
    """
    if not messages:
        return
    try:
        from ai.memory_manager import MemoryManager
        from db.prompt_store import get_prompt

        # Build conversation text for summarization
        convo_text = "\n".join(
            f"{'Customer' if m.get('role') == 'user' else 'Bot'}: {m.get('content', '')[:200]}"
            for m in messages[-20:]  # last 20 turns max
        )

        # Load summary prompt from DB (or use inline default)
        summary_prompt = ""
        if incoming:
            try:
                summary_prompt = get_prompt(
                    incoming, "conversation_summary_prompt",
                    conversation = convo_text,
                )
            except Exception:
                pass

        if not summary_prompt:
            summary_prompt = f"""Summarize this customer conversation in 3-5 concise sentences.
Focus on: products discussed, decisions made, purchases, negotiation outcomes, questions asked.
Do NOT include greetings, small talk, or filler.
Be factual and specific (include product names, prices, quantities where mentioned).

Conversation:
{convo_text}

Summary:"""

        loop = asyncio.get_event_loop()
        r = await loop.run_in_executor(
            None, lambda: _client.chat.completions.create(
                model      = AZURE_OPENAI_DEPLOYMENT,
                max_tokens = 200,
                temperature= 0.2,
                messages   = [{"role": "user", "content": summary_prompt}],
            )
        )
        content = r.choices[0].message.content
        if not content:
            return
        summary = content.strip()

        mm = MemoryManager(tenant_id, session_id)
        await mm._async_save(
            f"CONVERSATION_SUMMARY: {summary}",
            "conversation_summary",
        )
        print(f"[SUMMARY] Conversation summary saved for {session_id[-4:]} ({len(summary)} chars)")

    except Exception as e:
        print(f"[SUMMARY] generate_conversation_summary failed: {e}")


async def save_purchase_summary(
    tenant_id:    str,
    session_id:   str,
    product_name: str,
    quantity:     int,
    final_price:  float,
    order_id:     str,
    store_disc:   float = 0,
    neg_disc:     float = 0,
) -> None:
    """
    Step 11 — Order Completed event:
    Saves a structured purchase summary to Mem0.

    This is what enables "Need another 2" to work months later:
    Mem0 returns this summary → LLM knows which product "another" refers to.

    Note: does NOT replace the orders/order_items DB records.
    Those are the source of truth for billing. This is AI context only.
    """
    try:
        from ai.memory_manager import MemoryManager

        summary = {
            "type":         "purchase_summary",
            "product":      product_name,
            "quantity":     quantity,
            "final_price":  final_price,
            "order_id":     order_id,
            "store_disc":   store_disc,
            "neg_disc":     neg_disc,
        }
        mm = MemoryManager(tenant_id, session_id)
        await mm._async_save(
            f"PURCHASE_SUMMARY: {json.dumps(summary)}",
            "purchase_summary",
            extra_meta={"product": product_name, "order_id": order_id},
        )
        print(f"[SUMMARY] Purchase saved: {product_name} x{quantity} @ Rs.{final_price:,.0f}")
    except Exception as e:
        print(f"[SUMMARY] save_purchase_summary failed: {e}")


async def save_last_product(
    tenant_id:    str,
    session_id:   str,
    product:      dict,
) -> None:
    """
    Step 11 — Product Selected event:
    Updates the "last discussed product" in Mem0.

    Enables pronoun resolution: "is it waterproof?" → Mem0 returns product context
    → LLM knows what "it" refers to without re-fetching from GraphRAG.
    """
    try:
        from ai.memory_manager import MemoryManager
        mm = MemoryManager(tenant_id, session_id)
        await mm.save_product_context(product)
        print(f"[SUMMARY] Last product updated: {product.get('product_name', '?')}")
    except Exception as e:
        print(f"[SUMMARY] save_last_product failed: {e}")


async def save_customer_preference(
    tenant_id:    str,
    session_id:   str,
    pref_type:    str,
    value:        str,
    confidence:   float = 0.8,
) -> None:
    """
    Step 11 — Customer Preference event:
    Saves a detected preference to Mem0 (persists across sessions, 1-year TTL).

    Examples:
      pref_type="preferred_category"  value="Garden Bollard"
      pref_type="budget_range"        value="Rs.1800-2000"
      pref_type="preferred_wattage"   value="12W"
    """
    try:
        from ai.memory_manager import MemoryManager
        mm = MemoryManager(tenant_id, session_id)
        await mm.save_preference(pref_type, value, confidence)
    except Exception as e:
        print(f"[SUMMARY] save_customer_preference failed: {e}")


async def update_negotiation_profile(
    tenant_id:     str,
    session_id:    str,
    product:       str,
    opening_price: float,
    final_price:   float,
    rounds:        int,
    accepted:      bool,
    quantity:      int,
) -> None:
    """
    Step 11 — Negotiation Completed event:
    Saves negotiation outcome to Mem0 to build a customer profile over time.

    After 3-4 orders, get_customer_profile() returns:
      "Typically accepts 7% discount in 3 rounds, budget Rs.1800-2000"
    This enables smarter opening offers for returning customers.
    """
    try:
        from ai.memory_manager import MemoryManager
        mm = MemoryManager(tenant_id, session_id)
        await mm.save_negotiation_outcome(
            product, opening_price, final_price, rounds, accepted, quantity
        )
    except Exception as e:
        print(f"[SUMMARY] update_negotiation_profile failed: {e}")


async def get_semantic_context(
    tenant_id:  str,
    session_id: str,
    query:      str = "",
    intent:     str = "*",
    workflow:   str = "*",
) -> dict:
    """
    Step 7 + Step 8 — Retrieval:
    Fetches relevant semantic memories from Mem0 for the current request.

    Used by ContextBuilder to enrich the LLM prompt with customer history.
    Returns structured strings ready for prompt injection.

    Multi-tenant isolation guaranteed:
    Every search uses both user_id=session_id AND agent_id=tenant_id.
    Tenant A never sees Tenant B memories.
    """
    try:
        from ai.context_builder import ContextBuilder
        from ai.request_context import AIRequestContext, LLMContext

        # Build a minimal arc for ContextBuilder
        class _MinimalArc:
            def __init__(self):
                self.tenant_id: str = tenant_id
                self.session_id: str = session_id
                self.text: str = query
                self.intent: str = intent
                self.workflow: str = workflow
                self.current_product: str = ""
                self._updates: dict = {}

        arc = _MinimalArc()
        cb  = ContextBuilder(arc)  # type: ignore[arg-type]
        ctx = await cb.build(max_results=3)
        return ctx.to_dict()
    except Exception as e:
        print(f"[SUMMARY] get_semantic_context failed: {e}")
        return {
            "product_context":      "",
            "customer_preferences": "",
            "negotiation_profile":  "",
            "workflow_context":     "",
            "conversation_summary": "",
        }