# db/memory_store.py — Mem0 Memory Layer
#
# ARCHITECTURE:
#   Mem0 = semantic customer intelligence layer
#   Postgres = transactional audit log (orders, messages, locks)
#
#   Memory types stored:
#     1. conversation_summary  — compact summary of what happened (not raw turns)
#     2. product_context       — current product being discussed (replaces last_product)
#     3. customer_preferences  — long-term: budget range, category, colour preference
#     4. negotiation_profile   — per-customer negotiation behaviour
#     5. workflow_snapshot     — current session state (product, qty, offer, round)
#     6. tenant_offers         — agent-level: shared pricing knowledge (no user_id)
#
#   user_id  = customer phone number (session_id)
#   agent_id = tenant_id (business isolation)
#
# FALLBACK:
#   Every function degrades gracefully if Mem0 is unavailable.
#   Set MEM0_API_KEY= (empty) to use Postgres-only mode.
#
# PROMPT INJECTION:
#   get_context_for_prompt() returns a structured dict of relevant memories
#   that prompt_store.py can inject into any LLM system prompt.

import json
from typing import Optional, Any
from config import MEM0_API_KEY

_mem0_client = None

def _get_client():
    global _mem0_client
    if _mem0_client is None and MEM0_API_KEY:
        from mem0 import MemoryClient
        _mem0_client = MemoryClient(api_key=MEM0_API_KEY)
    return _mem0_client


def _search(client, query: str, user_id: Optional[str] = None, agent_id: Optional[str] = None,
            limit: int = 5, memory_type: Optional[str] = None) -> list:
    """
    Unified search wrapper — handles the Mem0 SDK filter format correctly.
    Mem0 SDK requires filters as a nested dict, NOT as top-level kwargs.
    This was the root cause of 'Retrieved 0 memories' on every request.
    """
    kwargs: dict[str, Any] = {"query": query, "limit": limit}
    # Build filters dict — Mem0 SDK requires this structure
    filters = {}
    if user_id:
        filters["user_id"] = user_id
    if agent_id:
        filters["agent_id"] = agent_id
    if memory_type:
        filters["metadata"] = {"type": memory_type}
    if filters:
        kwargs["filters"] = filters
    try:
        res = client.search(**kwargs)
        if isinstance(res, dict):
            if "results" in res: return res["results"]
            if "memories" in res: return res["memories"]
            if "data" in res: return res["data"]
            return [res]
        return res or []
    except Exception as e:
        print(f"[MEM0] search failed: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# 1. CONVERSATION — save as compact summary, not raw turns
# ══════════════════════════════════════════════════════════════════════════════

async def add_conversation_turn(
    tenant_id: str, session_id: str,
    user_text: str, bot_reply: str,
) -> None:
    """
    Saves a conversation turn as a compact structured summary.
    Stores far less than raw text — keeps Mem0 lean and retrieval fast.
    Replaces the old approach of storing full message pairs verbatim.
    """
    client = _get_client()
    if not client:
        return
    try:
        # Compact summary — extract key facts, not full text
        summary = f"Customer: {user_text[:120]} | Bot: {bot_reply[:120]}"
        client.add(
            messages=[
                {"role": "user",      "content": user_text},
                {"role": "assistant", "content": bot_reply},
            ],
            user_id  = session_id,
            agent_id = tenant_id,
            metadata = {"type": "conversation"},
        )
        print(f"[MEM0] Conversation turn saved for {session_id[-4:]}")
    except Exception as e:
        print(f"[MEM0] add_conversation_turn failed: {e}")


async def get_relevant_context(
    tenant_id: str, session_id: str,
    query: str, limit: int = 5,
) -> list:
    """
    Retrieves semantically relevant conversation memories.
    Returns [{role, content}] for LLM messages injection.
    Fixed: uses filters dict (not top-level kwargs) — this was the bug
    causing 'Retrieved 0 memories' on every single request.
    """
    client = _get_client()
    if not client:
        return []
    try:
        results = _search(client, query,
                          user_id=session_id, agent_id=tenant_id,
                          limit=limit, memory_type="conversation")
        history = []
        for r in results:
            memory_text = ""
            if isinstance(r, dict):
                memory_text = r.get("memory", "")
            else:
                memory_text = getattr(r, "memory", str(r)) if hasattr(r, "memory") else ""
            
            if memory_text:
                history.append({"role": "user", "content": memory_text})
        print(f"[MEM0] Retrieved {len(history)} memories for context (query: {query[:40]})")
        return history
    except Exception as e:
        print(f"[MEM0] get_relevant_context failed: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# 2. PRODUCT CONTEXT — richer than last_product, enables no-GraphRAG answers
# ══════════════════════════════════════════════════════════════════════════════

async def save_product_context(
    tenant_id: str, session_id: str,
    product: dict,
) -> None:
    """
    Saves current product being discussed as structured memory.
    Richer than the old LAST_PRODUCT string — includes key specs so
    follow-up questions like "is it waterproof?" can be answered
    from memory without a GraphRAG round-trip.

    product dict: {name, sku, category, price, warranty, waterproof,
                   wattage, material, installation_url, ...}
    """
    client = _get_client()
    if not client:
        return
    try:
        # Only store fields that answer common follow-up questions
        context = {
            "type":       "product_context",
            "name":       product.get("product_name") or product.get("name", ""),
            "sku":        product.get("sku", ""),
            "price":      product.get("list_price") or product.get("price", ""),
            "warranty":   product.get("warranty", ""),
            "waterproof": "Yes" if "waterproof" in str(product.get("feature_descriptions", "")).lower() else "check specs",
            "material":   _extract_material(product.get("feature_descriptions", "")),
            "category":   product.get("category", ""),
        }
        client.add(
            messages=[{"role": "system",
                       "content": f"PRODUCT_CONTEXT: {json.dumps(context)}"}],
            user_id  = session_id,
            agent_id = tenant_id,
            metadata = {"type": "product_context"},
        )
    except Exception as e:
        print(f"[MEM0] save_product_context failed: {e}")


def _extract_material(features: str) -> str:
    features_lower = features.lower()
    for mat in ["aluminum", "aluminium", "stainless steel", "plastic", "iron"]:
        if mat in features_lower:
            return mat.title()
    return "check specs"


async def get_product_context(
    tenant_id: str, session_id: str,
) -> Optional[dict]:
    """Retrieves the current product context from memory."""
    client = _get_client()
    if not client:
        return None
    try:
        results = _search(client, "PRODUCT_CONTEXT current product",
                          user_id=session_id, agent_id=tenant_id,
                          limit=1, memory_type="product_context")
        for r in results:
            text = r.get("memory", "")
            if "PRODUCT_CONTEXT:" in text:
                return json.loads(text.split("PRODUCT_CONTEXT:", 1)[1].strip())
        return None
    except Exception as e:
        print(f"[MEM0] get_product_context failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 3. CUSTOMER PREFERENCES — long-term, survives across conversations
# ══════════════════════════════════════════════════════════════════════════════

async def save_customer_preference(
    tenant_id: str, session_id: str,
    preference_type: str, value: str,
    confidence: float = 0.8,
) -> None:
    """
    Saves a detected customer preference for use in future sessions.
    Examples: budget_range, preferred_category, colour, wattage, language.
    These persist across sessions — unlike workflow_snapshot which is per-session.

    preference_type: "budget_range" | "preferred_category" | "colour" |
                     "wattage" | "language" | "delivery_city"
    """
    client = _get_client()
    if not client:
        return
    try:
        pref = {
            "type":       "customer_preference",
            "pref_type":  preference_type,
            "value":      value,
            "confidence": confidence,
        }
        client.add(
            messages=[{"role": "system",
                       "content": f"CUSTOMER_PREF: {json.dumps(pref)}"}],
            user_id  = session_id,
            agent_id = tenant_id,
            metadata = {"type": "customer_preference",
                        "pref_type": preference_type},
        )
        print(f"[MEM0] Preference saved: {preference_type}={value}")
    except Exception as e:
        print(f"[MEM0] save_customer_preference failed: {e}")


async def get_customer_preferences(
    tenant_id: str, session_id: str,
) -> dict:
    """Returns all known customer preferences as a flat dict."""
    client = _get_client()
    if not client:
        return {}
    try:
        results = _search(client, "CUSTOMER_PREF preferences budget category",
                          user_id=session_id, agent_id=tenant_id,
                          limit=10, memory_type="customer_preference")
        prefs = {}
        for r in results:
            text = r.get("memory", "")
            if "CUSTOMER_PREF:" in text:
                try:
                    p = json.loads(text.split("CUSTOMER_PREF:", 1)[1].strip())
                    prefs[p.get("pref_type", "unknown")] = p.get("value")
                except Exception:
                    pass
        return prefs
    except Exception as e:
        print(f"[MEM0] get_customer_preferences failed: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# 4. NEGOTIATION PROFILE — personalizes negotiation strategy
# ══════════════════════════════════════════════════════════════════════════════

async def save_negotiation_outcome(
    tenant_id: str, session_id: str,
    product_name: str,
    opening_price: float,
    final_price: float,
    rounds: int,
    accepted: bool,
    quantity: int,
) -> None:
    """
    Saves negotiation outcome after each deal (accepted or rejected).
    Accumulates over time into a negotiation profile.
    Future negotiations can start intelligently — e.g. if customer
    always settles around 8% off, open closer to that.
    """
    client = _get_client()
    if not client:
        return
    try:
        discount_pct = round((opening_price - final_price) / opening_price * 100, 1) if opening_price > 0 else 0
        outcome = {
            "type":            "negotiation_outcome",
            "product":         product_name,
            "opening_price":   opening_price,
            "final_price":     final_price,
            "discount_pct":    discount_pct,
            "rounds":          rounds,
            "accepted":        accepted,
            "quantity":        quantity,
        }
        client.add(
            messages=[{"role": "system",
                       "content": f"NEG_OUTCOME: {json.dumps(outcome)}"}],
            user_id  = session_id,
            agent_id = tenant_id,
            metadata = {"type": "negotiation_outcome"},
        )
        print(f"[MEM0] Negotiation outcome saved: {discount_pct}% discount, accepted={accepted}")
    except Exception as e:
        print(f"[MEM0] save_negotiation_outcome failed: {e}")


async def get_negotiation_profile(
    tenant_id: str, session_id: str,
) -> dict:
    """
    Returns a summary of the customer's negotiation history.
    Used to personalize the opening offer and negotiation style.
    Returns: {avg_discount_pct, typical_rounds, acceptance_rate, budget_range}
    """
    client = _get_client()
    if not client:
        return {}
    try:
        results = _search(client, "NEG_OUTCOME negotiation discount accepted",
                          user_id=session_id, agent_id=tenant_id,
                          limit=10, memory_type="negotiation_outcome")
        outcomes = []
        for r in results:
            text = r.get("memory", "")
            if "NEG_OUTCOME:" in text:
                try:
                    outcomes.append(json.loads(text.split("NEG_OUTCOME:", 1)[1].strip()))
                except Exception:
                    pass
        if not outcomes:
            return {}

        accepted  = [o for o in outcomes if o.get("accepted")]
        discounts = [o["discount_pct"] for o in accepted if "discount_pct" in o]
        prices    = [o["final_price"]  for o in accepted if "final_price"   in o]
        rounds    = [o["rounds"]       for o in outcomes  if "rounds"       in o]

        return {
            "avg_discount_pct":  round(sum(discounts) / len(discounts), 1) if discounts else None,
            "typical_rounds":    round(sum(rounds)    / len(rounds),    1) if rounds    else None,
            "acceptance_rate":   round(len(accepted)  / len(outcomes) * 100, 0),
            "budget_range":      f"Rs.{int(min(prices)):,}–Rs.{int(max(prices)):,}" if len(prices) >= 2 else None,
            "total_orders":      len(accepted),
        }
    except Exception as e:
        print(f"[MEM0] get_negotiation_profile failed: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# 5. WORKFLOW SNAPSHOT — single source for current session state in Mem0
# ══════════════════════════════════════════════════════════════════════════════

async def save_workflow_snapshot(
    tenant_id: str, session_id: str,
    state: str,           # "BROWSING" | "ORDERING" | "NEGOTIATING" | "CONFIRMING" | "INVOICED"
    product_name: str = "",
    quantity: int = 0,
    offer_price: float = 0,
    neg_round: int = 0,
) -> None:
    """
    Saves a compact workflow snapshot after each major state transition.
    Replaces the fragmented workflow_sessions + negotiation_state + pending_order
    pattern for the purpose of context retrieval. (Postgres still stores the
    authoritative state — this is for semantic context injection only.)
    """
    client = _get_client()
    if not client:
        return
    try:
        snapshot = {
            "type":         "workflow_snapshot",
            "state":        state,
            "product":      product_name,
            "quantity":     quantity,
            "offer_price":  offer_price,
            "neg_round":    neg_round,
        }
        client.add(
            messages=[{"role": "system",
                       "content": f"WORKFLOW_SNAPSHOT: {json.dumps(snapshot)}"}],
            user_id  = session_id,
            agent_id = tenant_id,
            metadata = {"type": "workflow_snapshot"},
        )
    except Exception as e:
        print(f"[MEM0] save_workflow_snapshot failed: {e}")


async def get_workflow_snapshot(
    tenant_id: str, session_id: str,
) -> Optional[dict]:
    """Returns the most recent workflow snapshot."""
    client = _get_client()
    if not client:
        return None
    try:
        results = _search(client, "WORKFLOW_SNAPSHOT current state",
                          user_id=session_id, agent_id=tenant_id,
                          limit=1, memory_type="workflow_snapshot")
        for r in results:
            text = r.get("memory", "")
            if "WORKFLOW_SNAPSHOT:" in text:
                return json.loads(text.split("WORKFLOW_SNAPSHOT:", 1)[1].strip())
        return None
    except Exception as e:
        print(f"[MEM0] get_workflow_snapshot failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 6. TENANT OFFERS — agent-level (shared across all customers of a tenant)
# ══════════════════════════════════════════════════════════════════════════════

async def save_tenant_knowledge(
    tenant_id: str, offers_text: str, tiers_json: Optional[str] = None,
) -> None:
    client = _get_client()
    if not client:
        return
    try:
        content = f"STORE_OFFERS: {offers_text}"
        if tiers_json:
            content += f"\nTIERS: {tiers_json}"
        client.add(
            messages=[{"role": "system", "content": content}],
            agent_id = tenant_id,
            metadata = {"type": "tenant_offers"},
        )
    except Exception as e:
        print(f"[MEM0] save_tenant_knowledge failed: {e}")


async def get_tenant_knowledge(tenant_id: str) -> Optional[dict]:
    client = _get_client()
    if not client:
        return None
    try:
        results = _search(client, "STORE_OFFERS discount tiers pricing",
                          agent_id=tenant_id, limit=1,
                          memory_type="tenant_offers")
        for r in results:
            text = r.get("memory", "")
            if "STORE_OFFERS:" in text:
                return {"offers_text": text.split("STORE_OFFERS:", 1)[1].strip()}
        return None
    except Exception as e:
        print(f"[MEM0] get_tenant_knowledge failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 7. CONTEXT FOR PROMPT INJECTION
#    Assembles all relevant memories into a structured dict that
#    prompt_store.py can inject into any LLM system prompt as variables.
# ══════════════════════════════════════════════════════════════════════════════

async def get_context_for_prompt(
    tenant_id: str, session_id: str, query: str = "",
) -> dict:
    """
    Retrieves and assembles all relevant memories for LLM prompt injection.
    Returns a dict of named context strings that map directly to prompt
    template variables (e.g. {customer_context}, {product_context}).

    Used by prompt_store.get_prompt() to enrich any system prompt with
    relevant customer/product/workflow context automatically.

    Zero workflow impact — if any memory retrieval fails, that field is
    just an empty string and the prompt works as before.
    """
    import asyncio

    # Fetch all relevant memory types in parallel — no sequential waits
    results = await asyncio.gather(
        get_product_context(tenant_id, session_id),
        get_customer_preferences(tenant_id, session_id),
        get_negotiation_profile(tenant_id, session_id),
        get_workflow_snapshot(tenant_id, session_id),
        return_exceptions=True,
    )

    product_ctx = results[0] if not isinstance(results[0], BaseException) else None
    prefs       = results[1] if not isinstance(results[1], BaseException) else {}
    neg_profile = results[2] if not isinstance(results[2], BaseException) else {}
    snapshot    = results[3] if not isinstance(results[3], BaseException) else None

    product_str = ""
    if product_ctx:
        _pctx       = product_ctx
        p_name      = _pctx.get("name",       "") or ""
        p_price     = _pctx.get("price",      "") or ""
        p_warranty  = _pctx.get("warranty",   "") or ""
        p_waterproof= _pctx.get("waterproof", "") or ""
        product_str = (
            f"Currently discussing: {p_name} "
            f"(Rs.{p_price}/unit, Warranty: {p_warranty}, Waterproof: {p_waterproof})"
        )

    prefs_str = ""
    if prefs:
        parts = [f"{str(k)}: {str(v)}" for k, v in prefs.items() if v]
        prefs_str = "Customer preferences — " + ", ".join(parts) if parts else ""

    neg_str = ""
    _np       = neg_profile
    avg_disc  = _np.get("avg_discount_pct")
    typ_rounds= _np.get("typical_rounds")
    budget_r  = _np.get("budget_range")
    if avg_disc is not None:
        neg_str = (
            f"Negotiation history: typically accepts "
            f"{str(avg_disc)}% discount, "
            f"resolves in {str(typ_rounds) if typ_rounds else '?'} rounds"
        )
        if budget_r:
            neg_str += f", budget range {str(budget_r)}"

    workflow_str = ""
    if snapshot:
        _snap     = snapshot
        w_state   = _snap.get("state",       "") or ""
        w_product = _snap.get("product",     "") or ""
        w_qty     = str(_snap.get("quantity",    "") or "")
        w_offer   = str(_snap.get("offer_price", "") or "")
        workflow_str = f"Session state: {w_state}"
        if w_product:
            workflow_str += f" — {w_product} x{w_qty}"
        if w_offer:
            workflow_str += f" @ Rs.{w_offer}"

    context = {
        "product_context":      product_str,
        "customer_preferences": prefs_str,
        "negotiation_profile":  neg_str,
        "workflow_context":     workflow_str,
    }
    # Only log if we actually found something useful
    non_empty = sum(1 for v in context.values() if v)
    if non_empty:
        print(f"[MEM0] Injecting {non_empty} context fields into prompt")
    return context


# ══════════════════════════════════════════════════════════════════════════════
# BACKWARDS COMPATIBILITY — keep old function names working
# ══════════════════════════════════════════════════════════════════════════════

# Old workflow_state functions (used by handlers.py) — delegate to snapshot
async def save_workflow_state(tenant_id, session_id, state):
    await save_workflow_snapshot(tenant_id, session_id,
                                  state=state.get("status", "UNKNOWN"),
                                  product_name=state.get("product_name", ""),
                                  quantity=state.get("quantity_value", 0))

async def get_workflow_state(tenant_id, session_id):
    return await get_workflow_snapshot(tenant_id, session_id)

async def clear_workflow_state(tenant_id, session_id):
    pass  # snapshots are overwritten, not deleted

# Old last_product functions
async def save_last_product(tenant_id, session_id, product_name):
    await save_product_context(tenant_id, session_id, {"product_name": product_name})

async def get_last_product(tenant_id, session_id):
    ctx = await get_product_context(tenant_id, session_id)
    return ctx.get("name") if ctx else None


# ══════════════════════════════════════════════════════════════════════════════
# INTENT-AWARE RETRIEVAL
# Instead of always searching everything, route each intent to only the
# memory types that are actually relevant. Faster + more precise.
# ══════════════════════════════════════════════════════════════════════════════

# Maps intent → which memory types to query
_INTENT_MEMORY_MAP = {
    # Product / FAQ queries → need product context and conversation summary
    "FAQ_KNOWLEDGE": ["product_context", "conversation"],

    # Workflow actions (ordering, qty change, confirm) → need workflow + product
    "WORKFLOW_ACTION": ["workflow_snapshot", "product_context", "conversation"],

    # Negotiation messages → need negotiation profile + workflow + product
    "NEGOTIATION": ["negotiation_outcome", "workflow_snapshot", "product_context"],

    # Greeting → preferences to personalize
    "GREETING": ["customer_preference", "conversation"],

    # Escalation → full context needed
    "HUMAN_ESCALATION": ["conversation", "workflow_snapshot", "product_context"],

    # Default — all types
    "*": ["conversation", "workflow_snapshot", "product_context", "customer_preference"],
}


async def get_context_for_intent(
    tenant_id: str, session_id: str,
    intent: str, query: str = "",
    limit_per_type: int = 3,
) -> dict:
    """
    Intent-aware context retrieval — only fetches memory types relevant
    to the current intent. Faster than get_context_for_prompt() which
    always fetches all 4 types in parallel regardless of what's needed.

    Returns the same structure as get_context_for_prompt() so callers
    can swap between them without code changes.
    """
    import asyncio

    memory_types = _INTENT_MEMORY_MAP.get(intent, _INTENT_MEMORY_MAP["*"])
    client       = _get_client()
    if not client:
        return {"product_context": "", "customer_preferences": "",
                "negotiation_profile": "", "workflow_context": ""}

    async def _fetch(mem_type: str) -> list:
        try:
            return _search(client, query or mem_type,
                           user_id=session_id, agent_id=tenant_id,
                           limit=limit_per_type, memory_type=mem_type)
        except Exception:
            return []

    # Fetch only the relevant types — in parallel
    results = await asyncio.gather(*[_fetch(t) for t in memory_types],
                                   return_exceptions=True)
    memories_by_type: dict = {}
    for i, mem_type in enumerate(memory_types):
        if not isinstance(results[i], Exception):
            memories_by_type[mem_type] = results[i]

    # Parse and build context strings
    from typing import cast as _cast

    product_str = ""
    for r in memories_by_type.get("product_context", []):
        text = r.get("memory", "")
        if "PRODUCT_CONTEXT:" in text:
            try:
                import json as _json
                p = _json.loads(text.split("PRODUCT_CONTEXT:", 1)[1].strip())
                _p = _cast(dict, p)
                product_str = (
                    f"Currently discussing: {_p.get('name','')}"
                    f" (Rs.{_p.get('price','')}, "
                    f"Warranty: {_p.get('warranty','')}, "
                    f"Waterproof: {_p.get('waterproof','')})"
                )
                break
            except Exception:
                pass

    neg_str = ""
    for r in memories_by_type.get("negotiation_outcome", []):
        text = r.get("memory", "")
        if "NEG_OUTCOME:" in text:
            try:
                import json as _json
                o = _cast(dict, _json.loads(text.split("NEG_OUTCOME:", 1)[1].strip()))
                neg_str = (
                    f"Past negotiation: accepted {o.get('discount_pct','')}% discount"
                    f" on {o.get('product','')}"
                )
                break
            except Exception:
                pass

    prefs_str = ""
    for r in memories_by_type.get("customer_preference", []):
        text = r.get("memory", "")
        if "CUSTOMER_PREF:" in text:
            try:
                import json as _json
                p = _cast(dict, _json.loads(text.split("CUSTOMER_PREF:", 1)[1].strip()))
                prefs_str += f"{p.get('pref_type','')}: {p.get('value','')}; "
            except Exception:
                pass
    if prefs_str:
        prefs_str = "Preferences — " + prefs_str.rstrip("; ")

    workflow_str = ""
    for r in memories_by_type.get("workflow_snapshot", []):
        text = r.get("memory", "")
        if "WORKFLOW_SNAPSHOT:" in text:
            try:
                import json as _json
                w = _cast(dict, _json.loads(text.split("WORKFLOW_SNAPSHOT:", 1)[1].strip()))
                workflow_str = (
                    f"Session: {w.get('state','')}"
                    + (f" — {w.get('product','')} x{w.get('quantity','')}" if w.get('product') else "")
                )
                break
            except Exception:
                pass

    non_empty = sum(1 for v in [product_str, neg_str, prefs_str, workflow_str] if v)
    if non_empty:
        print(f"[MEM0] Intent={intent}: retrieved {non_empty} context fields "
              f"from types={memory_types}")

    return {
        "product_context":      product_str,
        "customer_preferences": prefs_str,
        "negotiation_profile":  neg_str,
        "workflow_context":     workflow_str,
    }