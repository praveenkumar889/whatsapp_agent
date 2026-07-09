# ai/memory_handler.py — Centralized Memory Query Router & Formatter
#
# ROLE:
#   Classifies and responds to customer inquiries about past completed orders
#   and negotiation/offer history using long-term Mem0 data.
#   Bypasses GraphRAG catalog search for purely transactional history queries.

import asyncio
import json
import time
from typing import Optional
from openai import AzureOpenAI

from config import (
    AZURE_AI_ENDPOINT, AZURE_AI_API_KEY,
    AZURE_OPENAI_DEPLOYMENT, AZURE_AI_API_VERSION
)
from db.prompt_store import get_prompt
from ai.memory_manager import MemoryManager

_client = AzureOpenAI(
    azure_endpoint = AZURE_AI_ENDPOINT,
    api_key        = AZURE_AI_API_KEY,
    api_version    = AZURE_AI_API_VERSION,
    timeout        = 15.0,
    max_retries    = 0,
)


def _get_prompt_safe(incoming, key: str, fallback: str, **kwargs) -> str:
    """Safe prompt loader, falling back if not seeded in Postgres database."""
    try:
        return get_prompt(incoming, key, **kwargs)
    except RuntimeError:
        return fallback


async def handle_memory_query(incoming, session_history: list) -> Optional[str]:
    """
    Main memory handler:
    1. Classifies the query (order query, offer query, or other/catalog query).
    2. Queries Mem0 if relevant.
    3. Formats and returns a personalized WhatsApp message.
    4. Returns None if it is not a memory-related query, falling back to GraphRAG.
    """
    # ── Classification Prompt ──
    classifier_fallback = (
        "You are an intent classifier for a store customer support bot.\n"
        "Classify if the customer's message is asking about:\n"
        "- \"order\": Their past completed purchases, order history, what they bought, or invoice records.\n"
        "- \"offer\": Past discounts, special pricing history, or active offers they previously accepted.\n"
        "- \"other\": General questions, catalog browsing, greetings, product questions, or current pricing.\n\n"
        "Return a JSON object with keys:\n"
        "{\n"
        "  \"memory_type\": \"order\" | \"offer\" | \"other\",\n"
        "  \"confidence\": 0.0 to 1.0\n"
        "}"
    )

    system_prompt = _get_prompt_safe(
        incoming, "memory_query_classifier_prompt", fallback=classifier_fallback
    )

    try:
        resp = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model       = AZURE_OPENAI_DEPLOYMENT,
                max_tokens  = 60,
                temperature = 0,
                response_format = {"type": "json_object"},
                messages    = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": incoming.text},
                ],
            )
        )
        content = resp.choices[0].message.content
        parsed = json.loads(content.strip()) if content else {}
        memory_type = str(parsed.get("memory_type", "other")).lower()
        confidence = float(parsed.get("confidence", 0.0))
    except Exception as e:
        print(f"[MEMORY_HANDLER] Classification failed: {e}")
        return None

    print(f"[MEMORY_HANDLER] Classified message as '{memory_type}' with confidence {confidence:.2f}")

    if confidence < 0.6 or memory_type == "other":
        return None  # Fall back to standard GraphRAG query

    mm = MemoryManager(incoming.tenant_id, incoming.session_id)

    # ── Handle Completed Order Inquiries ──
    if memory_type == "order":
        try:
            results = await mm.search(["order_history"], query=incoming.text, max_results=3)
            orders = results.get("order_history", [])
            
            if not orders:
                # Retrieve generic fallback prompt if no past orders are in Mem0
                return _get_prompt_safe(
                    incoming, "memory_no_orders_found_prompt",
                    fallback=f"Hi {incoming.sender_name}! I couldn't find any previous orders for you under this number. "
                             f"Would you like to browse our catalog or place a new order? 😊"
                )

            # Format the past order items
            formatted_details = []
            for r in orders:
                raw_text = r.get("memory", "")
                if "ORDER_HISTORY:" in raw_text:
                    try:
                        data = json.loads(raw_text.split("ORDER_HISTORY:", 1)[1].strip())
                        negotiated_str = " (Negotiated)" if data.get("negotiated") else ""
                        formatted_details.append(
                            f"• {data.get('product')} x{data.get('quantity')} for Rs.{data.get('price'):,.0f}/unit on {data.get('date', '')[:10]}{negotiated_str}"
                        )
                    except Exception:
                        pass
            
            order_details = "\n".join(formatted_details) or "No details available."

            formatter_fallback = (
                f"Hi {incoming.sender_name}! Here is a summary of your previous completed orders:\n\n"
                f"{order_details}\n\n"
                f"Let me know if you would like to re-order any of these or if you need another invoice! 📄"
            )

            return _get_prompt_safe(
                incoming, "memory_order_formatter_prompt",
                fallback      = formatter_fallback,
                sender_name   = incoming.sender_name,
                order_details = order_details
            )

        except Exception as err:
            print(f"[MEMORY_HANDLER] Past order retrieval failed: {err}")
            return None

    # ── Handle Past Offer/Discount Inquiries ──
    elif memory_type == "offer":
        try:
            results = await mm.search(["offer_history"], query=incoming.text, max_results=3)
            offers = results.get("offer_history", [])

            from db.session_store import get_tenant_offers
            to_data = await get_tenant_offers(incoming.tenant_id)
            current_offers_str = to_data.get("offers_text", "") if to_data else ""

            if not offers:
                # Fallback to current offers only if no past offers in memory
                return _get_prompt_safe(
                    incoming, "memory_no_offers_found_prompt",
                    fallback=f"Hi {incoming.sender_name}! We don't have a negotiation history saved for you yet, but here are our current active store offers:\n\n"
                             f"{current_offers_str or 'No active offers at this moment.'}\n\n"
                             f"Let me know if you'd like to check out some products! 💡"
                )

            # Format the past offers
            formatted_details = []
            for r in offers:
                raw_text = r.get("memory", "")
                if "OFFER_HISTORY:" in raw_text:
                    try:
                        data = json.loads(raw_text.split("OFFER_HISTORY:", 1)[1].strip())
                        formatted_details.append(
                            f"• {data.get('store_offer_pct')}% off on {data.get('product')} (accepted price Rs.{data.get('negotiated_price'):,.0f}/unit)"
                        )
                    except Exception:
                        pass

            offer_details = "\n".join(formatted_details) or "No details available."

            formatter_fallback = (
                f"Hi {incoming.sender_name}! On your previous orders, you unlocked:\n\n"
                f"{offer_details}\n\n"
                f"For your next order, here are our current active store offers:\n\n"
                f"{current_offers_str or 'Standard catalog pricing applies.'}\n\n"
                f"Let me know what you'd like to set up today! 🚀"
            )

            return _get_prompt_safe(
                incoming, "memory_offers_formatter_prompt",
                fallback             = formatter_fallback,
                sender_name          = incoming.sender_name,
                offer_details        = offer_details,
                current_store_offers = current_offers_str
            )

        except Exception as err:
            print(f"[MEMORY_HANDLER] Past offer retrieval failed: {err}")
            return None

    return None
