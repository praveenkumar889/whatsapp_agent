# ai/customer_history_handler.py — Customer History Query Handler
#
# ROLE:
#   Classifies and responds to customer inquiries about past completed orders
#   and negotiation/offer history using PostgreSQL database tables.

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

_client = AzureOpenAI(
    azure_endpoint = AZURE_AI_ENDPOINT,
    api_key        = AZURE_AI_API_KEY,
    api_version    = AZURE_AI_API_VERSION,
    timeout        = 15.0,
    max_retries    = 0,
)
from ai.request_profiler import wrap_llm_client as _wrap_llm_client
_wrap_llm_client(_client)


def _get_prompt_safe(incoming, key: str, fallback: str, **kwargs) -> str:
    """Safe prompt loader, falling back if not seeded in Postgres database."""
    try:
        return get_prompt(incoming, key, **kwargs)
    except RuntimeError:
        return fallback


async def handle_customer_history_query(incoming, session_history: list) -> Optional[str]:
    """
    Main history query handler:
    1. Classifies if query is about completed orders, past offers, or general catalog/other.
    2. Queries Postgres-backed CustomerDataService.
    3. Formats the output dynamically using DB templates.
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
        print(f"[HISTORY_HANDLER] Classification failed: {e}")
        return None

    print(f"[HISTORY_HANDLER] Classified message as '{memory_type}' with confidence {confidence:.2f}")

    if confidence < 0.6 or memory_type == "other":
        return None  # Fall back to standard GraphRAG query

    from db.customer_data_service import CustomerDataService
    cds = CustomerDataService(incoming.tenant_id, incoming.session_id)

    # ── Handle Completed Order Inquiries ──
    if memory_type == "order":
        try:
            orders = await cds.get_order_history(limit=3)
            
            if not orders:
                no_orders_fallback = f"Hi {incoming.sender_name or 'there'}! I couldn't find any previous orders for you. Would you like to browse our catalog or place a new order? 😊"
                return _get_prompt_safe(
                    incoming, "memory_no_orders_found_prompt", fallback=no_orders_fallback,
                    sender_name=incoming.sender_name or "there"
                )
            
            details_list = []
            for o in orders:
                prod = o.get("product_name")
                qty = o.get("quantity_value") or 1
                price = float(o.get("total_price") or 0)
                oid = o.get("order_id") or "N/A"
                inv_url = o.get("invoice_url")
                
                detail = f"• Order {oid}: {prod} x{qty} @ Rs.{price:,.0f}"
                if inv_url:
                    detail += f"\n  🔗 *Invoice URL:* {inv_url}"
                details_list.append(detail)

            order_details = "\n".join(details_list)
            formatter_fallback = f"Hi {incoming.sender_name or 'there'}! Here are your previous orders:\n\n{order_details}"
            return _get_prompt_safe(
                incoming, "memory_order_formatter_prompt", fallback=formatter_fallback,
                sender_name=incoming.sender_name or "there", order_details=order_details
            )

        except Exception as order_err:
            print(f"[HISTORY_HANDLER] Order lookup failure: {order_err}")
            return None

    # ── Handle Offer Inquiries ──
    elif memory_type == "offer":
        try:
            offers = await cds.get_offer_history(limit=3)

            # Get current active store offers for the tenant
            from db.session_store import get_tenant_offers
            active_store_offers = ""
            try:
                raw_offers = await get_tenant_offers(incoming.tenant_id)
                active_store_offers = raw_offers.get("offers_text", "") if raw_offers else ""
            except Exception as e:
                print(f"[HISTORY_HANDLER] Failed to fetch current active offers: {e}")

            details_list = []
            for o in offers:
                prod = o.get("product_name")
                disc = float(o.get("discount_applied") or 0)
                status = "Accepted" if o.get("accepted") else "Rejected"
                details_list.append(f"• {prod}: {disc:.0f}% off - {status}")

            offer_details = "\n".join(details_list) if details_list else "No past offer negotiations found."
            formatter_fallback = f"Hi {incoming.sender_name or 'there'}!\n\nPast Offers:\n{offer_details}\n\nCurrent Store Offers:\n{active_store_offers}"
            return _get_prompt_safe(
                incoming, "memory_offers_formatter_prompt", fallback=formatter_fallback,
                sender_name=incoming.sender_name or "there", offer_details=offer_details,
                current_store_offers=active_store_offers
            )

        except Exception as offer_err:
            print(f"[HISTORY_HANDLER] Offer lookup failure: {offer_err}")
            return None

    return None
