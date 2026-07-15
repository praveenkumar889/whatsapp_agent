# ai/graphrag_handler.py — GraphRAG API integration and product list handling
#
# Extracted from main.py to keep the orchestrator lightweight.
# Contains: call_graphrag_api, _send_structured_product_list, _coerce_pythonic_dict
# All imports must be explicit — no globals from main.py.

import ast
import asyncio
import json
import re
import time
import httpx
from typing import Optional

from openai import AzureOpenAI
from config import (
    GRAPHRAG_API_URL,
    MCP_SERVER_URL, USE_MCP_SERVER,
    AZURE_AI_ENDPOINT, AZURE_AI_API_KEY,
    AZURE_OPENAI_DEPLOYMENT, AZURE_AI_API_VERSION,
)

from messaging import send_reply, send_image
from ai.product_followup import _try_resolve_product_followup
from db.session_store import (
    save_graphrag_product_selection,
    save_product_api_responses_batch,
    save_tenant_offers,
    save_last_discussed_product,
    save_outbound_message,
    get_graphrag_product_selection,
    get_negotiation_state,
    save_category_selection,
    get_category_selection,
    clear_category_selection,
    save_dialog_state,
    get_dialog_state,
)

def _load_category_prompt(incoming, cat_list: str) -> str:
    """Migration 014: load category_matcher_prompt from DB."""
    from db.prompt_store import get_prompt
    return get_prompt(incoming, "category_matcher_prompt", cat_list=cat_list)



_client = AzureOpenAI(
    azure_endpoint = AZURE_AI_ENDPOINT,
    api_key        = AZURE_AI_API_KEY,
    api_version    = AZURE_AI_API_VERSION,
    timeout        = 30.0,
    max_retries    = 0,
)


async def _send_structured_product_list(incoming, products: list) -> str:
    """
    Builds and sends the full product-list response: caches products,
    saves the selection for follow-up picking, sends image cards for the
    first 3 products, and returns the numbered text summary.

    Extracted so it can be reused both for the initial GraphRAG response
    AND for a successful retry response — fixes a bug where a successful
    retry with real products silently fell through to returning the
    ORIGINAL error text instead of ever rendering the retried products.
    """
    print(f"[GRAPHRAG] Got {len(products)} products from structured response")

    if len(products) == 1:
        try:
            from db.session_store import save_last_discussed_product
            pname = products[0].get("name") or products[0].get("product_name")
            if pname:
                await save_last_discussed_product(incoming.tenant_id, incoming.session_id, pname)
        except Exception as e:
            print(f"[GRAPHRAG] Failed to save single product context: {e}")

    try:
        _t_cache_save_start = time.monotonic()
        batch_items = []
        for p in products:
            sku = p.get("sku")
            if sku:
                cached_item = [{
                    "product_name":               p.get("name"),
                    "list_price":                 float(p.get("price_num", 0)),
                    "sku":                        sku,
                    "image_url":                  p.get("image_url"),
                    "installation_url":           p.get("installation_url"),
                    "product_url":                p.get("url"),
                    "discount_pct":               p.get("discount_percentage", 0),
                    "regular_price":              p.get("regular_price", p.get("price_num", 0)),
                    "features":                   [],
                    "specs":                      [],
                    "review_count":               p.get("review_count", 0),
                    "rating":                     p.get("rating", 0),
                    "policies":                   [],
                    "faqs":                       [],
                    "warranties":                 [],
                    "warranty":                   p.get("warranty", ""),
                    "replacement_exchange_policy": p.get("replacement_exchange_policy", ""),
                    "feature_descriptions":       p.get("feature_descriptions", ""),
                }]
                batch_items.append({"sku": sku, "api_response": cached_item})

        from db.session_store import save_product_api_responses_batch
        await save_product_api_responses_batch(incoming.tenant_id, batch_items)
        print(f"[TIMING] Product cache batch save ({len(batch_items)} products): {time.monotonic() - _t_cache_save_start:.2f}s")
    except Exception as e:
        print(f"[GRAPHRAG] Cache save failed (non-critical): {e}")

    try:
        await save_graphrag_product_selection(
            tenant_id  = incoming.tenant_id,
            session_id = incoming.session_id,
            products   = products,
        )
        print(f"[GRAPHRAG] Product selection saved to workflow_sessions")
    except Exception as e:
        print(f"[GRAPHRAG] Selection save failed (non-critical): {e}")

    try:
        _go = next((p.get("global_offers") for p in products if p.get("global_offers")), None)
        if _go:
            await save_tenant_offers(tenant_id=incoming.tenant_id, offers_text=_go)
    except Exception as e:
        print(f"[GRAPHRAG] tenant_offers save failed (non-critical): {e}")

    MAX_IMAGE_PRODUCTS = 3
    for i, p in enumerate(products, 1):
        if i > MAX_IMAGE_PRODUCTS:
            break

        img_url   = p.get("image_url")
        name      = p.get("name", "Product")
        price     = p.get("price_num", 0)
        reg_price = p.get("regular_price", price)
        discount  = p.get("discount_percentage", 0)
        rating    = p.get("rating", 0)
        reviews   = p.get("review_count", 0)

        caption = f"{i}. {name}\nRs.{float(price):,.0f}"
        if discount:
            caption += f" (Save {discount}% off Rs.{float(str(reg_price).replace(',','')):,.0f})"
        if rating:
            caption += f"\n⭐ {rating} ({reviews} reviews)"

        if img_url:
            img_wamid = await send_image(incoming, img_url, caption)
            if img_wamid:
                print(f"[GRAPHRAG] Image sent for product {i}: {name} — wamid={img_wamid}")
                await save_outbound_message(
                    tenant_id     = incoming.tenant_id,
                    session_id    = incoming.session_id,
                    message_id    = img_wamid,
                    text          = caption,
                    media_url     = img_url,
                    original_type = "image",
                    region        = incoming.region,
                )
        else:
            reply_wamid = await send_reply(incoming, caption)
            if reply_wamid:
                print(f"[GRAPHRAG] No image for product {i}: {name} — sent text card wamid={reply_wamid}")
                await save_outbound_message(
                    tenant_id  = incoming.tenant_id,
                    session_id = incoming.session_id,
                    message_id = reply_wamid,
                    text       = caption,
                    region        = incoming.region,
                )

    lines = [f"Here are the options for you, {incoming.sender_name}! 💡\n"]
    for i, p in enumerate(products, 1):
        name      = p.get("name", "Product")
        price     = p.get("price_num", 0)
        reg_price = p.get("regular_price", price)
        discount  = p.get("discount_percentage", 0)
        if i <= MAX_IMAGE_PRODUCTS:
            entry = f"*{i}.* {name} — Rs.{float(price):,.0f}"
            if discount:
                entry += f" (Save {discount}% off Rs.{float(str(reg_price).replace(',','')):,.0f})"
            lines.append(entry)
        else:
            lines.append(f"*{i}.* {name} — Rs.{float(price):,.0f}")

    lines.append(
        f"\nReply with the product *number* or *name* to know more or place an order."
    )

    summary_text = "\n".join(lines)
    if len(summary_text) > 4096:
        summary_text = summary_text[:4090] + "\n…"

    return summary_text


def _coerce_pythonic_dict(value):
    """
    GraphRAG is expected to return structured shapes (list of product dicts,
    or a {"status": "needs_clarification", ...} dict) as real JSON.

    In production we've seen it instead return that SAME dict already
    stringified on GraphRAG's side (Python's str(dict) — single quotes,
    not valid JSON) inside response_text. Because that arrives as a plain
    str, `isinstance(response_text, dict)` below is False, every structured
    check is skipped, and the literal Python dict text gets sent to the
    customer verbatim.

    This safely converts a string that LOOKS like a Python dict literal
    back into a real dict so the existing needs_clarification / product-list
    handling below can catch it. Anything that isn't a clean dict literal
    is returned unchanged — never raises, never guesses.
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = ast.literal_eval(stripped)
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, SyntaxError):
                pass
    return value




def _clean_category_name(cat: str) -> str:
    if not cat:
        return ""
    # Strip descriptions after en-dash (–), em-dash (—), hyphen (-), or colon (:)
    cleaned = re.split(r'\s*[–—\-:]\s+(?=[a-z])|\s+[–—\-:]\s*', cat)[0].strip()
    return cleaned.strip('*').strip()


def _extract_category_options(text: str) -> Optional[list]:
    """
    Extracts a list of category options from a GraphRAG BROWSE_CATEGORY reply.
    """
    if not text:
        return None

    categories = []
    for line in text.split("\n"):
        line = line.strip()
        m = re.match(r'^[•\-\*·]\s+(.+)$', line)
        if not m:
            m = re.match(r'^\*\*\d+\.\*\*\s+(.+)$', line)
        if not m:
            m = re.match(r'^\d+[.)]\s+(.+)$', line)
        if m:
            cat = _clean_category_name(m.group(1).strip())
            if cat:
                categories.append(cat)

    return categories if len(categories) >= 2 else None


def _parse_plaintext_categories(text: str) -> Optional[list]:
    """
    Detects and parses a plain-text bullet list of category options from a GraphRAG
    clarification response (when GraphRAG returns text instead of a needs_clarification dict).

    Returns list of category name strings when bullet markers are present AND at least
    one clarification keyword is found. Returns None otherwise.
    """
    _clarify_keywords = (
        "specify", "clarif", "which", "category", "categories",
        "type", "choose", "select", "let me know", "narrow",
    )
    if not any(kw in text.lower() for kw in _clarify_keywords):
        return None

    return _extract_category_options(text)


async def _resolve_category_from_message(incoming, text: str, categories: list) -> Optional[str]:
    """
    Matches a customer message to one of the stored category options.

    Priority:
    1. Index-based selection ("5", "#5", "option 5")
    2. Exact or substring string match (case-insensitive)
    3. LLM semantic mapping as fallback
    """
    text_stripped = text.strip()

    # 1. Index-based
    m = re.match(r'^[#]?\s*(\d+)\.?\s*$', text_stripped)
    if not m:
        m = re.match(r'^(?:option|item|number|no\.?)\s+(\d+)\.?\s*$', text_stripped, re.IGNORECASE)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(categories):
            cleaned = _clean_category_name(categories[idx])
            print(f"[CATEGORY] Index match: {idx+1} → '{cleaned}'")
            return cleaned

    # 2. String match
    text_lower = text_stripped.lower()
    for cat in categories:
        if cat.lower() == text_lower:
            cleaned = _clean_category_name(cat)
            print(f"[CATEGORY] Exact match: '{cleaned}'")
            return cleaned

    # Collect all substring matches
    substring_matches = []
    for cat in categories:
        if cat.lower() in text_lower or text_lower in cat.lower():
            substring_matches.append(cat)
    if len(substring_matches) == 1:
        cleaned = _clean_category_name(substring_matches[0])
        print(f"[CATEGORY] Unambiguous substring match: '{cleaned}'")
        return cleaned

    # Collect all word-overlap matches
    text_words = {w for w in text_lower.split() if len(w) > 3}
    best_overlap = 0
    best_cats = []
    for cat in categories:
        cat_words = {w for w in cat.lower().split() if len(w) > 3}
        overlap = len(text_words & cat_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_cats = [cat]
        elif overlap == best_overlap and overlap > 0:
            best_cats.append(cat)
            
    if len(best_cats) == 1 and (best_overlap >= 2 or (best_overlap >= 1 and len(text_words) <= 2)):
        cleaned = _clean_category_name(best_cats[0])
        print(f"[CATEGORY] Unambiguous word-overlap match ({best_overlap} words): '{cleaned}'")
        return cleaned
    
    if len(substring_matches) > 1 or len(best_cats) > 1:
        print(f"[CATEGORY] Ambiguous matches found (substrings: {len(substring_matches)}, overlaps: {len(best_cats)}). Deferring to broad query.")

    # 3. LLM semantic fallback
    try:
        from config import AZURE_OPENAI_DEPLOYMENT
        cat_list = "\n".join(f"{i+1}. {c}" for i, c in enumerate(categories))
        resp = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model       = AZURE_OPENAI_DEPLOYMENT,
                max_tokens  = 30,
                temperature = 0,
                messages    = [
                    {"role": "system", "content": _load_category_prompt(incoming, cat_list)},
                    {"role": "user", "content": text_stripped},
                ],
            )
        )
        content = resp.choices[0].message.content
        llm_choice = content.strip().strip("*").strip() if content else ""
        if llm_choice and llm_choice.upper() != "NONE":
            for cat in categories:
                if cat.lower() == llm_choice.lower() or llm_choice.lower() in cat.lower():
                    cleaned = _clean_category_name(cat)
                    print(f"[CATEGORY] LLM match: '{cleaned}'")
                    return cleaned
    except Exception as e:
        print(f"[CATEGORY] LLM mapping failed: {e}")

    return None


from ai.timing import log_timing

@log_timing("GraphRAGHandler.call_graphrag_api")
async def call_graphrag_api(incoming, session_history: Optional[list] = None, state = None, graphrag_url: Optional[str] = None) -> str:
    """
    Calls the Hybrid RAG Agent API for ALL product-related queries.

    graphrag_url: Per-tenant endpoint override. When provided (loaded from the
                  tenants table), this takes precedence over the global
                  GRAPHRAG_API_URL env var. Allows each business to connect
                  to their own Neo4j/GraphRAG deployment.

    HANDLES:
        - Product browsing by category or name
        - Product follow-up questions
        - Ordering and quantity confirmation
        - Picking from a numbered list
    """
    try:
        # ── Pre-check: is this a follow-up about a previously shown product? ──
        # If customer already saw a numbered list and is asking "is it aluminum?"
        # or "tell me more about Romy" — resolve that before calling GraphRAG.
        if session_history:
            follow_up_reply = await _try_resolve_product_followup(incoming, session_history)
            if follow_up_reply == "__ALREADY_HANDLED__":
                # Image/link/installation already sent directly to WhatsApp —
                # return empty string so the outer pipeline sends nothing more,
                # but does NOT fall through to GraphRAG.
                return ""
            if follow_up_reply:
                return follow_up_reply

        # ── Send original query to GraphRAG ──────────────────────────────────
        # GraphRAG uses Neo4j semantic search which understands natural language.
        # We send the customer's original message as-is — no stripping, no cleaning.
        # "i want to order outdoor lights?" → GraphRAG receives exactly this.
        graphrag_text = incoming.text

        # Only handle quote-reply prefix — strip [Quoting:...] to get actual message
        if graphrag_text.startswith("[Quoting:") and "\n" in graphrag_text:
            actual_msg = graphrag_text.split("\n", 1)[1].strip()
            if actual_msg:
                print(f"[GRAPHRAG] Quote-reply — using actual message: '{actual_msg[:60]}'")
                graphrag_text = actual_msg

        # ── Category selection resolution ──────────────────────────────────
        # If GraphRAG previously returned a category clarification list, the customer's
        # next message is their category pick — match it and rewrite the query so
        # GraphRAG returns products for that specific category.
        _client_intent_data = None
        _cat_options = await get_category_selection(incoming.tenant_id, incoming.session_id)
        if _cat_options:
            _matched_cat = await _resolve_category_from_message(incoming, incoming.text, _cat_options)
            if _matched_cat:
                clean_cat = _clean_category_name(_matched_cat)
                print(f"[CATEGORY] Resolved '{clean_cat}' — rewriting query and setting client intent_data")
                await clear_category_selection(incoming.tenant_id, incoming.session_id)
                graphrag_text = f"Show products in {clean_cat}"
                _client_intent_data = {
                    "intent": "find_product",
                    "category_keywords": [clean_cat],
                    "filters": {"category": clean_cat}
                }
            else:
                print(f"[CATEGORY] No match found — clearing state, proceeding with original query")
                await clear_category_selection(incoming.tenant_id, incoming.session_id)

        # ── Build payload matching messages table schema ───────────────────
        payload = {
            "id":                  incoming.message_id,
            "tenant_id":           incoming.tenant_id,
            "message_id":          incoming.message_id,
            "session_id":          incoming.session_id,
            "channel":             incoming.channel,
            "timestamp_unix":      incoming.timestamp,
            "region":              incoming.region,
            "original_type":       incoming.original_type,
            "text":                graphrag_text,
            "intent":              "FAQ_KNOWLEDGE",
            "confidence":          0.95,
            "product_name":        None,
            "quantity_value":      None,
            "quantity_unit":       None,
            "delivery_date":       None,
            "missing_entities":    [],
            "reply_text":          None,
            "replied_at":          None,
            "sender_name":         incoming.sender_name,
            "sender_phone_number": incoming.sender_phone,
            "trace_id":            incoming.trace_id,
            "received_at":         incoming.received_at,
            "direction":           "inbound",
            "invoice_number":      None,
            "payment_reference":   None,
            "dialogue_state":      state.__dict__ if state else None,
        }

        # Resolve effective GraphRAG URL:
        # 1. Per-tenant URL passed from main.py (from tenants table column) — highest priority
        # 2. Global GRAPHRAG_API_URL env var — fallback
        effective_graphrag_url = graphrag_url or GRAPHRAG_API_URL

        data = None
        is_mcp = USE_MCP_SERVER or (effective_graphrag_url and ("/mcp" in effective_graphrag_url or "/sse" in effective_graphrag_url))
        mcp_url = effective_graphrag_url if effective_graphrag_url and ("/mcp" in effective_graphrag_url or "/sse" in effective_graphrag_url) else MCP_SERVER_URL
        if is_mcp:
            print(f"[GRAPHRAG-MCP] Routing via MCP Server at {mcp_url}")
            from ai.mcp_client import query_mcp_catalog, get_taxonomy_context_mcp
            
            if _client_intent_data is None and state and getattr(state, "intent", None):
                intent_val = str(state.intent).lower()
                cat_kws = [state.category] if getattr(state, "category", "") else []
                intent_to_use = "browse_category" if cat_kws and not getattr(state, "product_name", "") else (intent_val if intent_val in ("browse_category", "find_product", "get_product_info", "check_policy") else "find_product")
                _client_intent_data = {
                    "intent": intent_to_use,
                    "category_keywords": cat_kws,
                    "feature_keywords": [],
                    "product_name": state.product_name if getattr(state, "product_name", "") else None,
                    "filters": {"category": None if intent_to_use == "browse_category" else (cat_kws[0] if cat_kws else None)},
                    "preferences": {}
                }
                print(f"[GRAPHRAG-CLIENT] Using dynamic intent data from tenant state: {json.dumps(_client_intent_data)}")
            elif _client_intent_data is None:
                from ai.intent_classifier import classify_user_intent_client_side
                _client_intent_data = await classify_user_intent_client_side(
                    query=graphrag_text,
                    taxonomy_hints=getattr(state, "taxonomy_hints", None) if state else None,
                    incoming=incoming
                )
                print(f"[GRAPHRAG-CLIENT] Pre-classified intent data via Supabase prompt: {json.dumps(_client_intent_data)}")

            mcp_res = await query_mcp_catalog(
                query=graphrag_text,
                session_id=incoming.session_id,
                tenant_id=incoming.tenant_id,
                server_url=mcp_url,
                intent_data=_client_intent_data,
                state=state.__dict__ if state and hasattr(state, "__dict__") else (state if isinstance(state, dict) else None)
            )
            if mcp_res and mcp_res.get("status") == "success":
                products = mcp_res.get("products", [])
                response_str = mcp_res.get("response", "")
                if products and len(products) > 0:
                    data = {"response_text": products, "intent": mcp_res.get("intent")}
                elif response_str:
                    data = {"response_text": response_str, "intent": mcp_res.get("intent")}
                else:
                    data = {"response_text": []}
            else:
                print(f"[GRAPHRAG-MCP] MCP query did not return success. mcp_res={mcp_res}")
                if mcp_res and mcp_res.get("response"):
                    data = {"response_text": mcp_res.get("response"), "intent": mcp_res.get("intent")}
                elif mcp_res and mcp_res.get("error"):
                    data = {"response_text": f"Sorry, I couldn't search the catalog right now: {mcp_res.get('error')}"}
                else:
                    data = {"response_text": []}

        if data is None and not is_mcp:
            if not effective_graphrag_url:

                print(f"[GRAPHRAG] No GraphRAG URL configured for tenant {incoming.tenant_id}")
                support = getattr(incoming, 'support_email', None) or incoming.biz_name
                return (
                    f"I'm not able to look up products right now, {incoming.sender_name}. "
                    f"Please contact *{support}* for assistance."
                )

            print(f"[GRAPHRAG] Calling {effective_graphrag_url[:60]} for: '{graphrag_text[:60]}'")

            # GraphRAG uses LangChain + Neo4j — can take 40-60 seconds
            graphrag_timeout = httpx.Timeout(connect=10.0, read=90.0, write=10.0, pool=10.0)
            async with httpx.AsyncClient(timeout=graphrag_timeout) as client:
                response = await client.post(
                    effective_graphrag_url,
                    json    = payload,
                    headers = {"Content-Type": "application/json"},
                )

            if response.status_code == 403:
                print(f"[GRAPHRAG] 403 — host not whitelisted")
                support = getattr(incoming, 'support_email', None) or incoming.biz_name
                return (
                    f"Thanks for your interest, {incoming.sender_name}! 😊\n\n"
                    f"I'm having trouble fetching product information right now.\n"
                    f"Please contact *{support}* for assistance."
                )

            if response.status_code != 200:
                print(f"[GRAPHRAG] HTTP {response.status_code}")
                support = getattr(incoming, 'support_email', None) or incoming.biz_name
                return (
                    f"I'm having trouble fetching product information right now, "
                    f"{incoming.sender_name}. 🔧\n\n"
                    f"Please try again shortly or contact *{support}*"
                )

            data = response.json()

        print(f"[GRAPHRAG] Response received — keys: {list(data.keys()) if isinstance(data, dict) else 'list'}")

        # Store raw response on incoming so pipeline can save it to DB
        try:
            incoming._graphrag_raw = json.dumps(data, ensure_ascii=False)
        except Exception:
            incoming._graphrag_raw = str(data)

        response_text = data.get("response_text", [])
        response_text = _coerce_pythonic_dict(response_text)

        # ── Clarification request response ──────────────────────────────────
        # GraphRAG can return a THIRD response shape: a dict with
        # "status": "needs_clarification" and "available_collections" — this
        # happens when a query (e.g. "outdoor lights") matches products
        # spanning multiple distinct collections and GraphRAG wants the
        # customer to narrow down which one they mean.
        #
        # BUG FIXED: previously this dict fell through to str(response_text)
        # and got sent to the customer VERBATIM as raw Python dict syntax
        # (e.g. "{'status': 'needs_clarification', 'message': ...}") —
        # confirmed in production screenshots. Now it's rendered as a
        # clean, friendly numbered list instead.
        if isinstance(response_text, dict) and response_text.get("status") == "needs_clarification":
            collections = response_text.get("available_collections", [])
            clarify_msg = response_text.get(
                "message",
                "Could you let me know which category you're interested in?"
            )
            print(f"[GRAPHRAG] Needs clarification — {len(collections)} collections offered")

            lines = [f"Hi {incoming.sender_name}! {clarify_msg}"]
            if collections:
                lines.append("")
                for i, c in enumerate(collections, 1):
                    lines.append(f"*{i}.* {c}")
                lines.append("")
                lines.append("Just reply with the collection name and I'll show you the options! 💡")
                try:
                    asyncio.create_task(save_category_selection(incoming.tenant_id, incoming.session_id, collections))
                    print(f"[CATEGORY] Dispatched save_category_selection task ({len(collections)} options)")
                except Exception as _ce:
                    print(f"[CATEGORY] save_category_selection dispatch failed: {_ce}")

            return "\n".join(lines)

        # ── Structured product list response ──────────────────────────────
        if isinstance(response_text, list) and response_text and isinstance(response_text[0], dict):
            return await _send_structured_product_list(incoming, response_text)

        # ── Plain text / string response ───────────────────────────────────
        # CRITICAL: response_text can be an empty list [] when GraphRAG finds
        # zero matching products. An empty list is falsy in Python, so the old
        # `if response_text else str(data)` fallback incorrectly stringified
        # the ENTIRE raw API payload (status, tenant_id, message_id, etc.) and
        # sent that directly to the customer as a WhatsApp message. Fixed:
        # explicitly check for the empty-list case and reply with a clean,
        # friendly message instead of ever exposing raw API internals.
        if isinstance(response_text, list) and len(response_text) == 0:
            print(f"[GRAPHRAG] Empty product list — no matches found")
            return (
                f"Sorry {incoming.sender_name}, I couldn't find any products matching that. "
                f"Could you try describing it differently, or browse all products at {incoming.website or incoming.biz_name}? 💡"
            )

        reply_str = str(response_text).strip() if response_text else str(data)
        print(f"[GRAPHRAG] Plain text reply — {len(reply_str)} chars")

        # Detect plain-text bullet list (GraphRAG clarification delivered as text, not dict)
        _plaintext_cats = _parse_plaintext_categories(reply_str)
        if _plaintext_cats:
            try:
                asyncio.create_task(save_category_selection(incoming.tenant_id, incoming.session_id, _plaintext_cats))
                print(f"[CATEGORY] Dispatched save_category_selection task ({len(_plaintext_cats)} options)")
            except Exception as _pce:
                print(f"[CATEGORY] Plain-text category save task dispatch failed: {_pce}")

        # If GraphRAG returned a short error message (≤100 chars), retry once
        # with an even simpler query — just the last 1-2 words as keywords
        if len(reply_str) <= 100 and ("error" in reply_str.lower() or "sorry" in reply_str.lower()):
            print(f"[GRAPHRAG] API error detected — retrying with simplified query")
            words = [w for w in graphrag_text.split() if len(w) > 3]
            simple_query = " ".join(words[-2:]) if words else graphrag_text
            if simple_query and simple_query != graphrag_text:
                print(f"[GRAPHRAG] Retry query: '{simple_query}'")
                try:
                    if is_mcp:
                        retry_res = await query_mcp_catalog(
                            query=simple_query,
                            session_id=incoming.session_id,
                            tenant_id=incoming.tenant_id,
                            server_url=mcp_url,
                            intent_data=_client_intent_data,
                            state=state.__dict__ if state and hasattr(state, "__dict__") else (state if isinstance(state, dict) else None)
                        )
                        if retry_res and retry_res.get("status") == "success":
                            retry_products = retry_res.get("products", [])
                            if retry_products and isinstance(retry_products, list) and isinstance(retry_products[0], dict):
                                print(f"[GRAPHRAG-MCP] Retry succeeded — {len(retry_products)} products")
                                return await _send_structured_product_list(incoming, retry_products)
                    else:
                        payload["text"] = simple_query
                        async with httpx.AsyncClient(timeout=graphrag_timeout) as retry_client:
                            retry_resp = await retry_client.post(
                                GRAPHRAG_API_URL,
                                json    = payload,
                                headers = {"Content-Type": "application/json"},
                            )
                        if retry_resp.status_code == 200:
                            retry_data = retry_resp.json()
                            retry_text = retry_data.get("response_text", [])
                            retry_text = _coerce_pythonic_dict(retry_text)
                            if isinstance(retry_text, list) and retry_text and isinstance(retry_text[0], dict):
                                print(f"[GRAPHRAG] Retry succeeded — {len(retry_text)} products")
                                return await _send_structured_product_list(incoming, retry_text)
                            elif isinstance(retry_text, dict) and retry_text.get("status") == "needs_clarification":
                                collections = retry_text.get("available_collections", [])
                                clarify_msg = retry_text.get(
                                    "message",
                                    "Could you let me know which category you're interested in?"
                                )
                                lines = [f"Hi {incoming.sender_name}! {clarify_msg}"]
                                if collections:
                                    lines.append("")
                                    for i, c in enumerate(collections, 1):
                                        lines.append(f"*{i}.* {c}")
                                    lines.append("")
                                    lines.append("Just reply with the collection name and I'll show you the options! 💡")
                                    try:
                                        asyncio.create_task(save_category_selection(incoming.tenant_id, incoming.session_id, collections))
                                        print(f"[CATEGORY] Dispatched save_category_selection task ({len(collections)} options)")
                                    except Exception as _ce:
                                        print(f"[CATEGORY] save_category_selection dispatch failed: {_ce}")
                                return "\n".join(lines)

                        elif isinstance(retry_text, str) and len(retry_text) > 100:
                            reply_str = retry_text
                except Exception as retry_err:
                    print(f"[GRAPHRAG] Retry failed: {retry_err}")

            # If we still have the original short error/sorry text (retry didn't
            # produce usable products or a longer message), never expose GraphRAG's
            # raw error string to the customer — replace with a friendly message.
            if len(reply_str) <= 100 and ("error" in reply_str.lower() or "sorry" in reply_str.lower()):
                print(f"[GRAPHRAG] Retry did not resolve the error — sending friendly fallback")
                return (
                    f"Sorry {incoming.sender_name}, I'm having trouble finding that right now. "
                    f"Could you try rephrasing, or browse all products at {incoming.website or incoming.biz_name}? 💡"
                )

        if len(reply_str) <= 4096:
            return reply_str

        # Split long plain text reply at line boundaries
        chunks  = []
        lines   = reply_str.split("\n")
        current = ""
        for line in lines:
            candidate = current + "\n" + line if current else line
            if len(candidate) > 3800:
                if current:
                    chunks.append(current.strip())
                if len(line) > 3800:
                    while len(line) > 3800:
                        chunks.append(line[:3800])
                        line = line[3800:]
                    current = line
                else:
                    current = line
            else:
                current = candidate
        if current.strip():
            chunks.append(current.strip())
        if not chunks:
            chunks = [reply_str[i:i+3800] for i in range(0, len(reply_str), 3800)]

        print(f"[GRAPHRAG] Split into {len(chunks)} message(s)")
        return "\n\n⟨MSG_SPLIT⟩\n\n".join(chunks)

    except Exception as e:
        import traceback
        print(f"[GRAPHRAG] Error: {type(e).__name__}: {e}")
        print(f"[GRAPHRAG] Traceback: {traceback.format_exc()[-300:]}")
        support = getattr(incoming, 'support_email', None) or incoming.biz_name
        website = getattr(incoming, 'website', None) or ""
        return (
            f"Thanks for your interest in our products, {incoming.sender_name}! 💡\n\n"
            f"Our product search is temporarily unavailable. Meanwhile:\n\n"
            + (f"• Browse all products at *{website}*\n" if website else "")
            + f"\nNeed help? Contact *{support}*"
        )