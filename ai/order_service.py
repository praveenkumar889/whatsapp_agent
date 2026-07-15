# ai/order_service.py — Order completion (create + persist product memory)
#
# Both router.py (_confirm_negotiated_order) and invoice_handler.py (the
# "commit pending order" fallback) need to do the exact same two things
# whenever an order is actually created: write the order row, and save
# product_context to Mem0 so "what did I order before?" has something real
# to find regardless of which conversational path got the customer there.
#
# This is intentionally NOT an event bus — with exactly two call sites,
# that would be solving a problem that doesn't exist yet. If a third
# order-creation path appears later (a web integration, a direct API),
# that's the point to reconsider — not before.
#
# Callers still own their own path-specific cleanup (clearing negotiation
# state vs. deleting a pending order, saving a negotiation outcome) since
# those genuinely differ between paths and don't belong here.

import asyncio
from typing import Any, Optional


class OrderResult:
    """
    Wraps create_order()'s return dict rather than replacing it with a
    strict dataclass — that dict is a full DB row (shape varies by tenant
    schema) plus whatever extra_fields each caller merges in, so a fixed
    dataclass would either reject unexpected fields or need to enumerate
    every possible one. Every existing .get(key) / result["key"] call
    (6+ in invoice_handler.py alone) keeps working unchanged via
    delegation to the underlying dict. New code can additionally use
    .order_id / .saved_to_memory — the one genuinely new piece of
    information complete_order() knows that create_order() doesn't.
    """
    def __init__(self, data: dict, saved_to_memory: bool):
        self._data = data
        self.saved_to_memory = saved_to_memory

    @property
    def order_id(self) -> Optional[str]:
        return self._data.get("order_id")

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __bool__(self) -> bool:
        return bool(self._data)

    def __repr__(self) -> str:
        return f"OrderResult(order_id={self.order_id!r}, saved_to_memory={self.saved_to_memory})"


async def complete_order(
    tenant_id: str, session_id: str, sender_name: str,
    items: list, gst_rate: float = 0.18, extra_fields: Optional[dict] = None,
    shipping_address: Optional[str] = None, status: str = "CONFIRMED",
) -> Optional[OrderResult]:
    """
    Creates the order and saves product_context, order_history, and offer_history
    to Mem0 (fire-and-forget). Returns an OrderResult (or None on failure).
    """
    from db.product_store import create_order

    new_order = await create_order(
        tenant_id=tenant_id, session_id=session_id, sender_name=sender_name,
        items=items, gst_rate=gst_rate, extra_fields=extra_fields,
        shipping_address=shipping_address, status=status,
    )

    if not new_order:
        return None

    saved_to_memory = False
    if items:
        try:


            _product_name = items[0].get("product_name")

            async def _save_order_context_async():
                from db.customer_data_service import CustomerDataService
                cds = CustomerDataService(tenant_id, session_id)
                
                # 1. Save product view for follow-up QA
                await cds.save_product_view(_product_name)

                # 2. Save offer history to database (if a discount or negotiation happened)
                qty_val = int(new_order.get("quantity_value") or items[0].get("quantity_value") or 1)
                unit_pr = float(new_order.get("unit_price") or items[0].get("unit_price") or 0.0)
                disc_pct = int(new_order.get("store_discount_pct") or 0)
                neg_disc = float(new_order.get("negotiation_discount_amount") or 0.0)
                was_neg = neg_disc > 0.0

                if disc_pct > 0 or was_neg:
                    orig_amount = float(new_order.get("original_amount") or (unit_pr * qty_val))
                    await cds.save_offer_history(
                        product          = _product_name,
                        offer_tier       = "negotiated" if was_neg else "store_offer",
                        discount_applied = disc_pct,
                        threshold        = orig_amount,
                        accepted         = True,
                    )

            asyncio.create_task(_save_order_context_async())
            saved_to_memory = True
        except Exception as e:
            print(f"[ORDER_SERVICE] save memory context failed (non-critical): {e}")

    return OrderResult(new_order, saved_to_memory)
