# db/customer_data_service.py — Structured Customer Data Service
#
# ROLE:
#   Dedicated service for writing and reading structured customer business data
#   (orders, negotiations, offers, preferences, product views).
#   This replaces the unstructured conversational memories of Mem0.

import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, cast
from db.session_store import _get_client

class CustomerDataService:
    def __init__(self, tenant_id: str, session_id: str):
        self.tenant_id = tenant_id
        self.session_id = session_id

    async def save_product_view(self, product_name: str) -> None:
        """Saves a product view record to Postgres."""
        try:
            row = {
                "tenant_id": self.tenant_id,
                "session_id": self.session_id,
                "product_name": product_name,
                "viewed_at": datetime.now(timezone.utc).isoformat()
            }
            _get_client().table("product_views").insert(row).execute()
        except Exception as e:
            print(f"[CustomerData] save_product_view failed (possibly missing table): {e}")

    async def get_recent_product_views(self, limit: int = 5) -> List[dict]:
        """Fetches recently viewed products from Postgres."""
        try:
            res = _get_client().table("product_views") \
                .select("*") \
                .eq("tenant_id", self.tenant_id) \
                .eq("session_id", self.session_id) \
                .order("viewed_at", desc=True) \
                .limit(limit) \
                .execute()
            return cast(List[dict], res.data or [])
        except Exception as e:
            print(f"[CustomerData] get_recent_product_views failed (possibly missing table): {e}")
            return []

    async def save_preference(self, pref_type: str, value: str) -> None:
        """Upserts a customer preference to Postgres."""
        try:
            row = {
                "tenant_id": self.tenant_id,
                "session_id": self.session_id,
                "pref_type": pref_type,
                "value": value,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            _get_client().table("customer_preferences").upsert(row, on_conflict="tenant_id,session_id,pref_type").execute()
        except Exception as e:
            print(f"[CustomerData] save_preference failed (possibly missing table): {e}")

    async def get_preferences(self) -> Dict[str, str]:
        """Fetches all customer preferences as a dictionary."""
        try:
            res = _get_client().table("customer_preferences") \
                .select("pref_type,value") \
                .eq("tenant_id", self.tenant_id) \
                .eq("session_id", self.session_id) \
                .execute()
            data = cast(List[dict], res.data or [])
            return {str(r["pref_type"]): str(r["value"]) for r in data if r.get("pref_type")}
        except Exception as e:
            print(f"[CustomerData] get_preferences failed (possibly missing table): {e}")
            return {}

    async def save_negotiation_outcome(
        self, product: str, opening_price: float, final_price: float,
        rounds: int, accepted: bool, quantity: int
    ) -> None:
        """Saves negotiation details to Postgres."""
        try:
            row = {
                "tenant_id": self.tenant_id,
                "session_id": self.session_id,
                "product_name": product,
                "initial_price": opening_price,
                "final_price": final_price,
                "rounds": rounds,
                "accepted": accepted,
                "quantity": quantity,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            _get_client().table("negotiation_history").insert(row).execute()
        except Exception as e:
            print(f"[CustomerData] save_negotiation_outcome failed (possibly missing table): {e}")

    async def get_negotiation_history(self, limit: int = 5) -> List[dict]:
        """Fetches past negotiations from Postgres."""
        try:
            res = _get_client().table("negotiation_history") \
                .select("*") \
                .eq("tenant_id", self.tenant_id) \
                .eq("session_id", self.session_id) \
                .order("created_at", desc=True) \
                .limit(limit) \
                .execute()
            return cast(List[dict], res.data or [])
        except Exception as e:
            print(f"[CustomerData] get_negotiation_history failed (possibly missing table): {e}")
            return []

    async def save_offer_history(
        self, product: str, offer_tier: str, discount_applied: float, threshold: float, accepted: bool
    ) -> None:
        """Saves offer details to Postgres."""
        try:
            row = {
                "tenant_id": self.tenant_id,
                "session_id": self.session_id,
                "product_name": product,
                "offer_tier": offer_tier,
                "discount_applied": discount_applied,
                "threshold": threshold,
                "accepted": accepted,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            _get_client().table("customer_offers").insert(row).execute()
        except Exception as e:
            print(f"[CustomerData] save_offer_history failed (possibly missing table): {e}")

    async def get_offer_history(self, limit: int = 5) -> List[dict]:
        """Fetches past offers from Postgres."""
        try:
            res = _get_client().table("customer_offers") \
                .select("*") \
                .eq("tenant_id", self.tenant_id) \
                .eq("session_id", self.session_id) \
                .order("created_at", desc=True) \
                .limit(limit) \
                .execute()
            return cast(List[dict], res.data or [])
        except Exception as e:
            print(f"[CustomerData] get_offer_history failed (possibly missing table): {e}")
            return []

    async def get_order_history(self, limit: int = 5) -> List[dict]:
        """Fetches completed orders from standard orders table."""
        try:
            res = _get_client().table("orders") \
                .select("*") \
                .eq("tenant_id", self.tenant_id) \
                .eq("session_id", self.session_id) \
                .order("created_at", desc=True) \
                .limit(limit) \
                .execute()
            return cast(List[dict], res.data or [])
        except Exception as e:
            print(f"[CustomerData] get_order_history failed: {e}")
            return []

    async def get_latest_order(self) -> Optional[dict]:
        """Fetches the latest completed order from the database."""
        orders = await self.get_order_history(limit=1)
        return orders[0] if orders else None

    async def get_previous_offers(self, limit: int = 5) -> List[dict]:
        """Fetches past offers from Postgres (alias)."""
        return await self.get_offer_history(limit=limit)

    async def get_customer_preferences(self) -> Dict[str, str]:
        """Fetches all customer preferences as a dictionary (alias)."""
        return await self.get_preferences()

    async def get_recent_products(self, limit: int = 5) -> List[dict]:
        """Fetches recently viewed products from Postgres (alias)."""
        return await self.get_recent_product_views(limit=limit)

    async def get_latest_negotiation(self) -> Optional[dict]:
        """Fetches the latest negotiation detail from database."""
        negs = await self.get_negotiation_history(limit=1)
        return negs[0] if negs else None

    async def get_latest_invoice(self) -> Optional[dict]:
        """Fetches the latest completed order invoice from database."""
        try:
            res = _get_client().table("orders") \
                .select("*") \
                .eq("tenant_id", self.tenant_id) \
                .eq("session_id", self.session_id) \
                .not_.is_("invoice_url", "null") \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
            return cast(List[dict], res.data)[0] if res.data else None
        except Exception as e:
            print(f"[CustomerData] get_latest_invoice failed: {e}")
            return None

    async def get_latest_ordered_product(self) -> Optional[str]:
        """Fetches the latest completed order's product name."""
        latest = await self.get_latest_order()
        return latest.get("product_name") if latest else None

    async def get_purchase_history(self, limit: int = 10) -> List[dict]:
        """Fetches completed orders history (alias)."""
        return await self.get_order_history(limit=limit)

    async def get_customer_summary(self) -> dict:
        """Fetches unified customer profile details (preferences, average negotiations, purchase stats)."""
        try:
            prefs = await self.get_preferences()
            negs = await self.get_negotiation_history(limit=10)
            orders = await self.get_order_history(limit=10)
            
            fav_cat = prefs.get("category") or "N/A"
            total_orders = len(orders)
            total_spent = sum(float(o.get("total_price") or 0) for o in orders)
            last_purchase_date = orders[0].get("created_at") if orders else None
            
            prod_counts: dict = {}
            for o in orders:
                p = o.get("product_name")
                if p:
                    prod_counts[p] = prod_counts.get(p, 0) + 1
            fav_prods = sorted(prod_counts.items(), key=lambda x: x[1], reverse=True)
            fav_prod = fav_prods[0][0] if fav_prods else "N/A"

            avg_discount = None
            if negs:
                accepted = [n for n in negs if n.get("accepted")]
                if accepted:
                    discounts = []
                    for n in accepted:
                        init = float(n.get("initial_price") or 0)
                        final = float(n.get("final_price") or 0)
                        if init > 0:
                            discounts.append((init - final) / init * 100)
                    if discounts:
                        avg_discount = round(sum(discounts) / len(discounts), 1)
            
            return {
                "preferences": prefs,
                "favorite_category": fav_cat,
                "favorite_product": fav_prod,
                "total_orders": total_orders,
                "total_spent": total_spent,
                "last_purchase_date": last_purchase_date,
                "avg_negotiation_discount_pct": avg_discount,
                "total_negotiations": len(negs),
            }
        except Exception as e:
            print(f"[CustomerData] get_customer_summary failed: {e}")
            return {}

