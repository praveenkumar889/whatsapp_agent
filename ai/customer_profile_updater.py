# ai/customer_profile_updater.py — Customer Profile & History Updater
#
# ROLE:
#   Generates summaries of conversations and purchase lifecycles, saving them to Postgres.

import asyncio
import json
from typing import Optional, Any
from openai import AzureOpenAI

from config import (
    AZURE_AI_ENDPOINT, AZURE_AI_API_KEY,
    AZURE_OPENAI_DEPLOYMENT, AZURE_AI_API_VERSION
)
from models.schemas import IncomingMessage
from db.customer_data_service import CustomerDataService

_client = AzureOpenAI(
    azure_endpoint = AZURE_AI_ENDPOINT,
    api_key        = AZURE_AI_API_KEY,
    api_version    = AZURE_AI_API_VERSION,
    timeout        = 15.0,
    max_retries    = 0,
)


async def generate_conversation_summary(
    tenant_id:  str,
    session_id: str,
    messages:   list,
    incoming:   Optional[IncomingMessage] = None,
) -> None:
    """
    Summarizes the recent conversation turns and saves the summary.
    Focuses on products discussed, decisions made, purchases, and neg outcomes.
    """
    if not messages:
        return
    try:
        from db.prompt_store import get_prompt

        convo_text = "\n".join(
            f"{'Customer' if m.get('role') == 'user' else 'Bot'}: {m.get('content', '')[:200]}"
            for m in messages[-20:]
        )

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

        print(f"[SUMMARY] Conversation summary generated for {session_id[-4:]} ({len(summary)} chars) - saving is no-op")

    except Exception as e:
        print(f"[SUMMARY] generate_conversation_summary failed: {e}")


async def save_purchase_summary(
    tenant_id:     str,
    session_id:    str,
    product_name:  str,
    quantity:      int,
    final_price:   float,
    order_id:      str,
    store_disc:    float = 0,
    neg_disc:      float = 0,
    incoming:      Optional[IncomingMessage] = None,
) -> None:
    """Saves a summary of the purchase context (no-op as Postgres order history handles this)."""
    try:
        print(f"[SUMMARY] Purchase logged: {product_name} x{quantity} @ Rs.{final_price:,.0f} (Order ID: {order_id})")
    except Exception as e:
        print(f"[SUMMARY] save_purchase_summary failed: {e}")


async def save_last_product(
    tenant_id:    str,
    session_id:   str,
    product:      dict,
    incoming:     Optional[IncomingMessage] = None,
) -> None:
    """Saves the last product context for checking quick specs/attributes."""
    try:
        pname = product.get("product_name") or product.get("name") or ""
        if pname:
            cds = CustomerDataService(tenant_id, session_id)
            await cds.save_product_view(pname)
            print(f"[SUMMARY] Last product updated: {pname}")
    except Exception as e:
        print(f"[SUMMARY] save_last_product failed: {e}")


async def save_customer_preference(
    tenant_id:    str,
    session_id:   str,
    pref_type:    str,
    value:        str,
    confidence:   float = 0.8,
    incoming:     Optional[IncomingMessage] = None,
) -> None:
    """Saves a detected customer preference to Postgres."""
    try:
        cds = CustomerDataService(tenant_id, session_id)
        await cds.save_preference(pref_type, value)
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
    incoming:      Optional[IncomingMessage] = None,
) -> None:
    """Saves negotiation outcome to database to build customer profile history."""
    try:
        cds = CustomerDataService(tenant_id, session_id)
        await cds.save_negotiation_outcome(
            product=product,
            opening_price=opening_price,
            final_price=final_price,
            rounds=rounds,
            accepted=accepted,
            quantity=quantity
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
    """Retrieves relevant customer profile and active workflow context."""
    try:
        from ai.context_builder import ContextBuilder

        class _MinimalArc:
            def __init__(self):
                self.tenant_id: str = tenant_id
                self.session_id: str = session_id
                self.text: str = query
                self.intent: str = intent
                self.workflow: str = workflow
                self.current_product: str = ""
                self._updates: dict = {}
                self.result = None
                self.resolved_product = None
                self.customer_context = ""

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
            "customer_context":     "",
        }
