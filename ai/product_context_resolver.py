# ai/product_context_resolver.py — Resolves active product name from session/history
from typing import Optional
from db.session_store import get_negotiation_state, get_last_discussed_product
from db.customer_data_service import CustomerDataService

class ProductContextResolver:
    @staticmethod
    async def resolve(tenant_id: str, session_id: str, incoming_cached_neg: Optional[dict] = None) -> Optional[str]:
        """
        Resolves active product name using a waterfall priority:
        1. Active negotiation state
        2. Last discussed product from active session
        3. Latest completed order product from customer history
        """
        # 1. Active negotiation
        active_neg = incoming_cached_neg or await get_negotiation_state(tenant_id, session_id)
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
