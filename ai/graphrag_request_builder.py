# ai/graphrag_request_builder.py — GraphRAGRequestBuilder
#
# ROLE:
#   Decides if GraphRAG is needed, transforms the user query, and enriches it
#   with current product, category, and Mem0 history (preferences & purchases).

import re
import json
from typing import Optional, Any, List, Dict
from db.session_store import get_graphrag_product_selection, get_last_discussed_product, get_category_selection
from db.customer_data_service import CustomerDataService

class GraphRAGRequestBuilder:
    """
    Builds the optimized payload for GraphRAG queries.
    Implements rules 1-16 for multi-tenant query enrichment.
    """

    def __init__(self, incoming: Any, session_history: Optional[List[Dict]] = None):
        self.incoming = incoming
        self.session_history = session_history or []
        self.tenant_id = incoming.tenant_id
        self.session_id = incoming.session_id

    async def build_payload(self) -> Dict[str, Any]:
        """
        Determines query type and builds the enriched JSON payload.
        Transforms the query text to incorporate context (pronoun resolution, Mem0).
        """
        original_query = self.incoming.text.strip()
        transformed_query = original_query

        # 1. Resolve quoted replies
        if original_query.startswith("[Quoting:") and "\n" in original_query:
            transformed_query = original_query.split("\n", 1)[1].strip()

        # 2. Fetch context from DB/Mem0
        selection = await get_graphrag_product_selection(self.tenant_id, self.session_id) or []
        from ai.product_context_resolver import ProductContextResolver
        last_product = await ProductContextResolver.resolve(self.tenant_id, self.session_id, self.incoming._cached_neg_state)
        category_options = await get_category_selection(self.tenant_id, self.session_id) or []


        # 3. Fetch customer profile for recommendation/personalization checks
        cds = CustomerDataService(self.tenant_id, self.session_id)
        profile = await cds.get_customer_summary()
        prefs = profile.get("preferences") or {}
        preferences = list(prefs.values())

        # Retrieve previous products from order history
        previous_products = []
        try:
            orders = await cds.get_order_history(limit=5)
            for o in orders:
                p_name = o.get("product_name")
                if p_name and p_name not in previous_products:
                    previous_products.append(p_name)
        except Exception:
            pass

        # ── QUERY RESOLUTION & TRANSFORMATION RULES ──

        # Rule 13: Category Clarification
        if category_options and original_query.isdigit():
            idx = int(original_query) - 1
            if 0 <= idx < len(category_options):
                transformed_query = f"Show products in {category_options[idx]}"

        # Rule 3: Product Comparison ("compare 1,2" or "compare Romi and Reva")
        elif "compare" in transformed_query.lower():
            nums = re.findall(r'\b\d+\b', transformed_query)
            if nums and len(selection) >= max(int(n) for n in nums):
                resolved = [
                    selection[int(n) - 1].get("product_name") or selection[int(n) - 1].get("name")
                    for n in nums
                ]
                transformed_query = f"Compare {' vs '.join(resolved)}"

        # Rule 14 / Rule 4: Number Selection (e.g. "11" or "1")
        elif transformed_query.isdigit() and len(selection) >= int(transformed_query):
            idx = int(transformed_query) - 1
            p_name = selection[idx].get("product_name") or selection[idx].get("name")
            transformed_query = str(p_name)

        # Rule 4: Personalized Recommendation
        elif any(k in transformed_query.lower() for k in ["recommend", "suggest", "usually buy", "based on my"]):
            history_str = ", ".join(previous_products) if previous_products else "None"
            pref_str = ", ".join(preferences) if preferences else "None"
            transformed_query = f"Customer history: {history_str}. Preferences: {pref_str}. Recommend similar products from the current catalog."

        # Rule 5 / Rule 15: Similar Product & Pronoun Resolution ("it", "this", "that")
        elif last_product and any(p in f" {transformed_query.lower()} " for p in [" it ", " this ", " that ", " same ", " previous "]):
            transformed_query = f"Product: {last_product}. Question: {transformed_query}"

        # Rule 12: Product Follow-up questions (waterproof, warranty, installation)
        elif last_product and any(k in transformed_query.lower() for k in ["waterproof", "warranty", "install", "dimension", "size", "color"]):
            transformed_query = f"Product: {last_product}. Question: {transformed_query}"

        # Rule 1: Catalog Discovery (if category matched but query was short)
        elif any(k in transformed_query.lower() for k in ["outdoor lights", "garden lights", "led bulb"]):
            transformed_query = f"Show products under {transformed_query}"

        # 4. Construct payload
        payload = {
            "id":                  self.incoming.message_id,
            "tenant_id":           self.tenant_id,
            "message_id":          self.incoming.message_id,
            "session_id":          self.session_id,
            "channel":             self.incoming.channel,
            "timestamp_unix":      self.incoming.timestamp,
            "region":              self.incoming.region,
            "original_type":       self.incoming.original_type,
            "text":                transformed_query,
            "intent":              self.incoming.captured_replies[0]["type"].upper() if getattr(self.incoming, "captured_replies", None) else "FAQ_KNOWLEDGE",
            "confidence":          0.95,
            "product_name":        last_product,
            "quantity_value":      None,
            "quantity_unit":       None,
            "delivery_date":       None,
            "missing_entities":    [],
            "reply_text":          None,
            "replied_at":          None,
            "sender_name":         self.incoming.sender_name,
            "sender_phone_number": self.incoming.sender_phone,
            "trace_id":            self.incoming.trace_id,
            "received_at":         self.incoming.received_at,
            "direction":           "inbound",
            "invoice_number":      None,
            "payment_reference":   None,
            # Structured context parameters for enriched queries (Rule 16)
            "current_product":     last_product,
            "previous_products":   previous_products,
            "customer_preferences": preferences,
            "workflow":            "BROWSING"
        }

        print(f"[GraphRAGRequestBuilder] Original: '{original_query}' -> Transformed: '{transformed_query}'")
        return payload
