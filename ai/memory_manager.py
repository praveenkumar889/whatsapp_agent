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
}

# ── Importance weights for ranking ────────────────────────────────────────────
_IMPORTANCE = {
    "workflow_snapshot":   1.0,   # most important — current session
    "product_context":     0.9,   # very important — current product
    "negotiation_outcome": 0.8,   # important for personalisation
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
        """Low-level Mem0 search with correct filter format."""
        client = self._client()
        if not client:
            return []
        try:
            res = client.search(
                query  = query,
                limit  = limit,
                filters= {"user_id":   self.session_id,
                          "agent_id":  self.tenant_id,
                          "metadata":  {"type": memory_type}},
            )
            return res if isinstance(res, list) else []
        except Exception as e:
            print(f"[MEM] search failed ({memory_type}): {e}")
            return []

    def _save_raw(self, content: str, memory_type: str, extra_meta: dict = {}) -> None:
        """Low-level Mem0 save."""
        client = self._client()
        if not client:
            return
        try:
            meta = {"type": memory_type, "saved_at": int(time.time()),
                    **extra_meta}
            client.add(
                messages = [{"role": "system", "content": content}],
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