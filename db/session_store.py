# db/session_store.py — Supabase PostgreSQL Message Store

import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List, cast
from supabase import create_client, Client  # type: ignore[import]
from models.schemas import IncomingMessage, EntityResult
from config import SUPABASE_URL, SUPABASE_SERVICE_KEY

_supabase: Optional[Client] = None

def _get_client() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _supabase


async def resolve_tenant_id(phone_number_id: str) -> Optional[dict]:
    """
    Resolves full tenant profile from phone_number_id via DB lookup.

    Returns ALL tenant fields needed across the system:
        tenant_id     → business isolation key
        biz_name      → shown on invoice header
        tagline       → shown below business name on invoice
        city          → shown on invoice
        support_email → shown on invoice
        website       → shown on invoice footer
        upi_id        → shown in invoice payment section
        account_name  → shown in invoice payment section
        timezone      → drives time-aware greetings
        region        → data residency
        language      → future AI prompt language

    WHY NO HARDCODING:
        Every field comes from the tenants table.
        Adding a new client = insert one row, zero code changes.
        Changing any detail = update one row, zero code changes.

    Returns:
        dict → full tenant profile if found
        None → phone_number_id not registered, reject message
    """
    try:
        result = _get_client().table("tenants") \
            .select("*") \
            .eq("phone_number_id", phone_number_id) \
            .limit(1) \
            .execute()

        if result.data:
            row = cast(dict, result.data[0])
            print(f"[DB] Tenant resolved: {row['tenant_id']} ({row.get('biz_name', 'N/A')}) "
                  f"for phone_number_id={phone_number_id}")
            return row

        print(f"[DB] Tenant NOT found for phone_number_id={phone_number_id} — rejecting message")
        return None

    except Exception as e:
        print(f"[DB] Tenant resolve failed: {e}")
        return None


async def get_session_history(tenant_id: str, session_id: str, limit: int = 10) -> List[dict]:
    """Fetches the last N messages for a customer session from the DB."""
    try:
        result = _get_client().table("messages") \
            .select("text, reply_text, direction, original_type, created_at") \
            .eq("tenant_id", tenant_id) \
            .eq("session_id", session_id) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()

        if not result.data:
            return []

        messages = list(reversed(result.data))

        history = []
        for msg in messages:
            if isinstance(msg, dict):
                if msg.get("text"):
                    history.append({"role": "user", "content": msg["text"]})
                if msg.get("reply_text"):
                    history.append({"role": "assistant", "content": msg["reply_text"]})

        print(f"[DB] Session history fetched — {len(history)} turns for {session_id}")
        return history

    except Exception as e:
        print(f"[DB] Session history fetch failed: {e}")
        return []


async def is_duplicate(message_id: str, tenant_id: str) -> bool:
    """Checks if message_id already exists in DB, scoped to this tenant."""
    try:
        result = _get_client().table("messages") \
            .select("id") \
            .eq("message_id", message_id) \
            .eq("tenant_id", tenant_id) \
            .limit(1) \
            .execute()
        return len(result.data) > 0
    except Exception as e:
        print(f"[DB] Duplicate check failed: {e}")
        return False


async def save_message(incoming: IncomingMessage) -> bool:
    """Saves the complete IncomingMessage to the messages table (Save-First rule)."""
    try:
        row = {
            "trace_id":            incoming.trace_id,
            "message_id":          incoming.message_id,
            "session_id":          incoming.session_id,
            "channel":             incoming.channel,
            "timestamp_unix":      incoming.timestamp,
            "tenant_id":           incoming.tenant_id,
            "region":              incoming.region,
            "sender_name":         incoming.sender_name,
            "sender_phone_number": incoming.sender_phone,
            "direction":           "inbound",
            "original_type":       incoming.original_type,
            "text":                incoming.text,
            "media_url":           incoming.media_url,
            "media_id":            incoming.media_id,
            "media_mime_type":     incoming.media_mime_type,
            "intent":              None,
            "confidence":          None,
            "product_name":        None,
            "quantity_value":      None,
            "quantity_unit":       None,
            "delivery_date":       None,
            "invoice_number":      None,
            "payment_reference":   None,
            "missing_entities":    None,
            "reply_text":          None,
            "replied_at":          None,
            "received_at":         incoming.received_at,
        }
        _get_client().table("messages").insert(row).execute()
        print(f"[DB] Message saved — trace_id={incoming.trace_id}")
        return True
    except Exception as e:
        print(f"[DB] Save failed: {e}")
        return False


async def update_intent(message_id: str, intent: str, confidence: float, tenant_id: Optional[str] = None) -> bool:
    """Updates intent + confidence after AI classification, scoped to this tenant."""
    try:
        q = _get_client().table("messages") \
            .update({"intent": intent, "confidence": confidence}) \
            .eq("message_id", message_id)
        if tenant_id:
            q = q.eq("tenant_id", tenant_id)
        q.execute()
        print(f"[DB] Intent updated — {intent} ({confidence})")
        return True
    except Exception as e:
        print(f"[DB] Intent update failed: {e}")
        return False


async def update_entities(message_id: str, entities: EntityResult) -> bool:
    """Stores extracted entities after entity extraction engine runs."""
    try:
        _get_client().table("messages") \
            .update({
                "product_name":      entities.product_name,
                "quantity_value":    entities.quantity_value,
                "quantity_unit":     entities.quantity_unit,
                "delivery_date":     entities.delivery_date,
                "invoice_number":    entities.invoice_number,
                "payment_reference": entities.payment_reference,
                "missing_entities":  json.dumps(entities.missing_entities),
            }) \
            .eq("message_id", message_id) \
            .execute()
        print(f"[DB] Entities updated — product={entities.product_name} qty_value={entities.quantity_value} qty_unit={entities.quantity_unit}")
        return True
    except Exception as e:
        print(f"[DB] Entities update failed: {e}")
        return False


async def update_reply(
    message_id:        str,
    reply_text:        str,
    replied_at:        str,
    graphrag_response: Optional[str] = None,
) -> bool:
    """Stores reply text + timestamp + optional raw GraphRAG response."""
    try:
        update_data = {"reply_text": reply_text, "replied_at": replied_at}
        if graphrag_response is not None:
            # Store complete GraphRAG response — DB column is TEXT (unlimited)
            update_data["graphrag_response"] = graphrag_response
        _get_client().table("messages") \
            .update(update_data) \
            .eq("message_id", message_id) \
            .execute()
        print(f"[DB] Reply stored — replied_at={replied_at}")
        return True
    except Exception as e:
        print(f"[DB] Reply update failed: {e}")
        return False


async def save_outbound_message(
    tenant_id: str,
    session_id: str,
    message_id: str,
    text: str,
    media_url: Optional[str] = None,
    original_type: str = "text",
    region: str = "india",
) -> bool:
    """Saves an outbound message (bot reply) to the database."""
    import uuid
    try:
        row = {
            "trace_id":            f"trace_out_{uuid.uuid4().hex[:8]}",
            "message_id":          message_id,
            "session_id":          session_id,
            "channel":             "whatsapp",
            "timestamp_unix":      int(datetime.now(timezone.utc).timestamp()),
            "tenant_id":           tenant_id,
            "region":              region,
            "direction":           "outbound",
            "original_type":       original_type,
            "text":                text,
            "media_url":           media_url,
            "media_id":            None,
            "media_mime_type":     None,
            "intent":              None,
            "confidence":          None,
            "product_name":        None,
            "quantity_value":      None,
            "quantity_unit":       None,
            "delivery_date":       None,
            "invoice_number":      None,
            "payment_reference":   None,
            "missing_entities":    None,
            "reply_text":          None,
            "replied_at":          None,
            "received_at":         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        _get_client().table("messages").insert(row).execute()
        print(f"[DB] Outbound message saved — message_id={message_id}")
        return True
    except Exception as e:
        print(f"[DB] Save outbound failed: {e}")
        return False


async def get_reply_by_message_id(tenant_id: str, message_id: str) -> Optional[str]:
    """Looks up reply_text or text for a given message ID from the messages table."""
    try:
        result = _get_client().table("messages") \
            .select("text, reply_text") \
            .eq("tenant_id", tenant_id) \
            .eq("message_id", message_id) \
            .limit(1) \
            .execute()

        if result.data:
            row = cast(dict, result.data[0])
            val = row.get("text") or row.get("reply_text")
            return cast(Optional[str], val)
        return None
    except Exception as e:
        print(f"[DB] get_reply_by_message_id failed: {e}")
        return None


async def save_pending_order(
    tenant_id: str,
    session_id: str,
    product_name: str,
    quantity_value: int,
    quantity_unit: str,
) -> bool:
    """Saves a pending order in workflow_sessions table."""
    try:
        now_utc    = datetime.now(timezone.utc)
        expires_at = (now_utc + timedelta(minutes=20)).isoformat()
        row = {
            "tenant_id":      tenant_id,
            "session_id":     session_id,
            "status":         "ORDER_PENDING",
            "product_name":   product_name,
            "quantity_value": quantity_value,
            "quantity_unit":  quantity_unit,
            "expires_at":     expires_at,
            "updated_at":     now_utc.isoformat(),
        }
        # Update or insert
        existing = _get_client().table("workflow_sessions") \
            .select("id") \
            .eq("tenant_id",  tenant_id) \
            .eq("session_id", session_id) \
            .eq("status", "ORDER_PENDING") \
            .limit(1) \
            .execute()

        if existing.data:
            existing_row = cast(dict, existing.data[0])
            _get_client().table("workflow_sessions") \
                .update(row) \
                .eq("id", existing_row["id"]) \
                .execute()
        else:
            _get_client().table("workflow_sessions") \
                .insert(row) \
                .execute()
        return True
    except Exception as e:
        print(f"[DB] save_pending_order failed: {e}")
        return False


async def get_pending_order(tenant_id: str, session_id: str) -> Optional[dict]:
    """Retrieves the pending order from workflow_sessions table if not expired."""
    try:
        now_utc = datetime.now(timezone.utc).isoformat()
        result = _get_client().table("workflow_sessions") \
            .select("product_name, quantity_value, quantity_unit") \
            .eq("tenant_id",  tenant_id) \
            .eq("session_id", session_id) \
            .eq("status", "ORDER_PENDING") \
            .gt("expires_at", now_utc) \
            .order("updated_at", desc=True) \
            .limit(1) \
            .execute()
        return cast(dict, result.data[0]) if result.data else None
    except Exception as e:
        print(f"[DB] get_pending_order failed: {e}")
        return None


async def delete_pending_order(tenant_id: str, session_id: str) -> bool:
    """Deletes a pending order from workflow_sessions."""
    try:
        _get_client().table("workflow_sessions") \
            .delete() \
            .eq("tenant_id", tenant_id) \
            .eq("session_id", session_id) \
            .eq("status", "ORDER_PENDING") \
            .execute()
        return True
    except Exception as e:
        print(f"[DB] delete_pending_order failed: {e}")
        return False


async def get_last_order(tenant_id: str, session_id: str) -> Optional[dict]:
    """Fetches the most recent WORKFLOW_ACTION message with extracted entities."""
    try:
        result = _get_client().table("messages") \
            .select("product_name, quantity_value, quantity_unit, delivery_date, created_at, text") \
            .eq("tenant_id", tenant_id) \
            .eq("session_id", session_id) \
            .eq("intent", "WORKFLOW_ACTION") \
            .not_.is_("product_name", "null") \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        if result.data:
            return cast(dict, result.data[0])
        return None

    except Exception as e:
        print(f"[DB] Last order fetch failed: {e}")
        return None


async def get_last_n_orders(tenant_id: str, session_id: str, n: int = 2) -> list:
    """
    Fetches the last N completed orders for a customer.

    Used when customer asks "what are my last 2 orders?" type questions.
    Only returns orders where product_name is NOT null — meaning
    the order was fully collected (both product and quantity known).

    Args:
        tenant_id:  Business isolation key.
        session_id: Customer phone number.
        n:          How many orders to fetch (default 2).

    Returns:
        List of order dicts ordered newest first.
        Empty list if no orders found or DB fails.
    """
    try:
        result = _get_client().table("messages") \
            .select("product_name, quantity_value, quantity_unit, delivery_date, created_at") \
            .eq("tenant_id", tenant_id) \
            .eq("session_id", session_id) \
            .eq("intent", "WORKFLOW_ACTION") \
            .not_.is_("product_name", "null") \
            .order("created_at", desc=True) \
            .limit(n) \
            .execute()

        return result.data if result.data else []

    except Exception as e:
        print(f"[DB] Last N orders fetch failed: {e}")
        return []


async def get_last_order_from_orders(tenant_id: str, session_id: str) -> Optional[dict]:
    """
    Fetches the most recent confirmed order from the orders table.
    Includes order_id, invoice_url, total_with_gst — things messages table doesn't have.
    Used for SINGLE_ORDER_INQUIRY to show invoice link.
    """
    try:
        result = _get_client().table("orders") \
            .select("order_id, tenant_id, session_id, sender_name, product_name, quantity_value, quantity_unit, "
                    "unit_price, total_price, total_with_gst, invoice_url, status, created_at, items_count") \
            .eq("tenant_id", tenant_id) \
            .eq("session_id", session_id) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        return cast(dict, result.data[0]) if result.data else None

    except Exception as e:
        print(f"[DB] get_last_order_from_orders failed: {e}")
        return None


async def get_last_n_orders_from_orders(tenant_id: str, session_id: str, n: int = 2) -> list:
    """
    Fetches the last N confirmed orders from the orders table.
    Includes order_id, invoice_url, total_with_gst — things messages table doesn't have.
    Used for MULTI_ORDER_INQUIRY to show order history with invoice links.
    """
    try:
        result = _get_client().table("orders") \
            .select("order_id, tenant_id, session_id, sender_name, product_name, quantity_value, quantity_unit, "
                    "unit_price, total_price, total_with_gst, invoice_url, status, created_at, items_count") \
            .eq("tenant_id", tenant_id) \
            .eq("session_id", session_id) \
            .order("created_at", desc=True) \
            .limit(n) \
            .execute()

        return result.data if result.data else []

    except Exception as e:
        print(f"[DB] get_last_n_orders_from_orders failed: {e}")
        return []

# ── Product API Response Cache ────────────────────────────────────────────────
# Stores Products API JSON response in DB per SKU.
# Retrieved from DB instead of calling API again — no in-memory storage.
# Table: product_cache (tenant_id, sku, api_response, cached_at)

import json as _json

async def save_product_api_response(
    tenant_id:    str,
    sku:          str,
    api_response: list,
) -> bool:
    """
    Saves Products API response to product_cache table.

    Called after every successful API call so next request
    reads from DB instead of calling the API again.

    Uses UPSERT — if SKU already cached, updates with fresh data.
    """
    try:
        row = {
            "tenant_id":    tenant_id,
            "sku":          sku.upper(),
            "api_response": _json.dumps(api_response),
            "cached_at":    datetime.now(timezone.utc).isoformat(),
        }
        # UPSERT — update if exists, insert if not
        _get_client().table("product_cache") \
            .upsert(row, on_conflict="tenant_id,sku") \
            .execute()
        print(f"[DB] Product API response saved — SKU={sku}")
        return True
    except Exception as e:
        print(f"[DB] save_product_api_response failed: {e}")
        return False


async def save_product_api_responses_batch(
    tenant_id: str,
    items:     list,  # list of {"sku": str, "api_response": list}
) -> bool:
    """
    Saves MULTIPLE products to product_cache in a SINGLE Supabase upsert call,
    instead of one network round-trip per product.

    PERFORMANCE: A category search can return 50-100+ products. The old
    per-product save loop made 100 sequential network round-trips to Supabase
    (~150-300ms each), adding 15-20+ seconds to every large category search —
    confirmed via production timing logs. Batching this into one upsert()
    call with all rows reduces it to a single round-trip (~200-500ms total),
    regardless of how many products are in the batch.

    Args:
        tenant_id: Business isolation key.
        items: List of dicts, each with "sku" (str) and "api_response" (list,
               the same structure previously passed to save_product_api_response).

    Returns:
        True if the batch upsert succeeded, False otherwise.
        On failure, falls back to per-row saves so a single bad row doesn't
        lose the entire batch.
    """
    if not items:
        return True

    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        def _extract_product_name(api_resp):
            """Extract product_name from api_response for indexed column."""
            data = api_resp if isinstance(api_resp, list) else [api_resp]
            if data:
                first = data[0] if isinstance(data[0], dict) else {}
                return (first.get("product_name") or first.get("name") or "").lower()
            return ""

        # Bug 3 fix: deduplicate by SKU before building rows.
        # GraphRAG can return the same SKU twice in one response (e.g. LOS06Y(M2)).
        # A batch UPSERT with duplicate SKUs causes:
        #   "ON CONFLICT DO UPDATE cannot affect row a second time"
        # Dedup: last occurrence of each SKU wins (most complete data).
        seen_skus: dict = {}
        for item in items:
            sku = item.get("sku", "").upper()
            if sku:
                seen_skus[sku] = item   # last write wins
        deduped_items = list(seen_skus.values())
        if len(deduped_items) < len(items):
            print(f"[DB] Deduped {len(items)} → {len(deduped_items)} products "
                  f"(removed {len(items)-len(deduped_items)} duplicate SKUs)")

        rows = [
            {
                "tenant_id":    tenant_id,
                "sku":          item["sku"].upper(),
                "api_response": _json.dumps(item["api_response"]),
                "cached_at":    now_iso,
                "product_name": _extract_product_name(item["api_response"]),
            }
            for item in deduped_items
        ]

        if not rows:
            return True

        _get_client().table("product_cache") \
            .upsert(rows, on_conflict="tenant_id,sku") \
            .execute()
        print(f"[DB] Batch saved {len(rows)} products to product_cache in 1 call")
        return True

    except Exception as e:
        print(f"[DB] Batch save failed ({e}) — falling back to per-row saves")
        # Fallback: save one at a time so a single malformed row doesn't
        # silently drop the whole batch's worth of product cache data.
        ok = True
        for item in items:
            sku = item.get("sku")
            if sku:
                success = await save_product_api_response(tenant_id, sku, item["api_response"])
                ok = ok and success
        return ok


async def get_product_api_response(
    tenant_id: str,
    sku:       str,
    max_age_hours: int = 24,
) -> Optional[list]:
    """
    Retrieves cached Products API response from product_cache table.

    Returns None if:
    - SKU not in cache
    - Cache is older than max_age_hours (default 24 hours)
      → caller will re-fetch from API and update cache

    This means product data is refreshed every 24 hours automatically.
    """
    try:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        ).isoformat()

        result = _get_client().table("product_cache") \
            .select("api_response, cached_at") \
            .eq("tenant_id", tenant_id) \
            .eq("sku", sku.upper()) \
            .gt("cached_at", cutoff) \
            .limit(1) \
            .execute()

        if result.data:
            row = cast(dict, result.data[0])
            raw = row.get("api_response")
            cached_at = row.get("cached_at")
            data = _json.loads(raw) if isinstance(raw, str) else raw
            print(f"[DB] Product API response loaded from cache — SKU={sku} cached_at={cached_at}")
            return data

        print(f"[DB] No cached response for SKU={sku} — will fetch from API")
        return None

    except Exception as e:
        print(f"[DB] get_product_api_response failed: {e}")
        return None


async def get_cached_product_by_name(
    tenant_id:    str,
    product_name: str,
) -> Optional[dict]:
    """
    Looks up a cached product by exact name match, scoped to this tenant.

    Uses an ilike (case-insensitive) filter on the product_name column instead
    of fetching all rows and scanning in Python. Requires a product_name column
    on the product_cache table (or falls back to in-memory scan on older schemas).

    Returns:
        dict with product data including list_price, sku etc.
        None if not found in cache.
    """
    name_lower = product_name.lower().strip()

    # Tiered lookup strategy — most precise to least precise.
    # Stops at first match so "LED" doesn't accidentally return all LED products.
    #
    # Tier 1: Exact match         WHERE product_name = 'romy 12w bollard'
    # Tier 2: Prefix match        WHERE product_name LIKE 'romy 12w%'
    # Tier 3: Trigram ILIKE       WHERE product_name ILIKE '%romy 12w%'
    # Tier 4: Fallback in-memory  (below, in case DB column missing)

    def _parse_row(row: dict) -> Optional[dict]:
        raw  = row.get("api_response")
        data = _json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, list):
            data = [data]
        return cast(dict, data[0]) if data else None

    # Tier 1 + 2: exact and prefix — limit 1 (only one sensible match possible)
    # Tier 3: trigram — fetch up to 5 candidates, pick the BEST match.
    #   "Best" = longest product_name that still contains the query string,
    #   because a longer match is more specific (e.g. "Romy 12W Bollard" over "Romy").
    #   Ties broken by lexicographic order for determinism.
    tiers = [
        ("exact",   "eq",   lambda n: n,       1),
        ("prefix",  "ilike",lambda n: f"{n}%", 1),
        ("trigram", "ilike",lambda n: f"%{n}%",5),
    ]

    for tier_name, method, val_fn, limit in tiers:
        try:
            q = _get_client().table("product_cache") \
                .select("sku, api_response, cached_at, product_name") \
                .eq("tenant_id", tenant_id) \
                .limit(limit)
            q = q.eq("product_name", val_fn(name_lower)) if method == "eq" \
                else q.ilike("product_name", val_fn(name_lower))
            result = q.execute()
            if result.data:
                if tier_name == "trigram" and len(result.data) > 1:
                    # Pick best: most specific = longest name that contains query
                    # (longer = more words matched = less ambiguous)
                    def _score_row(r: dict) -> int:
                        rn = r.get("product_name", "") or ""
                        # Higher score = longer name = more specific match
                        return len(rn)
                    best_row = max(cast(list[dict], result.data), key=_score_row)
                else:
                    best_row = cast(dict, result.data[0])
                item = _parse_row(best_row)
                if item:
                    matched_name = best_row.get("product_name", "")
                    print(f"[DB] Product found in cache ({tier_name}) — "
                          f"'{product_name}' -> '{matched_name}' SKU={item.get('sku')}")
                    return item
        except Exception as e:
            print(f"[DB] Tier '{tier_name}' lookup failed: {e}")
            break   # DB error — go to fallback scan

    # Fallback path: in-memory scan for schemas without product_name column.
    # Fetches all rows for this tenant only (tenant_id scoped).
    try:
        result2 = _get_client().table("product_cache") \
            .select("sku, api_response, cached_at") \
            .eq("tenant_id", tenant_id) \
            .execute()

        if not result2.data:
            return None

        for row in result2.data:
            row_dict = cast(dict, row)
            raw  = row_dict.get("api_response")
            data = _json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(data, list):
                data = [data]
            for item in data:
                item_dict = cast(dict, item)
                cached_name = (item_dict.get("product_name") or "").lower().strip()
                if cached_name == name_lower:
                    print(f"[DB] Product found in cache by name (fallback scan) — '{product_name}' -> SKU={item_dict.get('sku')}")
                    return item_dict

        return None
    except Exception as e:
        print(f"[DB] get_cached_product_by_name failed: {e}")
        return None


async def save_graphrag_product_selection(
    tenant_id:  str,
    session_id: str,
    products:   list,
) -> bool:
    """
    Saves GraphRAG product search results to workflow_sessions table.

    When GraphRAG returns multiple sub-products for a category query
    (e.g. "garden lights" → 5 bollard light variants), we save the full
    list so when customer says "I want 2 units of 12C-2080" the pipeline
    already has all product details cached in DB.

    Status = PRODUCT_SELECTION (distinct from WORKFLOW_PENDING).
    Expires in 20 minutes.
    """
    try:
        now_utc    = datetime.now(timezone.utc)
        expires_at = (now_utc + timedelta(hours=0, minutes=20)).isoformat()

        items_json_str = json.dumps([
            {
                "product_name":               p.get("name"),
                "sku":                        p.get("sku"),
                "quantity_value":             None,
                "quantity_unit":              None,
                "list_price":                 float(p.get("price_num", 0)),
                "regular_price":              p.get("regular_price", p.get("price_num", 0)),
                "discount_pct":               p.get("discount_percentage", 0),
                "image_url":                  p.get("image_url"),
                "product_url":                p.get("url"),
                "rating":                     p.get("rating", 0),
                "review_count":               p.get("review_count", 0),
                "feature_descriptions":       p.get("feature_descriptions", ""),
                # global_offers — store-wide value-based discount tiers used by
                # the negotiator to offer REAL discounts instead of hardcoded ones.
                # e.g. "Extra 2% OFF | Rs 2500 ... Extra 5% OFF | Rs 7500 ..."
                "global_offers":              p.get("global_offers", ""),
                "warranty":                   p.get("warranty", ""),
                "replacement_exchange_policy": p.get("replacement_exchange_policy", ""),
            }
            for p in products
        ])

        # CRITICAL: order by created_at desc so we always find the SAME row
        # that get_graphrag_product_selection() would read back. Without this
        # ordering, an UPDATE could silently target a different, older row
        # than the one later reads return — causing position lookups to
        # resolve against stale, unrelated product data.
        existing = _get_client().table("workflow_sessions") \
            .select("id") \
            .eq("tenant_id",  tenant_id) \
            .eq("session_id", session_id) \
            .eq("status", "PRODUCT_SELECTION") \
            .order("created_at", desc=True) \
            .execute()

        row = {
            "tenant_id":      tenant_id,
            "session_id":     session_id,
            "status":         "PRODUCT_SELECTION",
            "product_name":   None,
            "quantity_value": None,
            "quantity_unit":  None,
            "delivery_date":  None,
            "missing_fields": json.dumps(["product_selection", "quantity"]),
            "items_json":     items_json_str,
            "expires_at":     expires_at,
            "updated_at":     now_utc.isoformat(),
        }

        if existing.data:
            # Update the most recent row (first in desc-ordered results).
            most_recent_row = cast(dict, existing.data[0])
            most_recent_id = most_recent_row["id"]
            _get_client().table("workflow_sessions") \
                .update(row) \
                .eq("id", most_recent_id) \
                .execute()

            # Defensively remove any OTHER stale PRODUCT_SELECTION rows for
            # this session so save/read can never disagree on which is current.
            if len(existing.data) > 1:
                stale_ids = [cast(dict, r)["id"] for r in existing.data[1:]]
                _get_client().table("workflow_sessions") \
                    .delete() \
                    .in_("id", stale_ids) \
                    .execute()
                print(f"[DB] Removed {len(stale_ids)} stale PRODUCT_SELECTION row(s) for {session_id}")
        else:
            _get_client().table("workflow_sessions") \
                .insert(row) \
                .execute()

        print(f"[DB] PRODUCT_SELECTION saved — {len(products)} options for {session_id}")
        return True

    except Exception as e:
        print(f"[DB] save_graphrag_product_selection failed: {e}")
        return False


async def get_graphrag_product_selection(
    tenant_id:  str,
    session_id: str,
) -> Optional[list]:
    """
    Retrieves saved GraphRAG product selection options from workflow_sessions.
    Returns list of product dicts or None if not found/expired.
    Used when customer picks a product by number after seeing the list.
    """
    try:
        now_utc = datetime.now(timezone.utc).isoformat()

        result = _get_client().table("workflow_sessions") \
            .select("items_json") \
            .eq("tenant_id",  tenant_id) \
            .eq("session_id", session_id) \
            .eq("status", "PRODUCT_SELECTION") \
            .gt("expires_at", now_utc) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        if result.data:
            first_row = cast(dict, result.data[0])
            items_json = first_row.get("items_json")
            if items_json:
                products = json.loads(cast(str, items_json))
                print(f"[DB] PRODUCT_SELECTION loaded — {len(products)} options")
                return products

        return None

    except Exception as e:
        print(f"[DB] get_graphrag_product_selection failed: {e}")
        return None


async def save_category_selection(
    tenant_id:  str,
    session_id: str,
    categories: list,
) -> bool:
    """
    Saves the list of category names offered during a GraphRAG clarification step.
    Status = CATEGORY_SELECTION. Expires in 20 minutes.
    """
    try:
        now_utc    = datetime.now(timezone.utc)
        expires_at = (now_utc + timedelta(minutes=20)).isoformat()

        existing = _get_client().table("workflow_sessions") \
            .select("id") \
            .eq("tenant_id",  tenant_id) \
            .eq("session_id", session_id) \
            .eq("status", "CATEGORY_SELECTION") \
            .order("created_at", desc=True) \
            .execute()

        row = {
            "tenant_id":      tenant_id,
            "session_id":     session_id,
            "status":         "CATEGORY_SELECTION",
            "product_name":   None,
            "quantity_value": None,
            "quantity_unit":  None,
            "items_json":     json.dumps(categories),
            "expires_at":     expires_at,
            "updated_at":     now_utc.isoformat(),
        }

        if existing.data:
            most_recent_row = cast(dict, existing.data[0])
            most_recent_id = most_recent_row["id"]
            _get_client().table("workflow_sessions") \
                .update(row) \
                .eq("id", most_recent_id) \
                .execute()
            if len(existing.data) > 1:
                stale_ids = [cast(dict, r)["id"] for r in existing.data[1:]]
                _get_client().table("workflow_sessions") \
                    .delete() \
                    .in_("id", stale_ids) \
                    .execute()
        else:
            _get_client().table("workflow_sessions") \
                .insert(row) \
                .execute()

        print(f"[DB] CATEGORY_SELECTION saved — {len(categories)} options for {session_id}")
        return True

    except Exception as e:
        print(f"[DB] save_category_selection failed: {e}")
        return False


async def get_category_selection(
    tenant_id:  str,
    session_id: str,
) -> Optional[list]:
    """
    Retrieves stored category options. Returns list of strings or None if not found/expired.
    """
    try:
        now_utc = datetime.now(timezone.utc).isoformat()

        result = _get_client().table("workflow_sessions") \
            .select("items_json") \
            .eq("tenant_id",  tenant_id) \
            .eq("session_id", session_id) \
            .eq("status", "CATEGORY_SELECTION") \
            .gt("expires_at", now_utc) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        if result.data:
            first_row = cast(dict, result.data[0])
            items_json = first_row.get("items_json")
            if items_json:
                categories = json.loads(cast(str, items_json))
                print(f"[DB] CATEGORY_SELECTION loaded — {len(categories)} options")
                return categories

        return None

    except Exception as e:
        print(f"[DB] get_category_selection failed: {e}")
        return None


async def clear_category_selection(
    tenant_id:  str,
    session_id: str,
) -> bool:
    """Clears the category selection state after a category is picked."""
    try:
        _get_client().table("workflow_sessions") \
            .update({"status": "COMPLETED", "updated_at": datetime.now(timezone.utc).isoformat()}) \
            .eq("tenant_id",  tenant_id) \
            .eq("session_id", session_id) \
            .eq("status", "CATEGORY_SELECTION") \
            .execute()
        print(f"[DB] CATEGORY_SELECTION cleared for {session_id}")
        return True
    except Exception as e:
        print(f"[DB] clear_category_selection failed: {e}")
        return False


# ── Negotiation State ─────────────────────────────────────────────────────────
# Stores active negotiation state in workflow_sessions table.
# Status: NEGOTIATING (active) — expires after 30 minutes of inactivity.
# Tracks: rounds, quantity, last_offer_price, floor_price, product details.

async def save_negotiation_state(
    tenant_id:    str,
    session_id:   str,
    state:        dict,
) -> bool:
    """
    Saves or updates the negotiation state for this session.
    State dict contains: rounds, quantity, last_offer_price, floor_price,
    product_name, base_price.
    """
    try:
        from datetime import datetime, timezone, timedelta
        now_utc    = datetime.now(timezone.utc)
        expires_at = (now_utc + timedelta(minutes=30)).isoformat()

        row = {
            "tenant_id":      tenant_id,
            "session_id":     session_id,
            "status":         "NEGOTIATING",
            "product_name":   state.get("product_name"),
            "quantity_value": state.get("quantity"),
            "items_json":     json.dumps(state),
            "expires_at":     expires_at,
            "updated_at":     now_utc.isoformat(),
        }

        existing = _get_client().table("workflow_sessions")             .select("id")             .eq("tenant_id",  tenant_id)             .eq("session_id", session_id)             .eq("status", "NEGOTIATING")             .limit(1)             .execute()

        if existing.data:
            existing_row = cast(dict, existing.data[0])
            _get_client().table("workflow_sessions")                 .update(row)                 .eq("id", existing_row["id"])                 .execute()
        else:
            _get_client().table("workflow_sessions")                 .insert(row)                 .execute()

        print(f"[DB] Negotiation state saved — rounds={state.get('rounds')} "
              f"product={state.get('product_name')} qty={state.get('quantity')}")
        return True
    except Exception as e:
        print(f"[DB] save_negotiation_state failed: {e}")
        return False


async def get_negotiation_state(
    tenant_id:  str,
    session_id: str,
) -> Optional[dict]:
    """
    Retrieves active negotiation state. Returns None if no active negotiation.
    """
    try:
        now_utc = datetime.now(timezone.utc).isoformat()
        result  = _get_client().table("workflow_sessions")             .select("items_json")             .eq("tenant_id",  tenant_id)             .eq("session_id", session_id)             .eq("status", "NEGOTIATING")             .gt("expires_at", now_utc)             .order("updated_at", desc=True)             .limit(1)             .execute()

        if result.data:
            first_row = cast(dict, result.data[0])
            items_json = first_row.get("items_json")
            if items_json:
                state = json.loads(cast(str, items_json))
                print(f"[DB] Negotiation state loaded — rounds={state.get('rounds')} "
                      f"product={state.get('product_name')}")
                return state

        return None
    except Exception as e:
        print(f"[DB] get_negotiation_state failed: {e}")
        return None


async def clear_negotiation_state(
    tenant_id:  str,
    session_id: str,
) -> bool:
    """
    Clears the negotiation state after order is placed or negotiation ends.
    """
    try:
        _get_client().table("workflow_sessions")             .update({"status": "COMPLETED", "updated_at": datetime.now(timezone.utc).isoformat()})             .eq("tenant_id",  tenant_id)             .eq("session_id", session_id)             .eq("status", "NEGOTIATING")             .execute()
        print(f"[DB] Negotiation state cleared for {session_id}")
        return True
    except Exception as e:
        print(f"[DB] clear_negotiation_state failed: {e}")
        return False


async def update_order_invoice_url(order_id: str, tenant_id: str, invoice_url: str) -> bool:
    """Updates the invoice URL for a specific order in the database."""
    try:
        _get_client().table("orders") \
            .update({"invoice_url": invoice_url}) \
            .eq("order_id", order_id) \
            .eq("tenant_id", tenant_id) \
            .execute()
        print(f"[DB] Invoice URL updated for order {order_id}: {invoice_url}")
        return True
    except Exception as e:
        print(f"[DB] update_order_invoice_url failed: {e}")
        return False


async def save_last_discussed_product(
    tenant_id: str,
    session_id: str,
    product_name: str,
) -> bool:
    """Saves the last discussed product name in workflow_sessions table."""
    try:
        now_utc    = datetime.now(timezone.utc)
        # 30 minutes — long enough to cover a natural conversation, short enough
        # to prevent a product from a previous session poisoning a new one.
        # (Was 24 hours — caused "Zenia SKY" to appear when customer asked
        # "which is better?" about Romy/Electra in a brand-new session.)
        expires_at = (now_utc + timedelta(minutes=30)).isoformat()
        row = {
            "tenant_id":      tenant_id,
            "session_id":     session_id,
            "status":         "LAST_DISCUSSED_PRODUCT",
            "product_name":   product_name,
            "expires_at":     expires_at,
            "updated_at":     now_utc.isoformat(),
        }
        # Update or insert
        existing = _get_client().table("workflow_sessions") \
            .select("id") \
            .eq("tenant_id",  tenant_id) \
            .eq("session_id", session_id) \
            .eq("status", "LAST_DISCUSSED_PRODUCT") \
            .limit(1) \
            .execute()

        if existing.data:
            existing_row = cast(dict, existing.data[0])
            _get_client().table("workflow_sessions") \
                .update(row) \
                .eq("id", existing_row["id"]) \
                .execute()
        else:
            _get_client().table("workflow_sessions") \
                .insert(row) \
                .execute()
        print(f"[DB] Saved last discussed product: {product_name}")
        return True
    except Exception as e:
        print(f"[DB] save_last_discussed_product failed: {e}")
        return False


async def get_last_discussed_product(tenant_id: str, session_id: str) -> Optional[str]:
    """Retrieves the last discussed product name from workflow_sessions table if not expired."""
    try:
        now_utc = datetime.now(timezone.utc).isoformat()
        result = _get_client().table("workflow_sessions") \
            .select("product_name") \
            .eq("tenant_id",  tenant_id) \
            .eq("session_id", session_id) \
            .eq("status", "LAST_DISCUSSED_PRODUCT") \
            .gt("expires_at", now_utc) \
            .order("updated_at", desc=True) \
            .limit(1) \
            .execute()
        if result.data:
            first_row = cast(dict, result.data[0])
            return cast(Optional[str], first_row.get("product_name"))
        return None
    except Exception as e:
        print(f"[DB] get_last_discussed_product failed: {e}")
        return None


        #

# ══════════════════════════════════════════════════════════════════════════════
# TENANT OFFERS TABLE
# ══════════════════════════════════════════════════════════════════════════════
# Stores global_offers text and parsed tiers per tenant.
# global_offers is identical for every product from the same store —
# storing it once per tenant avoids redundant data in product cache.
#
# SQL to create the table (run once in Supabase SQL Editor):
#   CREATE TABLE IF NOT EXISTS tenant_offers (
#       id          BIGSERIAL PRIMARY KEY,
#       tenant_id   TEXT NOT NULL UNIQUE,
#       offers_text TEXT NOT NULL,
#       tiers_json  TEXT,
#       updated_at  TIMESTAMPTZ DEFAULT NOW()
#   );
#   CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_offers_tenant
#   ON tenant_offers(tenant_id);


async def save_tenant_offers(
    tenant_id:   str,
    offers_text: str,
    tiers_json:  Optional[str] = None,
) -> bool:
    """
    Upserts the global_offers text (and optionally pre-parsed tiers JSON)
    for a tenant into the tenant_offers table.

    Called whenever GraphRAG returns products — extracts global_offers from
    the first product (they're all the same for the same store) and persists
    it so the negotiator can always find real discount tiers.

    Args:
        tenant_id:   Business isolation key (e.g. "inventaa")
        offers_text: Raw global_offers string from GraphRAG
        tiers_json:  Optional pre-parsed tiers as JSON string
                     e.g. "[[2500, 2], [7500, 5], [14500, 8]]"

    Returns True on success, False on failure.
    """
    if not offers_text or not offers_text.strip():
        return False
    try:
        row = {
            "tenant_id":   tenant_id,
            "offers_text": offers_text.strip(),
            "tiers_json":  tiers_json,
            "updated_at":  datetime.now(timezone.utc).isoformat(),
        }
        # Upsert — update if already exists, insert if not
        _get_client().table("tenant_offers") \
            .upsert(row, on_conflict="tenant_id") \
            .execute()
        print(f"[DB] tenant_offers saved for {tenant_id}")
        return True
    except Exception as e:
        print(f"[DB] save_tenant_offers failed: {e}")
        return False


async def get_tenant_offers(tenant_id: str) -> Optional[dict]:
    """
    Fetches the stored global_offers for a tenant.

    Returns:
        dict with keys "offers_text" and "tiers_json" if found
        None if not stored yet
    """
    try:
        result = _get_client().table("tenant_offers") \
            .select("offers_text, tiers_json") \
            .eq("tenant_id", tenant_id) \
            .limit(1) \
            .execute()
        if result.data:
            return cast(dict, result.data[0])
        return None
    except Exception as e:
        print(f"[DB] get_tenant_offers failed: {e}")
        return None