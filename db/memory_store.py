# db/memory_store.py — Mem0 Memory Layer
#
# VERIFIED (2026-07-05 via verify_search_format.py):
#   The installed Mem0 SDK requires filters={"user_id": session_id} —
#   top-level user_id= kwargs are explicitly rejected with:
#   "Top-level entity parameters are not supported. Use filters={...} instead."
#   Both search() and get_all() follow this pattern.
#   DO NOT switch to top-level user_id= — it breaks all memory lookups.

import json
from typing import Optional
from config import MEM0_API_KEY

_mem0_client = None

def _get_client():
    global _mem0_client
    if _mem0_client is None and MEM0_API_KEY:
        try:
            from mem0 import MemoryClient
            _mem0_client = MemoryClient(api_key=MEM0_API_KEY)
        except ImportError:
            print("[MEM0] WARNING: 'mem0ai' module is not installed. Falling back to Postgres database storage.")
            return None


# ── Multi-tenant isolation ────────────────────────────────────────────────────
# SDK 2.0.10 confirmed: agent_id is NOT persisted (agent_id=None on all reads).
# Workaround: embed tenant_id + session_id as a prefix inside memory text.
# Format: "T:{tenant_id}|U:{session_id}| <actual memory content>"
# Save: always prefix before storing
# Search: filter by user_id only, then filter results by tenant prefix in code
# This gives correct multi-tenant isolation without relying on agent_id.

def _add_tenant_prefix(text: str, tenant_id: str, session_id: str) -> str:
    return f"T:{tenant_id}|U:{session_id}| {text}"

def _strip_tenant_prefix(text: str) -> str:
    """Returns clean memory text without the tenant prefix."""
    if "|" in text:
        parts = text.split("|", 2)
        if len(parts) >= 3 and parts[0].startswith("T:") and parts[1].startswith("U:"):
            return parts[2].strip()
    return text

def _matches_tenant(text: str, tenant_id: str, session_id: str) -> bool:
    return text.startswith(f"T:{tenant_id}|U:{session_id}|")

def _search_and_filter(
    client, query: str, tenant_id: str, session_id: str, limit: int = 10
) -> list:
    """
    Searches Mem0 by user_id, then filters by tenant prefix in Python.
    Fetches limit*3 to ensure enough results after tenant filtering.
    """
    try:
        result = client.search(
            query   = query,
            filters = {"user_id": session_id},
            limit   = limit * 3,
        )
        if isinstance(result, dict):
            result = result.get("results", [])
        if not isinstance(result, list):
            return []
        # Filter to only this tenant's memories
        filtered = [
            r for r in result
            if _matches_tenant(r.get("memory", ""), tenant_id, session_id)
        ]
        # Strip prefix from memory text so callers get clean content
        for r in filtered:
            if isinstance(r, dict) and "memory" in r:
                r["memory"] = _strip_tenant_prefix(r["memory"])
        return filtered[:limit]
    except Exception as e:
        print(f"[MEM0] search failed: {e}")
        return []

def _save_to_mem0(
    client, text: str, tenant_id: str, session_id: str,
    metadata: Optional[dict] = None,
) -> None:
    """Saves to Mem0 with tenant prefix embedded for isolation."""
    try:
        prefixed = _add_tenant_prefix(text, tenant_id, session_id)
        meta     = {"type": "general", **(metadata or {})}
        client.add(
            messages = [{"role": "system", "content": prefixed}],
            user_id  = session_id,
            agent_id = tenant_id,  # passed for future SDK compatibility
            metadata = meta,
        )
    except Exception as e:
        print(f"[MEM0] save failed: {e}")
    return _mem0_client


def _extract_memory_text(r) -> str:
    """Normalizes a single Mem0 search result to plain text — handles dict or string."""
    if isinstance(r, dict):
        return r.get("memory") or r.get("text") or ""
    if isinstance(r, str):
        return r
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# CONVERSATION CONTEXT
# ══════════════════════════════════════════════════════════════════════════════

async def add_conversation_turn(
    tenant_id: str,
    session_id: str,
    user_text: str,
    bot_reply: str,
) -> None:
    client = _get_client()
    if not client:
        return
    try:
        prefixed_user = _add_tenant_prefix(user_text, tenant_id, session_id)
        prefixed_bot  = _add_tenant_prefix(bot_reply,  tenant_id, session_id)
        client.add(
            messages=[
                {"role": "user",      "content": prefixed_user},
                {"role": "assistant", "content": prefixed_bot},
            ],
            user_id  = session_id,
            agent_id = tenant_id,
        )
        print(f"[MEM0] Conversation turn saved for {session_id[-4:]}")
    except Exception as e:
        print(f"[MEM0] add_conversation_turn failed: {e}")


async def get_relevant_context(
    tenant_id: str,
    session_id: str,
    query: str,
    limit: int = 5,
) -> list:
    """
    Retrieves semantically relevant memories for building LLM context.
    Uses filters dict (required by newer Mem0 SDK) instead of top-level kwargs.
    """
    client = _get_client()
    if not client:
        return []
    try:
        # Use _search_and_filter for multi-tenant isolation via tenant prefix
        results = _search_and_filter(client, query, tenant_id, session_id, limit)

        # FIX: Mem0 SDK returns different shapes depending on version:
        #   - dict with "results" key: {"results": [...], "relations": [...]}
        #   - list of dicts: [{"memory": "...", "id": "..."}]
        #   - list of strings: ["memory text 1", "memory text 2"]
        # The original bug: results.get("results") called on a list → AttributeError
        if not isinstance(results, list):
            results = []

        history = []
        for r in results:
            memory_text = _extract_memory_text(r)
            if memory_text:
                history.append({"role": "user", "content": memory_text})

        # Point 7: log by memory type breakdown for easier debugging
        # Shows: [MEM0] Retrieved 3: conversation=2 last_product=1 workflow=0
        type_counts: dict = {}
        for r in results:
            if isinstance(r, dict):
                mem_type = (r.get("metadata") or {}).get("type", "unknown")
                text = r.get("memory", "")
                if "LAST_PRODUCT" in text:
                    mem_type = "last_product"
                elif "WORKFLOW_SNAPSHOT" in text:
                    mem_type = "workflow"
                elif "PRODUCT_CONTEXT" in text:
                    mem_type = "product_context"
                type_counts[mem_type] = type_counts.get(mem_type, 0) + 1

        if type_counts:
            breakdown = " | ".join(f"{k}={v}" for k, v in type_counts.items())
            print(f"[MEM0] Retrieved {len(history)}: {breakdown} (query: {query[:35]})")
        else:
            print(f"[MEM0] Retrieved {len(history)} memories (query: {query[:40]})")
        return history
    except Exception as e:
        print(f"[MEM0] get_relevant_context failed: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW / PENDING ORDER STATE
# ══════════════════════════════════════════════════════════════════════════════

async def save_workflow_state(tenant_id: str, session_id: str, state: dict) -> None:
    client = _get_client()
    if not client:
        return
    try:
        await clear_workflow_state(tenant_id, session_id)
        state_text = f"PENDING_ORDER_STATE: {json.dumps(state)}"
        state_prefixed = _add_tenant_prefix(f"PENDING_ORDER_STATE: {json.dumps(state)}", tenant_id, session_id)
        client.add(
            messages=[{"role": "system", "content": state_prefixed}],
            user_id  = session_id,
            agent_id = tenant_id,
            metadata = {"type": "workflow_state", "status": "PENDING"},
        )
        print(f"[MEM0] Workflow state saved: missing={state.get('missing_fields')}")
    except Exception as e:
        print(f"[MEM0] save_workflow_state failed: {e}")


async def get_workflow_state(tenant_id: str, session_id: str) -> Optional[dict]:
    client = _get_client()
    if not client:
        return None
    try:
        results = _search_and_filter(client, "PENDING_ORDER_STATE",
                                     tenant_id, session_id, limit=1)

        for r in results:
            memory_text = _extract_memory_text(r)
            if "PENDING_ORDER_STATE:" in memory_text:
                json_str = memory_text.split("PENDING_ORDER_STATE:", 1)[1].strip()
                state = json.loads(json_str)
                print(f"[MEM0] Workflow state retrieved: {state.get('missing_fields')}")
                return state
        return None
    except Exception as e:
        print(f"[MEM0] get_workflow_state failed: {e}")
        return None


async def clear_workflow_state(tenant_id: str, session_id: str) -> None:
    client = _get_client()
    if not client:
        return
    try:
        results = _search_and_filter(client, "PENDING_ORDER_STATE",
                                     tenant_id, session_id, limit=5)

        for r in results:
            memory_id = r.get("id") if isinstance(r, dict) else None
            if memory_id:
                client.delete(memory_id)
        print(f"[MEM0] Workflow state cleared for {session_id[-4:]}")
    except Exception as e:
        print(f"[MEM0] clear_workflow_state failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# TENANT OFFERS / KNOWLEDGE
# ══════════════════════════════════════════════════════════════════════════════

async def save_tenant_knowledge(tenant_id: str, offers_text: str, tiers_json: Optional[str] = None) -> None:
    client = _get_client()
    if not client:
        return
    try:
        content = f"STORE_OFFERS: {offers_text}"
        if tiers_json:
            content += f"\nTIERS: {tiers_json}"
        content_prefixed = _add_tenant_prefix(content, tenant_id, f"agent_{tenant_id}")
        client.add(
            messages=[{"role": "system", "content": content_prefixed}],
            user_id  = f"agent_{tenant_id}",
            agent_id = tenant_id,
            metadata = {"type": "tenant_offers"},
        )
        print(f"[MEM0] Tenant offers saved for {tenant_id}")
    except Exception as e:
        print(f"[MEM0] save_tenant_knowledge failed: {e}")


async def get_tenant_knowledge(tenant_id: str) -> Optional[dict]:
    client = _get_client()
    if not client:
        return None
    try:
        results = _search_and_filter(client, "STORE_OFFERS discount tiers pricing",
                                     tenant_id, f"agent_{tenant_id}", limit=1)

        for r in results:
            memory_text = _extract_memory_text(r)
            if "STORE_OFFERS:" in memory_text:
                offers_text = memory_text.split("STORE_OFFERS:", 1)[1].strip()
                print(f"[MEM0] Tenant offers retrieved for {tenant_id}")
                return {"offers_text": offers_text, "tiers_json": None}
        return None
    except Exception as e:
        print(f"[MEM0] get_tenant_knowledge failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# LAST DISCUSSED PRODUCT
# ══════════════════════════════════════════════════════════════════════════════

async def save_last_product(tenant_id: str, session_id: str, product_name: str) -> None:
    client = _get_client()
    if not client:
        return
    try:
        text_prefixed = _add_tenant_prefix(f"LAST_PRODUCT: {product_name}", tenant_id, session_id)
        client.add(
            messages=[{"role": "system", "content": text_prefixed}],
            user_id  = session_id,
            agent_id = tenant_id,
            metadata = {"type": "last_product"},
        )
    except Exception as e:
        print(f"[MEM0] save_last_product failed: {e}")


async def get_last_product(tenant_id: str, session_id: str) -> Optional[str]:
    client = _get_client()
    if not client:
        return None
    try:
        results = _search_and_filter(client, "LAST_PRODUCT",
                                     tenant_id, session_id, limit=1)

        for r in results:
            memory_text = _extract_memory_text(r)
            if "LAST_PRODUCT:" in memory_text:
                return memory_text.split("LAST_PRODUCT:", 1)[1].strip()
        return None
    except Exception as e:
        print(f"[MEM0] get_last_product failed: {e}")
        return None


async def get_context_for_prompt(
    tenant_id: str,
    session_id: str,
    query: str,
) -> dict:
    """
    Fetches customer preferences, product context, negotiation profile, and
    workflow context from Mem0 for a prompt.
    """
    try:
        from ai.memory_manager import MemoryManager
        mm = MemoryManager(tenant_id, session_id)
        
        # We need product_context and workflow_snapshot memories.
        # Prefs and negotiation are derived from customer profile.
        raw = await mm.search(
            ["product_context", "workflow_snapshot"],
            query = query,
            max_results = 3
        )
        
        profile = await mm.get_customer_profile()
        
        # Parse product context
        product_str = ""
        for r in raw.get("product_context", []):
            text = r.get("memory", "")
            if "PRODUCT_CONTEXT:" in text:
                try:
                    p = json.loads(text.split("PRODUCT_CONTEXT:", 1)[1].strip())
                    product_str = (
                        f"{p.get('name','')} | Rs.{p.get('price','')} | "
                        f"Warranty: {p.get('warranty','')} | "
                        f"Waterproof: {p.get('waterproof','')}"
                    )
                    break
                except Exception:
                    pass

        # Parse workflow context
        workflow_str = ""
        for r in raw.get("workflow_snapshot", []):
            text = r.get("memory", "")
            if "WORKFLOW_SNAPSHOT:" in text:
                try:
                    w = json.loads(text.split("WORKFLOW_SNAPSHOT:", 1)[1].strip())
                    out = f"State: {w.get('state','')}"
                    if w.get("product"):
                        out += f" — {w['product']} x{w.get('quantity','')}"
                    if w.get("offer_price"):
                        out += f" @ Rs.{w['offer_price']}"
                    workflow_str = out
                    break
                except Exception:
                    pass

        # Parse preferences
        prefs = profile.get("preferences", {})
        prefs_str = ""
        if prefs:
            parts = [f"{k}: {v}" for k, v in prefs.items() if v]
            prefs_str = "Preferences — " + ", ".join(parts) if parts else ""

        # Parse negotiation profile
        neg = profile.get("negotiation", {})
        neg_str = ""
        if neg and neg.get("avg_discount_pct") is not None:
            neg_str = f"Typically accepts {neg['avg_discount_pct']}% discount"
            if neg.get("typical_rounds"):
                neg_str += f" in {neg['typical_rounds']} rounds"
            if neg.get("budget_range"):
                neg_str += f", budget {neg['budget_range']}"

        return {
            "product_context":      product_str,
            "customer_preferences": prefs_str,
            "negotiation_profile":  neg_str,
            "workflow_context":     workflow_str,
        }
    except Exception as e:
        print(f"[MEM0] get_context_for_prompt failed: {e}")
        return {
            "product_context":      "",
            "customer_preferences": "",
            "negotiation_profile":  "",
            "workflow_context":     "",
        }