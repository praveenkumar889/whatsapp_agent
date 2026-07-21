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
)

def _load_category_prompt(incoming, cat_list: str) -> str:
    """Migration 014: load category_matcher_prompt from DB."""
    from db.prompt_store import get_prompt
    return get_prompt(incoming, "category_matcher_prompt", cat_list=cat_list)


def _reply_prompt(incoming, key: str, fallback: str, **kwargs) -> str:
    """
    Renders a DB-driven customer-facing reply, falling back to `fallback`
    (plain English) only if the tenant hasn't seeded this prompt key yet
    (migration 019). Every hardcoded customer-facing string in this file
    now goes through this helper instead of being a bare Python literal.
    """
    from db.prompt_store import get_prompt
    try:
        return get_prompt(incoming, key, **kwargs)
    except RuntimeError:
        return fallback



_client = AzureOpenAI(
    azure_endpoint = AZURE_AI_ENDPOINT,
    api_key        = AZURE_AI_API_KEY,
    api_version    = AZURE_AI_API_VERSION,
    timeout        = 30.0,
    max_retries    = 0,
)


async def _send_product_card(incoming, position: int, p: dict) -> None:
    """
    Sends one product image card (or text fallback) to the customer.
    Called in parallel via asyncio.gather — each card is independent.
    The save_outbound_message is fire-and-forget (audit log, non-blocking).
    """
    img_url   = p.get("image_url")
    name      = p.get("name", "Product")
    price     = p.get("price_num", 0)
    reg_price = p.get("regular_price", price)
    discount  = p.get("discount_percentage", 0)
    rating    = p.get("rating", 0)
    reviews   = p.get("review_count", 0)

    caption = f"{position}. {name}\nRs.{float(price):,.0f}"
    if discount:
        caption += f" (Save {discount}% off Rs.{float(str(reg_price).replace(',','')):,.0f})"
    if rating:
        caption += f"\n⭐ {rating} ({reviews} reviews)"

    if img_url:
        img_wamid = await send_image(incoming, img_url, caption)
        if img_wamid:
            print(f"[GRAPHRAG] Image sent for product {position}: {name} — wamid={img_wamid}")
            asyncio.create_task(save_outbound_message(
                tenant_id     = incoming.tenant_id,
                session_id    = incoming.session_id,
                message_id    = img_wamid,
                text          = caption,
                media_url     = img_url,
                original_type = "image",
                region        = incoming.region,
            ))
    else:
        reply_wamid = await send_reply(incoming, caption)
        if reply_wamid:
            print(f"[GRAPHRAG] No image for product {position}: {name} — sent text card wamid={reply_wamid}")
            asyncio.create_task(save_outbound_message(
                tenant_id  = incoming.tenant_id,
                session_id = incoming.session_id,
                message_id = reply_wamid,
                text       = caption,
                region     = incoming.region,
            ))


async def _send_structured_product_list(incoming, products: list) -> str:
    """
    Builds and sends the full product-list response: caches products,
    saves the selection for follow-up picking, sends image cards for the
    first N products in parallel, and returns the numbered text summary.

    Extracted so it can be reused both for the initial GraphRAG response
    AND for a successful retry response — fixes a bug where a successful
    retry with real products silently fell through to returning the
    ORIGINAL error text instead of ever rendering the retried products.

    LATENCY OPTIMIZATIONS:
      - All DB cache saves are fire-and-forget (asyncio.create_task) — they're
        for future requests only and don't affect the current response.
      - Product image cards are sent in parallel (asyncio.gather) instead of
        sequentially — saves 1-2s when max_image_products >= 2.
    """
    print(f"[GRAPHRAG] Got {len(products)} products from structured response")

    if len(products) == 1:
        pname = products[0].get("name") or products[0].get("product_name")
        if pname:
            asyncio.create_task(save_last_discussed_product(incoming.tenant_id, incoming.session_id, pname))

    # Build batch cache items (sync — just dict construction, no I/O)
    batch_items = []
    for p in products:
        sku = p.get("sku")
        if sku:
            # Copy all fields from GraphRAG response dynamically
            item_dict = p.copy()

            # Normalize standard keys the application relies on
            if "name" in p:
                item_dict["product_name"] = p.get("name")
            if "price_num" in p:
                item_dict["list_price"]   = float(p.get("price_num", 0))
            if "url" in p:
                item_dict["product_url"]  = p.get("url")
            if "discount_percentage" in p:
                item_dict["discount_pct"] = p.get("discount_percentage", 0)
            if "regular_price" not in item_dict:
                item_dict["regular_price"] = p.get("regular_price", p.get("price_num", 0))

            cached_item = [item_dict]
            batch_items.append({"sku": sku, "api_response": cached_item})

    # Fire-and-forget: all three cache saves are for future requests only.
    # Moving these off the critical path saves ~400-600ms per product-list response.
    if batch_items:
        asyncio.create_task(save_product_api_responses_batch(incoming.tenant_id, batch_items))
    asyncio.create_task(save_graphrag_product_selection(
        tenant_id  = incoming.tenant_id,
        session_id = incoming.session_id,
        products   = products,
    ))
    _go = next((p.get("global_offers") for p in products if p.get("global_offers")), None)
    if _go:
        asyncio.create_task(save_tenant_offers(tenant_id=incoming.tenant_id, offers_text=_go))

    # Send image cards sequentially to guarantee they are delivered in correct numerical order.
    MAX_IMAGE_PRODUCTS = getattr(incoming, "max_image_products", None) or 3
    for i, p in enumerate(products, 1):
        if i <= MAX_IMAGE_PRODUCTS:
            try:
                await _send_product_card(incoming, i, p)
            except Exception as ce:
                print(f"[GRAPHRAG] Error sending product card {i}: {ce}")

    # Load the max message limit from the DB (same key _send_reply_chunked uses)
    # so that clients can tune this per-tenant without any code changes.
    try:
        from db.prompt_store import get_prompt
        _limit_str = get_prompt(incoming, "whatsapp_max_message_limit")
        _wa_limit = min(int(_limit_str.strip()), 4096)
    except Exception:
        _wa_limit = 4096

    header = _reply_prompt(
        incoming, "graphrag_product_list_header_prompt",
        fallback=f"Here are the options for you, {incoming.sender_name}! 💡\n",
        sender_name=incoming.sender_name,
    )
    footer = "\n" + _reply_prompt(
        incoming, "graphrag_product_list_footer_prompt",
        fallback="Reply with the product *number* or *name* to know more or place an order.",
    )

    def _build_lines(prods, strip_suffix, compact_discount):
        """Build product entry lines with optional name/discount compression."""
        result = [header]
        for i, p in enumerate(prods, 1):
            name      = p.get("name", "Product")
            price     = p.get("price_num", 0)
            reg_price = p.get("regular_price", price)
            discount  = p.get("discount_percentage", 0)
            if strip_suffix:
                # Remove long SEO description appended after common separators
                for sep in [" — ", " - ", " | "]:
                    if sep in name:
                        candidate = name.split(sep)[0].strip()
                        if len(candidate) > 3:
                            name = candidate
                            break
            entry = f"*{i}.* {name} — Rs.{float(price):,.0f}"
            if discount:
                if compact_discount:
                    entry += f" ({discount}% off)"
                else:
                    entry += f" (Save {discount}% off Rs.{float(str(reg_price).replace(',','')):,.0f})"
            result.append(entry)
        result.append(footer)
        return "\n".join(result)

    # Strategy 1 — Full names, full discount detail
    summary_text = _build_lines(products, strip_suffix=False, compact_discount=False)
    if len(summary_text) <= _wa_limit:
        return summary_text

    # Strategy 2 — Strip trailing SEO suffixes from names, full discount detail
    summary_text = _build_lines(products, strip_suffix=True, compact_discount=False)
    if len(summary_text) <= _wa_limit:
        return summary_text

    # Strategy 3 — Strip SEO suffixes + compact discount format
    summary_text = _build_lines(products, strip_suffix=True, compact_discount=True)
    if len(summary_text) <= _wa_limit:
        return summary_text

    # Strategy 4 — Strip SEO suffixes + no discount detail
    lines = [header]
    for i, p in enumerate(products, 1):
        name  = p.get("name", "Product")
        price = p.get("price_num", 0)
        for sep in [" — ", " - ", " | "]:
            if sep in name:
                candidate = name.split(sep)[0].strip()
                if len(candidate) > 3:
                    name = candidate
                    break
        lines.append(f"*{i}.* {name} — Rs.{float(price):,.0f}")
    lines.append(footer)
    summary_text = "\n".join(lines)
    if len(summary_text) <= _wa_limit:
        return summary_text

    # Strategy 5 — absolute fallback: drop entries from the bottom until it fits
    while len(summary_text) > _wa_limit and len(lines) > 2:
        lines.pop(-2)
        summary_text = "\n".join(lines)

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

    categories = []
    for line in text.split("\n"):
        line = line.strip()
        m = re.match(r'^[•\-\*·]\s+(.+)$', line)
        if not m:
            m = re.match(r'^\*\*\d+\.\*\*\s+(.+)$', line)
        if not m:
            m = re.match(r'^\d+[.)]\s+(.+)$', line)
        if m:
            cat = m.group(1).strip()
            cat = re.split(r'\s+[—\-]\s+', cat)[0].strip()
            cat = cat.strip('*').strip()
            if cat:
                categories.append(cat)

    return categories if len(categories) >= 2 else None


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
            print(f"[CATEGORY] Index match: {idx+1} → '{categories[idx]}'")
            return categories[idx]

    # 2. String match
    text_lower = text_stripped.lower()
    for cat in categories:
        if cat.lower() == text_lower:
            print(f"[CATEGORY] Exact match: '{cat}'")
            return cat
    for cat in categories:
        if cat.lower() in text_lower or text_lower in cat.lower():
            print(f"[CATEGORY] Substring match: '{cat}'")
            return cat
    text_words = {w for w in text_lower.split() if len(w) > 3}
    best_cat, best_overlap = None, 0
    for cat in categories:
        cat_words = {w for w in cat.lower().split() if len(w) > 3}
        overlap = len(text_words & cat_words)
        if overlap > best_overlap:
            best_overlap, best_cat = overlap, cat
    if best_overlap >= 2 or (best_overlap >= 1 and len(text_words) <= 2):
        print(f"[CATEGORY] Word-overlap match ({best_overlap} words): '{best_cat}'")
        return best_cat

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
                    print(f"[CATEGORY] LLM match: '{cat}'")
                    return cat
    except Exception as e:
        print(f"[CATEGORY] LLM mapping failed: {e}")

    return None




async def _build_enriched_graphrag_query(
    incoming, customer_message: str, memory_context: str, current_product: Optional[str] = None,
) -> Optional[str]:
    """
    Builds an enriched GraphRAG query via graphrag_query_builder_prompt
    (DB-driven, tenant-configurable) — not Python string concatenation, so
    the query construction itself stays tenant-configurable like every other
    customer-facing/LLM-facing text in this codebase.

    Combines short-term context (current_product, from this session's own
    state) with long-term context (memory_context, from Mem0) — a customer
    actively discussing one product asking "recommend something for me"
    should get results related to what they're looking at right now, not
    only what they bought previously.
    """
    try:
        from db.prompt_store import get_prompt
        prompt = get_prompt(
            incoming, "graphrag_query_builder_prompt",
            customer_message=customer_message, memory_context=memory_context,
            current_product=current_product or "None",
        )
        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=150, temperature=0,
                messages=[{"role": "system", "content": prompt}],
            )
        )
        content = r.choices[0].message.content
        return content.strip() if content else None
    except RuntimeError:
        raise
    except Exception as e:
        print(f"[MEM0] _build_enriched_graphrag_query failed (non-critical): {e}")
        return None


async def call_graphrag_api(incoming, session_history: Optional[list] = None, graphrag_url: Optional[str] = None) -> str:
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
        follow_up_reply = await _try_resolve_product_followup(incoming, session_history or [])
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
        graphrag_text = getattr(incoming, "resolved_query", None) or incoming.text

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
        _cat_options = await get_category_selection(incoming.tenant_id, incoming.session_id)
        if _cat_options:
            _matched_cat = await _resolve_category_from_message(incoming, incoming.text, _cat_options)
            if _matched_cat:
                print(f"[CATEGORY] Resolved '{_matched_cat}' — rewriting query")
                await clear_category_selection(incoming.tenant_id, incoming.session_id)
                graphrag_text = f"Show products in {_matched_cat}"
            else:
                print(f"[CATEGORY] No match found — clearing state, proceeding with original query")
                await clear_category_selection(incoming.tenant_id, incoming.session_id)

        # ── Memory-aware query enrichment ───────────────────────────────────
        # ── RequestContext Context Consumption ──────────────────────────────
        # GraphRAG consumes structured context assembled upstream by ContextBuilder
        # rather than querying customer databases itself.
        try:
            arc = getattr(incoming, "_cached_arc", None)
            _current_product = None
            if arc and arc.llm_context:
                msg_lower = incoming.text.lower()
                has_past_ref = any(k in msg_lower for k in ("previous order", "last order", "previously ordered", "previous purchase", "last purchase", "what i bought", "bought last", "past order", "previous product", "last product", "what i ordered"))
                
                if has_past_ref:
                    try:
                        from db.customer_data_service import CustomerDataService
                        cds = CustomerDataService(incoming.tenant_id, incoming.session_id)
                        _current_product = await cds.get_latest_ordered_product()
                        if _current_product:
                            print(f"[CONTEXT] Resolved previous order product: {_current_product}")
                    except Exception as e:
                        print(f"[CONTEXT] Failed to resolve previous order product: {e}")
                
                if not _current_product:
                    routing = getattr(incoming, "_routing", None)
                    needs_prod = getattr(routing, "needs_product_context", False) if routing else False
                    req_field = getattr(routing, "requested_knowledge_field", "none") or "none"
                    is_details_query = needs_prod or (req_field.lower() != "none")
                    
                    if is_details_query:
                        from db.session_store import get_last_discussed_product
                        _current_product = await get_last_discussed_product(incoming.tenant_id, incoming.session_id)
                        if not _current_product:
                            _current_product = arc.resolved_product
                    elif not getattr(incoming, "_is_new_category_search", False):
                        # Skip re-attaching the previously active product when
                        # _try_resolve_product_followup already classified this
                        # message as a new/broader search (is_new_search=True) —
                        # otherwise the query GraphRAG receives gets silently
                        # rewritten back to the stale product name.
                        if getattr(arc.llm_context, "active_product_session", False):
                            _current_product = arc.resolved_product
            history_context = arc.customer_context if arc else ""

            if history_context or _current_product:
                print(f"[CONTEXT] Consuming resolved_product={_current_product} for this GraphRAG query")
                enriched = await _build_enriched_graphrag_query(
                    incoming, graphrag_text, history_context, current_product=_current_product,
                )
                if enriched:
                    print(f"[CONTEXT] Enriched GraphRAG query with retrieved context")
                    graphrag_text = enriched
        except Exception as e:
            print(f"[CONTEXT] Query enrichment failed (non-critical, using original query): {e}")

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
        }

        # Resolve effective GraphRAG URL:
        # 1. Per-tenant URL passed from main.py (from tenants table column) — highest priority
        # 2. Global GRAPHRAG_API_URL env var — fallback
        effective_graphrag_url = graphrag_url or GRAPHRAG_API_URL
        if not effective_graphrag_url:
            print(f"[GRAPHRAG] No GraphRAG URL configured for tenant {incoming.tenant_id}")
            support = getattr(incoming, 'support_email', None) or incoming.biz_name
            return _reply_prompt(
                incoming, "graphrag_no_url_configured_prompt",
                fallback=f"I'm not able to look up products right now, {incoming.sender_name}. "
                         f"Please contact *{support}* for assistance.",
                sender_name=incoming.sender_name, support=support,
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
            return _reply_prompt(
                incoming, "graphrag_403_error_prompt",
                fallback=f"Thanks for your interest, {incoming.sender_name}! 😊\n\n"
                         f"I'm having trouble fetching product information right now.\n"
                         f"Please contact *{support}* for assistance.",
                sender_name=incoming.sender_name, support=support,
            )

        if response.status_code != 200:
            print(f"[GRAPHRAG] HTTP {response.status_code}")
            support = getattr(incoming, 'support_email', None) or incoming.biz_name
            return _reply_prompt(
                incoming, "graphrag_http_error_prompt",
                fallback=f"I'm having trouble fetching product information right now, "
                         f"{incoming.sender_name}. 🔧\n\n"
                         f"Please try again shortly or contact *{support}*",
                sender_name=incoming.sender_name, support=support,
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
                _reply_prompt(incoming, "graphrag_category_clarify_default",
                               fallback="Could you let me know which category you're interested in?")
            )
            print(f"[GRAPHRAG] Needs clarification — {len(collections)} collections offered")

            lines = [_reply_prompt(
                incoming, "graphrag_category_clarify_greeting_prompt",
                fallback=f"Hi {incoming.sender_name}! {clarify_msg}",
                sender_name=incoming.sender_name, clarify_msg=clarify_msg,
            )]
            if collections:
                lines.append("")
                for i, c in enumerate(collections, 1):
                    lines.append(f"*{i}.* {c}")
                lines.append("")
                lines.append(_reply_prompt(
                    incoming, "graphrag_category_clarify_footer_prompt",
                    fallback="Just reply with the collection name and I'll show you the options! 💡",
                ))
                try:
                    await save_category_selection(incoming.tenant_id, incoming.session_id, collections)
                    print(f"[CATEGORY] Saved {len(collections)} category options for follow-up")
                except Exception as _ce:
                    print(f"[CATEGORY] save_category_selection failed (non-critical): {_ce}")

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
            return _reply_prompt(
                incoming, "graphrag_empty_results_prompt",
                fallback=f"Sorry {incoming.sender_name}, I couldn't find any products matching that. "
                         f"Could you try describing it differently, or browse all products at {incoming.website or incoming.biz_name}? 💡",
                sender_name=incoming.sender_name, website=(incoming.website or incoming.biz_name),
            )

        reply_str = str(response_text).strip() if response_text else str(data)
        print(f"[GRAPHRAG] Plain text reply — {len(reply_str)} chars")

        # Detect plain-text bullet list (GraphRAG clarification delivered as text, not dict)
        _plaintext_cats = _parse_plaintext_categories(reply_str)
        if _plaintext_cats:
            try:
                await save_category_selection(incoming.tenant_id, incoming.session_id, _plaintext_cats)
                print(f"[CATEGORY] Parsed {len(_plaintext_cats)} categories from plain-text response — saved")
            except Exception as _pce:
                print(f"[CATEGORY] Plain-text category save failed (non-critical): {_pce}")

        # If GraphRAG returned a short error message (≤100 chars), retry once
        # with an even simpler query — just the last 1-2 words as keywords
        if len(reply_str) <= 100 and ("error" in reply_str.lower() or "sorry" in reply_str.lower()):
            print(f"[GRAPHRAG] API error detected — retrying with simplified query")
            words = [w for w in graphrag_text.split() if len(w) > 3]
            simple_query = " ".join(words[-2:]) if words else graphrag_text
            if simple_query and simple_query != graphrag_text:
                print(f"[GRAPHRAG] Retry query: '{simple_query}'")
                payload["text"] = simple_query
                try:
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
                            # BUG FIX: previously this only set response_text with a comment
                            # "fall through to handling below" — but no such handling existed
                            # after this point, so the retry's real products were silently
                            # discarded and the ORIGINAL error text was returned instead.
                            return await _send_structured_product_list(incoming, retry_text)
                        elif isinstance(retry_text, dict) and retry_text.get("status") == "needs_clarification":
                            collections = retry_text.get("available_collections", [])
                            clarify_msg = retry_text.get(
                                "message",
                                _reply_prompt(incoming, "graphrag_category_clarify_default",
                                               fallback="Could you let me know which category you're interested in?")
                            )
                            lines = [_reply_prompt(
                                incoming, "graphrag_category_clarify_greeting_prompt",
                                fallback=f"Hi {incoming.sender_name}! {clarify_msg}",
                                sender_name=incoming.sender_name, clarify_msg=clarify_msg,
                            )]
                            if collections:
                                lines.append("")
                                for i, c in enumerate(collections, 1):
                                    lines.append(f"*{i}.* {c}")
                                lines.append("")
                                lines.append(_reply_prompt(
                                    incoming, "graphrag_category_clarify_footer_prompt",
                                    fallback="Just reply with the collection name and I'll show you the options! 💡",
                                ))
                                try:
                                    await save_category_selection(incoming.tenant_id, incoming.session_id, collections)
                                    print(f"[CATEGORY] Saved {len(collections)} category options (retry path)")
                                except Exception as _ce:
                                    print(f"[CATEGORY] save_category_selection failed (non-critical): {_ce}")
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
                return _reply_prompt(
                    incoming, "graphrag_retry_failed_prompt",
                    fallback=f"Sorry {incoming.sender_name}, I'm having trouble finding that right now. "
                             f"Could you try rephrasing, or browse all products at {incoming.website or incoming.biz_name}? 💡",
                    sender_name=incoming.sender_name, website=(incoming.website or incoming.biz_name),
                )

        # Load character limit dynamically from DB with fallback
        try:
            from db.prompt_store import get_prompt
            limit_str = get_prompt(incoming, "whatsapp_max_message_limit")
            max_len = int(limit_str.strip())
        except Exception:
            max_len = 5000

        if len(reply_str) <= max_len:
            return reply_str

        # Split long plain text reply at line boundaries
        chunks  = []
        lines   = reply_str.split("\n")
        current = ""
        safety_limit = max_len - 200
        for line in lines:
            candidate = current + "\n" + line if current else line
            if len(candidate) > safety_limit:
                if current:
                    chunks.append(current.strip())
                if len(line) > safety_limit:
                    while len(line) > safety_limit:
                        chunks.append(line[:safety_limit])
                        line = line[safety_limit:]
                    current = line
                else:
                    current = line
            else:
                current = candidate
        if current.strip():
            chunks.append(current.strip())
        if not chunks:
            chunks = [reply_str[i:i+safety_limit] for i in range(0, len(reply_str), safety_limit)]

        print(f"[GRAPHRAG] Split into {len(chunks)} message(s)")
        return "\n\n⟨MSG_SPLIT⟩\n\n".join(chunks)

    except Exception as e:
        import traceback
        print(f"[GRAPHRAG] Error: {type(e).__name__}: {e}")
        print(f"[GRAPHRAG] Traceback: {traceback.format_exc()[-300:]}")
        support = getattr(incoming, 'support_email', None) or incoming.biz_name
        website = getattr(incoming, 'website', None) or ""
        _fallback_lines = (
            f"Thanks for your interest in our products, {incoming.sender_name}! 💡\n\n"
            f"Our product search is temporarily unavailable. Meanwhile:\n\n"
            + (f"• Browse all products at *{website}*\n" if website else "")
            + f"\nNeed help? Contact *{support}*"
        )
        return _reply_prompt(
            incoming, "graphrag_exception_fallback_prompt",
            fallback=_fallback_lines,
            sender_name=incoming.sender_name, support=support, bullet_website=(f"• Browse all products at *{website}*\n" if website else ""),
        )