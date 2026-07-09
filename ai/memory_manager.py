# ai/memory_manager.py — MemoryManager
#
# ROLE:
#   Single layer that owns HOW memories are stored, searched, ranked,
#   expired, and retrieved. ContextBuilder calls MemoryManager — it never
#   touches Mem0 or Postgres directly.
#
# WHY THIS EXISTS:
#   Previously ContextBuilder mixed Mem0 search calls with JSON parsing
#   and ranking logic. If you replace Mem0 with another provider (Zep,
#   LangChain Memory, Redis Semantic Cache), only MemoryManager changes.
#
# RESPONSIBILITIES:
#   ✅ Store memories (product_context, preferences, negotiation_outcome, etc.)
#   ✅ Search memories with intent-appropriate queries
#   ✅ Rank memories (importance × recency × confidence)
#   ✅ Enforce TTL (workflow snapshots expire in 30 min, preferences in 1 year)
#   ✅ Build customer profile summaries
#   ❌ NOT responsible for business logic (that's in handlers)
#   ❌ NOT responsible for prompt building (that's in PromptBuilder)

from __future__ import annotations

import json
import time
from typing import Optional

# ── TTL constants (seconds) ───────────────────────────────────────────────────
TTL = {
    "workflow_snapshot":   30  * 60,          # 30 minutes
    "product_context":     2   * 60 * 60,     # 2 hours
    "conversation":        30  * 24 * 60 * 60,# 30 days
    "negotiation_outcome": 90  * 24 * 60 * 60,# 90 days
    "customer_preference": 365 * 24 * 60 * 60,# 1 year
    "purchase_history":    None,               # forever
    "order_history":       None,               # forever
    "offer_history":       90  * 24 * 60 * 60,# 90 days
}

# ── Importance weights for ranking ────────────────────────────────────────────
_IMPORTANCE = {
    "workflow_snapshot":   1.0,   # most important — current session
    "product_context":     0.9,   # very important — current product
    "negotiation_outcome": 0.8,   # important for personalisation
    "order_history":       0.85,  # completed orders
    "offer_history":       0.75,  # historical offer records
    "customer_preference": 0.7,   # useful for personalisation
    "conversation":        0.5,   # background context
}


class MemoryManager:
    """
    Owns all memory storage and retrieval operations.
    Abstracts the underlying provider (currently Mem0).

    Usage:
        mm = MemoryManager(tenant_id, session_id)
        await mm.save_product(product_dict)
        memories = await mm.search(["product_context", "workflow_snapshot"],
                                   query="warranty outdoor light",
                                   max_results=3)
        profile = await mm.get_customer_profile()
    """

    def __init__(self, tenant_id: str, session_id: str):
        self.tenant_id  = tenant_id
        self.session_id = session_id

    def _client(self):
        from db.memory_store import _get_client
        return _get_client()

    def _search_raw(self, query: str, memory_type: str, limit: int) -> list:
        """
        Low-level Mem0 search. Filters by user_id only — agent_id is NOT
        persisted by this Mem0 SDK version (confirmed in db/memory_store.py's
        module comment), so agent_id filtering silently matches nothing.
        Tenant isolation instead comes from the text-prefix convention
        already used elsewhere in this codebase (db/memory_store.py) —
        every save here embeds "T:{tenant}|U:{session}|" in the content,
        and results are filtered/stripped of that prefix here too.

        IMPORTANT: Do NOT add a metadata filter here. The Mem0 SDK's compound
        filter {"user_id": ..., "metadata": {"type": ...}} silently returns
        raw=0 — the same SDK quirk documented in db/memory_store.py. We
        post-filter by memory type in Python using keyword prefixes instead.
        """
        from db.memory_store import _matches_tenant, _strip_tenant_prefix

        # Maps MemoryManager memory_type names → the keyword prefix written
        # at the start of the content string by each _save_raw() call.
        # conversation turns have no structured prefix (they're raw user/bot text).
        _TYPE_TO_PREFIX: dict = {
            "product_context":     "PRODUCT_CONTEXT:",
            "workflow_snapshot":   "WORKFLOW_SNAPSHOT:",
            "negotiation_outcome": "NEG_OUTCOME:",
            "customer_preference": "CUSTOMER_PREF:",
            "order_history":       "ORDER_HISTORY:",
            "offer_history":       "OFFER_HISTORY:",
            "conversation":        None,   # no structured keyword prefix
        }

        client = self._client()
        if not client:
            print(f"[MEM] _search_raw({memory_type}): no Mem0 client available")
            return []
        try:
            res = client.search(
                query  = query,
                limit  = limit * 3,  # over-fetch since tenant filtering happens after
                filters= {"user_id": self.session_id},
            )
            raw = res if isinstance(res, list) else []
            required_prefix = _TYPE_TO_PREFIX.get(memory_type)
            result = []
            for r in raw:
                if not isinstance(r, dict):
                    continue
                mem_text = r.get("memory", "")
                if not _matches_tenant(mem_text, self.tenant_id, self.session_id):
                    continue
                stripped = _strip_tenant_prefix(mem_text)
                # Enforce memory-type keyword prefix for structured types;
                # conversation turns pass through (required_prefix is None).
                if required_prefix is not None and not stripped.startswith(required_prefix):
                    continue
                r = {**r, "memory": stripped}
                result.append(r)
            result = result[:limit]
            print(f"[MEM] _search_raw({memory_type}) tenant={self.tenant_id} session={self.session_id} "
                  f"query='{query[:40]}' -> raw={len(raw)} tenant_matched={len(result)}")
            return result
        except Exception as e:
            print(f"[MEM] search failed ({memory_type}): {e}")
            return []

    def _save_raw(self, content: str, memory_type: str, extra_meta: dict = {}) -> None:
        """Low-level Mem0 save. Embeds the tenant/session text prefix —
        see _search_raw()'s docstring for why agent_id alone isn't enough."""
        from db.memory_store import _add_tenant_prefix

        client = self._client()
        if not client:
            return
        try:
            meta = {"type": memory_type, "saved_at": int(time.time()),
                    **extra_meta}
            prefixed_content = _add_tenant_prefix(content, self.tenant_id, self.session_id)
            client.add(
                messages = [{"role": "system", "content": prefixed_content}],
                user_id  = self.session_id,
                agent_id = self.tenant_id,
                metadata = meta,
            )
        except Exception as e:
            print(f"[MEM] save failed ({memory_type}): {e}")

    def _rank(self, memories: list, mem_type: str) -> list:
        """
        Ranks memories by: importance_weight × recency × confidence.
        More recent and higher-importance memories score higher.
        Returns sorted list, highest score first.
        """
        importance = _IMPORTANCE.get(mem_type, 0.5)
        now        = time.time()

        def _score(r: dict) -> float:
            saved_at   = r.get("metadata", {}).get("saved_at", now)
            age_hours  = max((now - float(saved_at)) / 3600, 0.01)
            recency    = 1.0 / (1.0 + age_hours / 24)   # decay over 24h
            confidence = float(r.get("score", 0.8) or 0.8)
            return importance * recency * confidence

        return sorted(memories, key=_score, reverse=True)

    def _deduplicate(self, memories: list) -> list:
        """
        Removes near-duplicate memories using simple token overlap.
        'Customer likes warm white' and 'Customer prefers warm white'
        are considered duplicates and only one is kept.
        Threshold: >70% word overlap = duplicate.
        """
        if len(memories) <= 1:
            return memories
        unique: list = []
        seen_words: list[set] = []
        for r in memories:
            text  = r.get("memory", "").lower()
            words = set(text.split())
            is_dup = False
            for prev_words in seen_words:
                if not prev_words:
                    continue
                overlap = len(words & prev_words) / max(len(words | prev_words), 1)
                if overlap > 0.70:
                    is_dup = True
                    break
            if not is_dup:
                unique.append(r)
                seen_words.append(words)
        return unique

    def _is_expired(self, r: dict, mem_type: str) -> bool:
        """Returns True if memory has exceeded its TTL."""
        ttl = TTL.get(mem_type)
        if ttl is None:
            return False
        saved_at = r.get("metadata", {}).get("saved_at", 0)
        return (time.time() - float(saved_at)) > ttl

    async def search(
        self,
        memory_types: list[str],
        query:        str,
        max_results:  int = 3,
    ) -> dict[str, list[dict]]:
        """
        Searches multiple memory types in parallel.
        Returns dict of {memory_type: [ranked, non-expired memories]}.
        Filters out expired memories automatically.
        """
        import asyncio
        import concurrent.futures

        loop = asyncio.get_event_loop()

        async def _fetch(mem_type: str) -> tuple[str, list]:
            raw = await loop.run_in_executor(
                None, lambda: self._search_raw(query, mem_type, max_results * 2)
            )
            # 1. Filter expired
            valid = [r for r in raw if not self._is_expired(r, mem_type)]
            # 2. Filter low-confidence (Mem0 score < 0.5 means weak semantic match)
            valid = [r for r in valid if float(r.get("score", 1.0) or 1.0) >= 0.5]
            # 3. Deduplicate — removes near-identical memories before ranking
            valid = self._deduplicate(valid)
            # 4. Rank by importance × recency × confidence
            ranked = self._rank(valid, mem_type)[:max_results]
            if raw and not ranked:
                print(f"[MEM] {mem_type}: {len(raw)} raw -> 0 after filtering "
                      f"(expired/low-confidence/dedup — check scores: "
                      f"{[r.get('score') for r in raw]})")
            return mem_type, ranked

        results = await asyncio.gather(*[_fetch(t) for t in memory_types],
                                       return_exceptions=True)
        output: dict[str, list[dict]] = {}
        for item in results:
            if isinstance(item, tuple):
                mem_type, ranked = item
                output[mem_type] = ranked
        return output

    # ── Save methods ──────────────────────────────────────────────────────────

    async def save_product_context(self, product: dict) -> None:
        """Saves current product as structured memory for follow-up Q&A."""
        import asyncio, concurrent.futures
        ctx = {
            "type":        "product_context",
            "name":        str(product.get("product_name") or product.get("name", "")),
            "sku":         str(product.get("sku", "")),
            "price":       str(product.get("list_price") or product.get("price", "")),
            "warranty":    str(product.get("warranty", "")),
            "waterproof":  "Yes" if "waterproof" in str(
                               product.get("feature_descriptions", "")).lower() else "check specs",
            "material":    _extract_material(str(product.get("feature_descriptions", ""))),
            "category":    str(product.get("category", "")),
        }
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: self._save_raw(
                f"PRODUCT_CONTEXT: {json.dumps(ctx)}", "product_context"
            )
        )
        print(f"[MEM] Saved product_context: {ctx['name']}")

    async def save_workflow_snapshot(
        self, state: str, product: str = "",
        quantity: int = 0, offer_price: float = 0, neg_round: int = 0,
    ) -> None:
        """Saves current workflow state after each major transition."""
        import asyncio
        snap = {"type": "workflow_snapshot", "state": state,
                "product": product, "quantity": quantity,
                "offer_price": offer_price, "neg_round": neg_round}
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: self._save_raw(
                f"WORKFLOW_SNAPSHOT: {json.dumps(snap)}", "workflow_snapshot"
            )
        )

    async def save_preference(self, pref_type: str, value: str,
                               confidence: float = 0.8) -> None:
        """Saves a detected customer preference (long-term, 1 year TTL)."""
        import asyncio
        pref = {"type": "customer_preference", "pref_type": pref_type,
                "value": value, "confidence": confidence}
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: self._save_raw(
                f"CUSTOMER_PREF: {json.dumps(pref)}", "customer_preference",
                {"pref_type": pref_type}
            )
        )
        print(f"[MEM] Saved preference: {pref_type}={value}")

    async def save_negotiation_outcome(
        self, product: str, opening_price: float, final_price: float,
        rounds: int, accepted: bool, quantity: int,
    ) -> None:
        """Saves negotiation outcome for building negotiation profile."""
        import asyncio
        disc = round((opening_price - final_price) / opening_price * 100, 1) if opening_price else 0
        outcome = {"type": "negotiation_outcome", "product": product,
                   "opening_price": opening_price, "final_price": final_price,
                   "discount_pct": disc, "rounds": rounds,
                   "accepted": accepted, "quantity": quantity}
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: self._save_raw(
                f"NEG_OUTCOME: {json.dumps(outcome)}", "negotiation_outcome"
            )
        )
        print(f"[MEM] Saved negotiation outcome: {disc}% discount, accepted={accepted}")

    async def save_order_history(
        self, product: str, quantity: int, price: float, order_id: str,
        discount_pct: int = 0, negotiated: bool = False
    ) -> None:
        """Saves a completed order to structured long-term memory."""
        import asyncio
        from datetime import datetime, timezone
        order_meta = {
            "type": "order_history",
            "product": product,
            "quantity": quantity,
            "price": price,
            "order_id": order_id,
            "discount_pct": discount_pct,
            "negotiated": negotiated,
            "date": datetime.now(timezone.utc).isoformat()
        }
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: self._save_raw(
                f"ORDER_HISTORY: {json.dumps(order_meta)}", "order_history"
            )
        )
        print(f"[MEM] Saved order history: {order_id} — {product} x {quantity}")

    async def save_offer_history(
        self, product: str, store_offer_pct: int, negotiated_price: float,
        offer_threshold: float, accepted: bool
    ) -> None:
        """Saves an offer outcome to structured long-term memory."""
        import asyncio
        offer_meta = {
            "type": "offer_history",
            "product": product,
            "store_offer_pct": store_offer_pct,
            "negotiated_price": negotiated_price,
            "offer_threshold": offer_threshold,
            "accepted": accepted,
            "date": str(int(time.time()))
        }
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: self._save_raw(
                f"OFFER_HISTORY: {json.dumps(offer_meta)}", "offer_history"
            )
        )
        print(f"[MEM] Saved offer history: {product} discount={store_offer_pct}% accepted={accepted}")

    async def save_conversation_turn(self, user_text: str, bot_reply: str) -> None:
        """Saves a conversation turn (30-day TTL)."""
        client = self._client()
        if client:
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: client.add(
                    messages = [{"role": "user",      "content": user_text},
                                {"role": "assistant", "content": bot_reply}],
                    user_id  = self.session_id,
                    agent_id = self.tenant_id,
                    metadata = {"type": "conversation",
                                "saved_at": int(time.time())},
                )
            )

    # ── Profile builders ──────────────────────────────────────────────────────

    async def get_customer_profile(self) -> dict:
        """
        Returns a unified customer profile dict by aggregating preferences
        and negotiation outcomes from Mem0.
        Returned dict is injected directly into prompt variables.
        """
        results = await self.search(
            ["customer_preference", "negotiation_outcome"],
            query = "customer preferences budget negotiation",
            max_results = 10,
        )

        # Parse preferences
        prefs: dict = {}
        for r in results.get("customer_preference", []):
            text = r.get("memory", "")
            if "CUSTOMER_PREF:" in text:
                try:
                    p = json.loads(text.split("CUSTOMER_PREF:", 1)[1].strip())
                    prefs[str(p.get("pref_type",""))] = str(p.get("value",""))
                except Exception:
                    pass

        # Build negotiation profile from outcomes
        outcomes = []
        for r in results.get("negotiation_outcome", []):
            text = r.get("memory", "")
            if "NEG_OUTCOME:" in text:
                try:
                    outcomes.append(json.loads(text.split("NEG_OUTCOME:", 1)[1].strip()))
                except Exception:
                    pass

        neg_profile: dict = {}
        if outcomes:
            accepted = [o for o in outcomes if o.get("accepted")]
            discounts= [o["discount_pct"] for o in accepted if "discount_pct" in o]
            prices   = [o["final_price"]  for o in accepted if "final_price"   in o]
            rounds   = [o["rounds"]       for o in outcomes  if "rounds"        in o]
            neg_profile = {
                "avg_discount_pct": round(sum(discounts)/len(discounts), 1) if discounts else None,
                "typical_rounds":   round(sum(rounds)/len(rounds), 1)    if rounds    else None,
                "budget_range":     f"Rs.{int(min(prices)):,}–Rs.{int(max(prices)):,}" if len(prices) >= 2 else None,
                "total_orders":     len(accepted),
            }

        return {"preferences": prefs, "negotiation": neg_profile}


def _extract_material(features: str) -> str:
    fl = features.lower()
    for mat in ["aluminum", "aluminium", "stainless steel", "plastic", "iron"]:
        if mat in fl:
            return mat.title()
    return "check specs"


def build_memory_context_text(results: dict) -> str:
    """
    Turns MemoryManager.search()'s {memory_type: [memory_dicts]} result into
    grouped, labeled sections instead of a flat bullet list — easier for any
    downstream prompt (GraphRAG query builder, memory-only answer, future
    handlers) to work with than raw concatenated memory strings. Lives here,
    not in any one handler, so it's reusable across all of them — Mem0
    formatting is not GraphRAG-specific or router-specific.

    Each memory's raw "memory" field carries a TYPE_PREFIX: {json} format
    (see the save_* methods above) — parsed here to extract the actual
    field worth surfacing, not just the storage encoding.
    """
    import json as _json

    products, categories, preferences, negotiations, orders, offers = [], [], [], [], [], []

    for mem_type, items in (results or {}).items():
        for item in items:
            raw = (item or {}).get("memory", "")
            if not raw:
                continue
            _, _, json_part = raw.partition(":")
            try:
                data = _json.loads(json_part.strip())
            except Exception:
                data = {}

            if mem_type == "product_context":
                name = data.get("name")
                if name:
                    products.append(name)
            elif mem_type == "customer_preference":
                pref_type = data.get("pref_type", "")
                value     = data.get("value")
                if value:
                    (categories if pref_type == "category" else preferences).append(str(value))
            elif mem_type == "negotiation_outcome":
                product  = data.get("product")
                qty      = data.get("quantity")
                accepted = data.get("accepted")
                if product:
                    detail = f"{product}"
                    if qty:
                        detail += f" ({qty} units)"
                    if accepted is False:
                        detail += " — offer not accepted"
                    negotiations.append(detail)
            elif mem_type == "order_history":
                product = data.get("product")
                qty = data.get("quantity")
                price = data.get("price")
                oid = data.get("order_id")
                if product:
                    orders.append(f"Order {oid or ''}: {product} x{qty or 1} @ Rs.{price or 0:,.0f}")
            elif mem_type == "offer_history":
                product = data.get("product")
                disc = data.get("store_offer_pct")
                price = data.get("negotiated_price")
                accepted = data.get("accepted")
                if product:
                    status = "Accepted" if accepted else "Rejected"
                    offers.append(f"Offer for {product}: {disc or 0}% off (Rs.{price or 0:,.0f}/unit) - {status}")

    sections = []
    if products:
        sections.append("Previous Products:\n" + "\n".join(f"- {p}" for p in products))
    if categories:
        sections.append("Preferred Categories:\n" + "\n".join(f"- {c}" for c in categories))
    if preferences:
        sections.append("Preferences:\n" + "\n".join(f"- {p}" for p in preferences))
    if negotiations:
        sections.append("Negotiation History:\n" + "\n".join(f"- {n}" for n in negotiations))
    if orders:
        sections.append("Completed Orders:\n" + "\n".join(f"- {o}" for o in orders))
    if offers:
        sections.append("Past Offers:\n" + "\n".join(f"- {o}" for o in offers))

    return "\n\n".join(sections)