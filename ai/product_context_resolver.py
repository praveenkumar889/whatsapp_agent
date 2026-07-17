# ai/product_context_resolver.py — Resolves active product name from session/history
from typing import Any, Optional
from db.session_store import get_negotiation_state, get_last_discussed_product, get_graphrag_product_selection
from db.customer_data_service import CustomerDataService

# Sentinel distinguishing "caller didn't pass a value" (need to fetch) from
# "caller explicitly passed None" (already fetched — genuinely no active
# negotiation). A plain `None` default can't tell these apart, which used to
# cause a redundant get_negotiation_state() DB round-trip on every message
# for the common case of a customer who isn't mid-negotiation.
_NOT_PROVIDED: Any = object()

class ProductContextResolver:
    @staticmethod
    async def resolve(tenant_id: str, session_id: str, incoming_cached_neg: Optional[dict] = _NOT_PROVIDED, incoming: Optional[Any] = None, session_history: Optional[list] = None) -> Optional[str]:
        """
        Resolves active product name using a waterfall priority:
        0. Numeric index selection from active selection list
        1. Active negotiation state
        2. Last discussed product from active session
        3. Latest completed order product from customer history
        """
        # 0. Check for index-based selection reference in active PRODUCT_SELECTION
        if incoming and getattr(incoming, "text", None):
            selection = await get_graphrag_product_selection(tenant_id, session_id)
            if selection:
                from ai.product_followup import _parse_followup_message
                
                # Fetch dynamically parsed followup details (utilizes DB prompt templates via LLM)
                parsed = await _parse_followup_message(incoming, selection, session_history)
                
                selected_product = parsed.get("selected_product_name")
                if selected_product:
                    print(f"[CONTEXT] Resolved index selection via ProductContextResolver: '{selected_product}'")
                    from db.session_store import save_last_discussed_product
                    import asyncio
                    asyncio.create_task(save_last_discussed_product(tenant_id, session_id, selected_product))
                    return selected_product
        # 1. Active negotiation
        active_neg: Optional[dict]
        if incoming_cached_neg is _NOT_PROVIDED:
            active_neg = await get_negotiation_state(tenant_id, session_id)
        else:
            active_neg = incoming_cached_neg
        if active_neg:
            product = active_neg.get("product_name")
            if product:
                return product

        # 2. Last discussed product
        product = await get_last_discussed_product(tenant_id, session_id)
        if product:
            return product

        # 3. Latest completed order
        try:
            cds = CustomerDataService(tenant_id, session_id)
            product = await cds.get_latest_ordered_product()
            if product:
                return product
        except Exception as e:
            print(f"[CONTEXT] Fallback product lookup from orders failed: {e}")

        return None
