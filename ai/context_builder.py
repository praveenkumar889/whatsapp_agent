# ai/context_builder.py — ContextBuilder v3
#
# ROLE:
#   Orchestrates the assembly of PromptContext by:
#     1. Reading memory_strategy from DB (which memory types for this intent+workflow)
#     2. Querying CustomerDataService to fetch structured database fields
#     3. Resolving product context waterfall via ProductContextResolver
#     4. Constructing and formatting the PromptContext fields
#     5. Caching the result on AIRequestContext
#

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ai.request_context import AIRequestContext, PromptContext
    from db.customer_data_service import CustomerDataService

# ── Strategy cache (in-memory, 5 min TTL) ────────────────────────────────────
_STRATEGY_CACHE: dict = {}
_STRATEGY_TTL   = 300

# ── Hardcoded fallback strategy ───────────────────────────────────────────────
_DEFAULT_STRATEGY: dict[tuple[str, str], list[str]] = {
    ("FAQ_KNOWLEDGE",   "NEGOTIATING"):      ["product_context", "workflow_snapshot", "conversation"],
    ("FAQ_KNOWLEDGE",   "COUNTER_PRESENTED"):["product_context", "workflow_snapshot"],
    ("FAQ_KNOWLEDGE",   "*"):                ["product_context", "conversation"],
    ("WORKFLOW_ACTION", "NEGOTIATING"):      ["workflow_snapshot", "product_context", "negotiation_outcome"],
    ("WORKFLOW_ACTION", "ORDERING"):         ["workflow_snapshot", "product_context", "customer_preference"],
    ("WORKFLOW_ACTION", "BROWSING"):         ["product_context", "customer_preference", "conversation"],
    ("WORKFLOW_ACTION", "*"):                ["workflow_snapshot", "product_context", "conversation"],
    ("NEGOTIATION",     "*"):                ["negotiation_outcome", "workflow_snapshot", "product_context"],
    ("GREETING",        "*"):                ["customer_preference", "conversation"],
    ("HUMAN_ESCALATION","*"):                ["conversation", "workflow_snapshot", "product_context"],
    ("*",               "*"):                ["conversation", "product_context", "workflow_snapshot",
                                             "customer_preference"],
}


def _load_strategy_from_db(tenant_id: str) -> dict:
    """Loads memory_strategy table once per 5 minutes per tenant."""
    cached = _STRATEGY_CACHE.get(tenant_id)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    try:
        from db.session_store import _get_client
        result = (
            _get_client()
            .table("memory_strategy")
            .select("intent,workflow,memory_types,max_results,enabled")
            .eq("tenant_id", tenant_id)
            .eq("enabled", True)
            .order("priority")
            .execute()
        )
        strategy: dict = {}
        for row in (result.data or []):
            row_dict = cast(dict, row)
            key   = (row_dict["intent"], row_dict["workflow"])
            types = [t.strip() for t in cast(str, row_dict["memory_types"]).split(",") if t.strip()]
            strategy[key] = types
        _STRATEGY_CACHE[tenant_id] = (strategy, time.monotonic() + _STRATEGY_TTL)
        return strategy
    except Exception:
        return {}


def _get_memory_types(tenant_id: str, intent: str, workflow: str) -> list[str]:
    """Returns the ordered list of memory types for (intent, workflow)."""
    db   = _load_strategy_from_db(tenant_id)
    combined = {**_DEFAULT_STRATEGY, **db}
    for key in [(intent, workflow), (intent, "*"), ("*", workflow), ("*", "*")]:
        if key in combined:
            return combined[key]
    return _DEFAULT_STRATEGY[("*", "*")]


class ContextBuilder:
    """
    Assembles PromptContext for a request by:
      1. Resolving the active product context via ProductContextResolver
      2. Fetching customer preferences, negotiations, and summary via CustomerDataService
      3. Compiling recent session turns
      4. Stashing the PromptContext in AIRequestContext.llm_context
    """

    def __init__(self, arc: "AIRequestContext"):
        self.arc        = arc
        self.tenant_id  = arc.tenant_id
        self.session_id = arc.session_id

    async def build(self, max_results: int = 3) -> "PromptContext":
        """Builds PromptContext directly from database tables using CustomerDataService."""
        from ai.request_context import PromptContext
        from db.customer_data_service import CustomerDataService
        from db.session_store import get_cached_product_by_name, get_session_history
        from ai.product_context_resolver import ProductContextResolver

        # Check request cache first
        cache_key = f"_ctx_{self.arc.intent}_{self.arc.workflow}"
        if hasattr(self.arc, cache_key):
            return getattr(self.arc, cache_key)

        cds = CustomerDataService(self.tenant_id, self.session_id)

        # 1. Resolve product context and attach to request context envelope
        pname = await ProductContextResolver.resolve(self.tenant_id, self.session_id, self.arc.neg_state)
        self.arc.resolved_product = pname

        # Determine if active product session
        active_product_session = False
        knowledge_state = {
            "available": False,
            "source": "product_cache",
            "cached_at": None,
            "ttl_hours": 24,
            "needs_refresh": True,
            "reason": "cache_missing"
        }
        knowledge_context = {}

        p = None
        if pname:
            from db.session_store import get_last_discussed_product, get_graphrag_product_selection
            last_prod = await get_last_discussed_product(self.tenant_id, self.session_id)
            selection = await get_graphrag_product_selection(self.tenant_id, self.session_id) or []
            selection_names = [(prod.get("product_name") or prod.get("name") or "").lower().strip() if isinstance(prod, dict) else str(prod).lower().strip() for prod in selection]
            neg_product = self.arc.neg_state.get("product_name") if self.arc.neg_state else None
            
            pname_lower = pname.lower().strip()
            is_neg = (neg_product and neg_product.lower().strip() == pname_lower)
            is_last = (last_prod and last_prod.lower().strip() == pname_lower)
            is_select = (pname_lower in selection_names)
            active_product_session = bool(is_neg or is_last or is_select)

            p = await get_cached_product_by_name(self.tenant_id, pname)
            if p:
                cached_at_str = p.get("_cached_at")
                
                # Fetch TTL hours and policy from configurations
                from db.session_store import get_tenant_config
                refresh_policy = await get_tenant_config(self.tenant_id, "knowledge_refresh_policy") or {}
                ttl_hours = refresh_policy.get("ttl_hours", 24)
                
                needs_refresh = False
                reason = None
                if active_product_session:
                    # Within active 20-minute product session, trust the cache
                    needs_refresh = False
                    reason = "active_product_session"
                elif cached_at_str:
                    from datetime import datetime, timezone
                    try:
                        # cached_at is usually ISO format with TZ info, e.g. 2026-07-09T17:00:00+00:00
                        # Replace Z with UTC offset if present
                        dt_str = cached_at_str
                        if dt_str.endswith("Z"):
                            dt_str = dt_str[:-1] + "+00:00"
                        cached_at = datetime.fromisoformat(dt_str)
                        age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600.0
                        if age_hours > ttl_hours:
                            needs_refresh = True
                            reason = "ttl_expired"
                    except Exception as e:
                        print(f"[CONTEXT] Failed to parse cached_at date '{cached_at_str}': {e}")
                else:
                    needs_refresh = True
                    reason = "missing_timestamp"
                    
                knowledge_state = {
                    "available": True,
                    "source": "product_cache",
                    "cached_at": cached_at_str,
                    "ttl_hours": ttl_hours,
                    "needs_refresh": needs_refresh,
                    "reason": reason
                }
                
                # Extract structured details into standard schema
                installation = p.get("installation") or p.get("installation_instructions")
                installation_url = p.get("installation_url") or (installation.get("pdf_url") if isinstance(installation, dict) else "")
                manual_url = p.get("manual_url") or (installation.get("manual_url") if isinstance(installation, dict) else "")
                video_url = p.get("video_url") or (installation.get("video_url") if isinstance(installation, dict) else "")
                
                knowledge_context = {
                    "product": {
                        "metadata": {
                            "brand": p.get("brand") or "",
                            "category": p.get("category") or "",
                            "warranty": p.get("warranty") or p.get("warranty_period") or "",
                            "ip_rating": p.get("ip_rating") or "",
                            "voltage": p.get("voltage") or "",
                            "power": p.get("power") or "",
                        },
                        "assets": {
                            "installation_url": installation_url or "",
                            "manual_url": manual_url or "",
                            "brochure_url": p.get("brochure_url") or "",
                            "images": p.get("images") or ([p.get("image_url")] if p.get("image_url") else []),
                            "videos": p.get("videos") or ([video_url] if video_url else []),
                        },
                        "documents": {
                            "installation": p.get("installation_guide") or p.get("installation_instructions") or "",
                            "manual": p.get("manual") or p.get("user_manual") or "",
                            "brochure": p.get("brochure") or "",
                        },
                        "specifications": p.get("specs") or p.get("specifications") or {},
                        "faq": [{"q": f.get("question"), "a": f.get("answer")} for f in p.get("faqs", [])] if p.get("faqs") else [],
                        "descriptions": {
                            "short": p.get("feature_descriptions") or p.get("short_description") or "",
                            "long": p.get("description") or p.get("long_description") or "",
                        }
                    }
                }

        # 2. Format product details for LLM
        product_str = ""
        if pname and p:
            fmt = getattr(self.arc.incoming, "cb_product_format", None) or "{name} | Rs.{price}"
            product_str = fmt.replace("{name}", str(p.get("product_name") or pname)) \
                             .replace("{price}", str(p.get("price") or 0)) \
                             .replace("{warranty}", str(p.get("warranty") or "N/A")) \
                             .replace("{waterproof}", str(p.get("waterproof") or "N/A"))
            prefix = getattr(self.arc.incoming, "cb_product_marker", "PRODUCT_CONTEXT:")
            product_str = f"{prefix} {product_str}"

        # 3. Format workflow snapshot
        workflow_str = ""
        if self.arc.neg_state:
            state = self.arc.workflow
            fmt_state = getattr(self.arc.incoming, "cb_workflow_format_state", "State: {state}")
            workflow_str = fmt_state.replace("{state}", state)
            prod_name = self.arc.neg_state.get("product_name")
            q = self.arc.neg_state.get("quantity")
            op = self.arc.neg_state.get("offer_price")
            if prod_name and q:
                fmt_prod = getattr(self.arc.incoming, "cb_workflow_format_product", " - {product} x{quantity}")
                workflow_str += fmt_prod.replace("{product}", prod_name).replace("{quantity}", str(q))
            if op:
                fmt_price = getattr(self.arc.incoming, "cb_workflow_format_price", " @ Rs.{offer_price}")
                workflow_str += fmt_price.replace("{offer_price}", str(op))
            prefix = getattr(self.arc.incoming, "cb_workflow_marker", "WORKFLOW_SNAPSHOT:")
            workflow_str = f"{prefix} {workflow_str}"

        # 4. Format preferences
        prefs = await cds.get_customer_preferences()
        prefs_str = ""
        if prefs:
            parts = [f"{k}: {v}" for k, v in prefs.items() if v]
            prefix = getattr(self.arc.incoming, "cb_preferences_prefix", "Preferences - ")
            prefs_str = prefix + ", ".join(parts) if parts else ""

        # 5. Format negotiation summary / profile
        summary = await cds.get_customer_summary()
        neg_str = ""
        avg_disc = summary.get("avg_negotiation_discount_pct")
        if avg_disc is not None:
            neg_str = f"Typically accepts {avg_disc}% discount"
            negs = await cds.get_negotiation_history(limit=10)
            rounds = [int(n["rounds"]) for n in negs if n.get("rounds") is not None]
            prices = [float(n["final_price"]) for n in negs if n.get("accepted") and n.get("final_price") is not None]
            if rounds:
                avg_rounds = round(sum(rounds)/len(rounds), 1)
                neg_str += f" in {avg_rounds} rounds"
            if len(prices) >= 2:
                neg_str += f", budget Rs.{int(min(prices)):,} - Rs.{int(max(prices)):,}"

        # 6. Format recent conversation turns
        conv_str = ""
        try:
            turns = await get_session_history(self.tenant_id, self.session_id, limit=2)
            turns_str = []
            for turn in turns:
                if turn.get("role") == "user" and turn.get("content"):
                    turns_str.append(turn["content"])
            if turns_str:
                conv_str = "\n".join(turns_str)
        except Exception as e:
            print(f"[CONTEXT] Conversation history fetch failed: {e}")

        # 7. Format customer history block if needs_customer_context or history is true
        customer_context = ""
        routing = getattr(self.arc.result, "routing", None)
        if routing and (routing.needs_customer_context or getattr(routing, "needs_customer_history", False)):
            try:
                customer_context = await self._build_customer_context_text(cds, summary)
            except Exception as e:
                print(f"[CONTEXT] Formatting customer_context failed: {e}")

        # Save to request context envelope
        self.arc.customer_context = customer_context

        ctx = PromptContext(
            product_context      = product_str,
            customer_preferences = prefs_str,
            negotiation_profile  = neg_str,
            workflow_context     = workflow_str,
            conversation_summary = conv_str,
            customer_context     = customer_context,
            active_product_session = active_product_session,
            resolved_product     = pname,
            knowledge_state      = knowledge_state,
            knowledge_context    = knowledge_context,
        )


        setattr(self.arc, cache_key, ctx)
        return ctx

    async def _build_customer_context_text(self, cds: "CustomerDataService", summary: dict) -> str:
        """Formats structural customer metrics and order listings into a clean text block."""
        sections = []
        
        pref_parts = []
        prefs = summary.get("preferences", {})
        for k, v in prefs.items():
            if v:
                pref_parts.append(f"{k}: {v}")
        if pref_parts:
            sections.append("Preferences:\n" + "\n".join(f"- {p}" for p in pref_parts))
            
        summary_parts = []
        if summary.get("favorite_category") and summary["favorite_category"] != "N/A":
            summary_parts.append(f"Favorite Category: {summary['favorite_category']}")
        if summary.get("favorite_product") and summary["favorite_product"] != "N/A":
            summary_parts.append(f"Favorite Product: {summary['favorite_product']}")
        if summary.get("total_orders"):
            summary_parts.append(f"Total Orders: {summary['total_orders']}")
            summary_parts.append(f"Total Spent: Rs.{summary['total_spent']:,.2f}")
        if summary.get("last_purchase_date"):
            summary_parts.append(f"Last Purchased At: {summary['last_purchase_date']}")
        if summary.get("avg_negotiation_discount_pct") is not None:
            summary_parts.append(f"Average Negotiation Discount: {summary['avg_negotiation_discount_pct']}%")
        if summary_parts:
            sections.append("Customer Profile Summary:\n" + "\n".join(f"- {s}" for s in summary_parts))

        orders = await cds.get_order_history(limit=5)
        if orders:
            order_list = []
            for o in orders:
                p = o.get("product_name")
                q = o.get("quantity_value")
                pr = o.get("total_price")
                oid = o.get("order_id")
                if p:
                    order_list.append(f"- Order {oid or ''}: {p} x{q or 1} @ Rs.{pr or 0:,.0f}")
            if order_list:
                sections.append("Completed Orders:\n" + "\n".join(order_list))

        negs = await cds.get_negotiation_history(limit=5)
        if negs:
            negotiations = []
            for n in negs:
                p = n.get("product_name")
                q = n.get("quantity")
                acc = n.get("accepted")
                if p:
                    detail = f"- {p}"
                    if q:
                        detail += f" ({q} units)"
                    if acc is False:
                        detail += " - offer not accepted"
                    negotiations.append(detail)
            if negotiations:
                sections.append("Negotiation History:\n" + "\n".join(negotiations))

        offers = await cds.get_offer_history(limit=5)
        if offers:
            offer_list = []
            for o in offers:
                p = o.get("product_name")
                disc = o.get("discount_applied")
                th = o.get("threshold")
                acc = o.get("accepted")
                if p:
                    status = "Accepted" if acc else "Rejected"
                    offer_list.append(f"- Offer for {p}: {disc or 0}% off (Rs.{th or 0:,.0f}/unit) - {status}")
            if offer_list:
                sections.append("Past Offers:\n" + "\n".join(offer_list))

        return "\n\n".join(sections)