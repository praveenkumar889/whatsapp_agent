# ai/invoice_handler.py — Invoice and order confirmation handling
#
# Extracted from main.py to keep the orchestrator lightweight.

import asyncio
from typing import Optional, Any, Union
from openai import AzureOpenAI

from config import (
    AZURE_AI_ENDPOINT, AZURE_AI_API_KEY,
    AZURE_OPENAI_DEPLOYMENT, AZURE_AI_API_VERSION,
)
from db.session_store import (
    get_negotiation_state,
    save_negotiation_state,
    save_outbound_message,
)
from adapter.whatsapp_adapter import send_whatsapp_reply
from ai.order_service import OrderResult

def _get_invoice_prompt(key: str, incoming: Any = None, **vars) -> str:
    """Load invoice prompt from DB."""
    from db.prompt_store import get_prompt as _gp
    return _gp(incoming, key, **vars)



_client = AzureOpenAI(
    azure_endpoint = AZURE_AI_ENDPOINT,
    api_key        = AZURE_AI_API_KEY,
    api_version    = AZURE_AI_API_VERSION,
    timeout        = 30.0,
    max_retries    = 0,
)
from ai.request_profiler import wrap_llm_client as _wrap_llm_client
_wrap_llm_client(_client)

async def _is_invoice_inquiry(incoming) -> bool:
    """
    Uses LLM to determine if the customer's message is asking for their invoice,
    bill, receipt, or payment document. Zero hardcoding.
    """
    try:
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model       = AZURE_OPENAI_DEPLOYMENT,
                max_tokens  = 5,
                temperature = 0,
                messages    = [
                    {"role": "system", "content": _get_invoice_prompt("invoice_inquiry_check_prompt", incoming)},
                    {"role": "user", "content": incoming.text},
                ],
            )
        )
        raw = response.choices[0].message.content
        content = raw.strip().upper() if raw else ""
        return "YES" in content
    except Exception as e:
        print(f"[INVOICE] Inquiry check failed: {e}")
        return False

async def _generate_confirmation_prompt(reply_text: str, incoming) -> str:
    """
    Dynamically generates a short line asking the user to reply with 'Confirm' or 'Proceed'
    to automatically generate and receive their tax invoice. Zero hardcoding.
    """
    try:
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model       = AZURE_OPENAI_DEPLOYMENT,
                max_tokens  = 100,
                temperature = 0.5,
                messages    = [
                    {"role": "system", "content": _get_invoice_prompt("generate_invoice_cta_prompt", incoming, biz_name=incoming.biz_name)},
                    {"role": "user", "content": reply_text},
                ],
            )
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Empty or None response content")
        return content.strip()
    except Exception as e:
        print(f"[INVOICE] Failed to generate confirmation prompt: {e}")
        try:
            return _get_invoice_prompt("generate_invoice_cta_fallback", incoming)
        except RuntimeError:
            return "Reply 'Proceed' or 'Confirm' to automatically generate and receive your tax invoice! 📄"


async def _is_invoice_confirmation_request(incoming, session_history: list) -> bool:
    """
    Uses LLM to determine if the customer's message is a confirmation (e.g., 'Proceed', 'Confirm')
    in response to the assistant's previous message asking them to confirm to generate their invoice.
    Zero hardcoding.
    """
    if not session_history:
        return False

    recent_bot_msgs = [
        m["content"] for m in session_history[-4:]
        if m.get("role") == "assistant"
    ]
    if not recent_bot_msgs:
        return False

    last_bot_msg = recent_bot_msgs[-1]

    try:
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model       = AZURE_OPENAI_DEPLOYMENT,
                max_tokens  = 5,
                temperature = 0,
                messages    = [
                    {"role": "system", "content": _get_invoice_prompt("invoice_confirmation_request_check_prompt", incoming)},
                    {"role": "user", "content": f"Assistant: {last_bot_msg}\nUser: {incoming.text}"},
                ],
            )
        )
        raw = response.choices[0].message.content
        content = raw.strip().upper() if raw else ""
        return "YES" in content
    except Exception as e:
        print(f"[INVOICE] Confirmation check failed: {e}")
        return False


async def handle_invoice_request(incoming, negotiated_order: Optional[Union[OrderResult, dict]] = None) -> str:
    """
    Generates invoice PDF. When negotiated_order is passed (from confirmed
    negotiation), uses it directly — avoids stale pending order overriding
    the correct negotiated quantity/price.
    """
    print(f"[INVOICE] Handling invoice request for session {incoming.session_id}")
    from db.session_store import (
        get_last_order_from_orders,
        update_order_invoice_url,
        get_pending_order,
        delete_pending_order,
        get_cached_product_by_name,
    )
    from ai.order_service import complete_order
    from utils.invoice import generate_and_upload_invoice

    if negotiated_order:
        # Use the just-created negotiated order directly (correct qty + price).
        # Delete stale pending order which has old qty/price from before negotiation.
        order = negotiated_order
        print(f"[INVOICE] Using negotiated order: {order.get('order_id')}")
        try:
            pending = await get_pending_order(incoming.tenant_id, incoming.session_id)
            if pending:
                await delete_pending_order(incoming.tenant_id, incoming.session_id)
                print(f"[INVOICE] Deleted stale pending order")
        except Exception:
            pass
    else:
        # Non-negotiated path: commit pending order if exists
        try:
            pending = await get_pending_order(incoming.tenant_id, incoming.session_id)
            if pending:
                print(f"[INVOICE] Committing pending order: {pending}")
                product_name = pending["product_name"]
                qty_val  = pending["quantity_value"]
                qty_unit = pending["quantity_unit"] or "units"
                cached_product = await get_cached_product_by_name(incoming.tenant_id, product_name)
                if cached_product:
                    unit_price = float(cached_product.get("list_price") or 0)
                    items = [{
                        "product_name":   product_name,
                        "quantity_value": qty_val,
                        "quantity_unit":  qty_unit,
                        "unit_price":     unit_price,
                        "total_price":    qty_val * unit_price,
                    }]
                    new_order = await complete_order(
                        tenant_id   = incoming.tenant_id,
                        session_id  = incoming.session_id,
                        sender_name = incoming.sender_name,
                        items       = items,
                        gst_rate    = getattr(incoming, "gst_rate", 0.18),
                    )
                    if new_order:
                        print(f"[INVOICE] Order committed: {new_order.get('order_id')}")
                        await delete_pending_order(incoming.tenant_id, incoming.session_id)
                        try:
                            from db.session_store import clear_post_order_context
                            import asyncio as _asyncio
                            _asyncio.create_task(clear_post_order_context(incoming.tenant_id, incoming.session_id))
                        except Exception:
                            pass  # never block invoice for a context-cleanup save
        except Exception as commit_err:
            print(f"[INVOICE] Error committing pending order: {commit_err}")

        order = await get_last_order_from_orders(incoming.tenant_id, incoming.session_id)
    if not order:
        try:
            return _get_invoice_prompt("invoice_no_orders_found_prompt", incoming, sender_name=incoming.sender_name)
        except RuntimeError:
            return (
                f"I couldn't find any recent orders for you, {incoming.sender_name}. 🤔\n\n"
                f"If you'd like to place a new order, just let me know what you need!"
            )

    # Get invoice_url if already exists
    invoice_url = order.get("invoice_url")
    if not invoice_url:
        print(f"[INVOICE] Invoice URL missing for order {order.get('order_id')} — generating now...")
        invoice_url = await generate_and_upload_invoice(
            order         = order,
            biz_name      = incoming.biz_name,
            tagline       = incoming.tagline,
            city          = incoming.city,
            support_email = incoming.support_email,
            support_phone = getattr(incoming, "support_phone", None),
            website       = incoming.website,
            upi_id        = incoming.upi_id,
            account_name  = incoming.account_name,
            sender_name   = incoming.sender_name,
            sender_phone  = incoming.sender_phone,
            gst_rate      = getattr(incoming, "gst_rate", 0.18),
            gstin         = getattr(incoming, "gstin", None),
        )
        if invoice_url:
            await update_order_invoice_url(order["order_id"], incoming.tenant_id, invoice_url)
            print(f"[INVOICE] Invoice URL updated in DB: {invoice_url}")

    if invoice_url:
        try:
            return _get_invoice_prompt(
                "invoice_success_reply_prompt", incoming,
                order_id=order.get('order_id'), sender_name=incoming.sender_name,
                invoice_url=invoice_url, biz_name=incoming.biz_name,
            )
        except RuntimeError:
            return (
                f"Here is your tax invoice for order *{order.get('order_id')}*, {incoming.sender_name}! 📄\n\n"
                f"🔗 *Download Invoice PDF*:\n{invoice_url}\n\n"
                f"Thank you for doing business with *{incoming.biz_name}*! 🙏"
            )
    else:
        support = getattr(incoming, 'support_email', None) or incoming.biz_name
        try:
            return _get_invoice_prompt("invoice_pdf_failed_prompt", incoming, sender_name=incoming.sender_name, support=support)
        except RuntimeError:
            return (
                f"I had trouble generating your invoice PDF right now, {incoming.sender_name}. 🔧\n\n"
                f"Please contact our team at *{support}* to get your invoice."
            )


# ══════════════════════════════════════════════════════════════════════════════
# WHATSAPP SEND UTILITIES
# ══════════════════════════════════════════════════════════════════════════════