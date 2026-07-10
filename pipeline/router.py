# pipeline/router.py — Intent Dispatch + Pre-Route Guards
#
# FIX APPLIED: _invoice_guard() now REQUIRES awaiting_invoice_confirmation=True
# before treating ANY message as a confirmation. Previously it checked
# _is_invoice_confirmation_request() unconditionally on every message,
# which could falsely trigger invoice generation on casual messages like
# "add 2 more units" if the LLM loosely matched confirmation patterns.
#
# All ai.* imports are kept INSIDE functions to prevent circular imports.

import asyncio
from typing import Optional


async def dispatch(incoming, result, session_history: list) -> str:
    """
    Main routing function — returns reply string.
    All imports are local to avoid circular import chain.
    """
    # Performance: load negotiation state ONCE and cache on incoming object.
    if not hasattr(incoming, '_cached_neg_state'):
        from db.session_store import get_negotiation_state as _gns
        incoming._cached_neg_state = await _gns(incoming.tenant_id, incoming.session_id)

    # Centralized RequestContext ContextBuilder assembly
    arc = getattr(incoming, "_cached_arc", None)
    if arc:
        from ai.context_builder import ContextBuilder
        await ContextBuilder(arc).build()

    # Dynamic Knowledge Resolution Intercept Guard
    _routing = getattr(incoming, "_routing", None)
    requested_field = _routing.requested_knowledge_field if _routing else None
    domain = (_routing.knowledge_domain or "product") if _routing else "product"

    if arc and requested_field and requested_field.upper() != "NONE":
        # Check messages table for the last graphrag_response in this session
        from db.session_store import get_latest_graphrag_response
        import json
        raw_graph_resp = await get_latest_graphrag_response(incoming.tenant_id, incoming.session_id)
        
        products = []
        if raw_graph_resp:
            try:
                data = json.loads(raw_graph_resp)
                if isinstance(data, dict):
                    response_text = data.get("response_text", [])
                    if isinstance(response_text, list):
                        products = response_text
                elif isinstance(data, list):
                    products = data
            except Exception as e:
                print(f"[ROUTER] Failed to parse graphrag_response JSON: {e}")

        # Resolve matched product
        matched_product = None
        if arc.resolved_product and products:
            pname_lower = arc.resolved_product.lower().strip()
            for p in products:
                if not isinstance(p, dict):
                    continue
                p_pname = (p.get("name") or p.get("product_name") or "").lower().strip()
                if p_pname == pname_lower or pname_lower in p_pname or p_pname in pname_lower:
                    matched_product = p
                    break

        # Fallback to DB product_cache lookup if not found in latest graphrag_response
        if not matched_product and arc.resolved_product:
            from db.session_store import get_cached_product_by_name
            cached_product = await get_cached_product_by_name(incoming.tenant_id, arc.resolved_product)
            if cached_product:
                matched_product = cached_product

        val = None
        if matched_product:
            if requested_field.lower() == "installation":
                val = matched_product.get("installation_url") or matched_product.get("installation") or matched_product.get("pdf_url")
                if isinstance(val, dict):
                    val = val.get("pdf_url") or val.get("manual_url") or val.get("video_url")
                if not val:
                    # check nested installation column string in product_cache
                    installation = matched_product.get("installation")
                    if isinstance(installation, str):
                        try:
                            installation = json.loads(installation)
                        except Exception:
                            installation = None
                    if isinstance(installation, dict):
                        val = installation.get("pdf_url") or installation.get("manual_url") or installation.get("video_url")
            else:
                val = matched_product.get(requested_field) or matched_product.get(f"{requested_field}_url")

        if val:
            from db.prompt_store import get_prompt
            if requested_field.lower() == "installation":
                try:
                    header = get_prompt(incoming, "followup_installation_header_prompt", product_name=arc.resolved_product)
                    return f"{header}\n🔗 {val}"
                except Exception:
                    return f"Here is the installation guide for *{arc.resolved_product}*:\n\n🔗 {val}"
            elif isinstance(val, str) and (val.startswith("http://") or val.startswith("https://")):
                prompt_key = f"asset_success_{requested_field}_prompt"
                try:
                    return get_prompt(incoming, prompt_key, product_name=arc.resolved_product, value=val)
                except Exception:
                    label = requested_field.capitalize().replace("_", " ")
                    return f"Here is the {label} for *{arc.resolved_product}*:\n\n🔗 {val}"
            else:
                prompt_key = f"asset_success_{requested_field}_prompt"
                try:
                    return get_prompt(incoming, prompt_key, product_name=arc.resolved_product, value=str(val))
                except Exception:
                    label = requested_field.capitalize().replace("_", " ")
                    return f"Here is the {label} for *{arc.resolved_product}*:\n{val}"

        # Check cache/refresh state
        kstate = getattr(arc.llm_context, "knowledge_state", {})
        is_available = kstate.get("available", False)
        needs_refresh = kstate.get("needs_refresh", False)
        
        if not is_available or needs_refresh:
            print(f"[ROUTER] Cache miss or expired for '{arc.resolved_product}' (domain: {domain}, field: {requested_field}) — calling GraphRAG")
            from ai.graphrag_handler import call_graphrag_api
            # Temporarily rewrite query to request the details
            original_text = incoming.text
            incoming.text = f"Product: {arc.resolved_product}. Query: {original_text}"
            
            # This calls GraphRAG, fetches from Neo4j, formats/sends, and updates product_cache
            res = await call_graphrag_api(incoming, session_history)
            
            # Restore original text
            incoming.text = original_text
            
            if res is not None:
                return res
            
            # Rebuild context to reload refreshed details
            from ai.context_builder import ContextBuilder
            await ContextBuilder(arc).build()
            
        # Access structured knowledge
        kstate = getattr(arc.llm_context, "knowledge_state", {})
        if kstate.get("available"):
            from ai.knowledge_accessor import KnowledgeAccessor
            asset = await KnowledgeAccessor.get(incoming, arc.llm_context.knowledge_context, domain, requested_field)
            if asset:
                asset_type = asset.get("type")
                asset_val = asset.get("value")
                
                # Deliver typed assets: Image
                if asset_type == "image":
                    from messaging import send_image
                    img_url = asset_val[0] if isinstance(asset_val, list) and asset_val else asset_val
                    if img_url:
                        caption = f"Here is the image for {arc.resolved_product or 'the product'}"
                        wamid = await send_image(incoming, img_url, caption)
                        if wamid:
                            from db.session_store import save_outbound_message
                            await save_outbound_message(
                                tenant_id=incoming.tenant_id,
                                session_id=incoming.session_id,
                                message_id=wamid,
                                text=caption,
                                media_url=img_url,
                                original_type="image",
                                region=incoming.region,
                            )
                            return ""
                
                # Deliver typed assets: Document / PDF URL
                elif asset_type == "document":
                    doc_url = asset_val
                    if doc_url:
                        from db.prompt_store import get_prompt
                        prompt_key = f"asset_success_{requested_field}_prompt"
                        try:
                            return get_prompt(incoming, prompt_key, product_name=arc.resolved_product, value=doc_url)
                        except RuntimeError:
                            if requested_field.lower() == "installation":
                                try:
                                    header = get_prompt(incoming, "followup_installation_header_prompt", product_name=arc.resolved_product)
                                    return f"{header}\n🔗 {doc_url}"
                                except Exception:
                                    return f"Here is the installation guide for *{arc.resolved_product}*:\n\n🔗 {doc_url}"
                            label = requested_field.capitalize().replace("_", " ")
                            return f"You can view the {label} for *{arc.resolved_product}* here:\n{doc_url}"

                # Deliver typed assets: Text / Specifications / etc
                from db.prompt_store import get_prompt
                prompt_key = f"asset_success_{requested_field}_prompt"
                try:
                    return get_prompt(incoming, prompt_key, product_name=arc.resolved_product, value=asset_val)
                except RuntimeError:
                    label = requested_field.capitalize().replace("_", " ")
                    return f"Here is the {label} for *{arc.resolved_product}*:\n{asset_val}"
            else:
                # Deliver missing-asset fallback
                from db.prompt_store import get_prompt
                prompt_key = f"asset_missing_{requested_field}_prompt"
                try:
                    return get_prompt(incoming, prompt_key, product_name=arc.resolved_product)
                except RuntimeError:
                    label = requested_field.capitalize().replace("_", " ")
                    return f"This product currently doesn't have {label} details available."


    # Guard 1: Negotiation awaiting confirmation
    reply = await _neg_guard(incoming, result, session_history)
    if reply:
        return reply

    # Guard 2: Invoice fast-path (only fires when bot is ACTUALLY awaiting confirmation)
    reply = await _invoice_guard(incoming, session_history)
    if reply:
        return reply

    # Main intent routing
    intent     = result.intent
    confidence = result.confidence_score

    if intent == "GREETING":
        from ai.handlers import handle_greeting
        return await handle_greeting(incoming)

    if intent == "HUMAN_ESCALATION":
        from ai.handlers import handle_escalation
        return await handle_escalation(incoming)

    from ai.handlers import DEFAULT_INTENT_MIN_CONFIDENCE
    _min_conf = getattr(incoming, "intent_min_confidence", None) or DEFAULT_INTENT_MIN_CONFIDENCE

    # Routing gate: a message can only MODIFY an existing workflow if one
    # exists. GraphRAG is a product-catalog search engine — it has no way
    # to answer "add 3 more units" correctly (it doesn't know which
    # product/order), so it shouldn't be asked. This check is semantic
    # (routing.operation, computed by intent_system_prompt — migration 031),
    # not a keyword match, so it works the same for "add 3 more units",
    # "add one more MRI scan", "add two pizzas", or "add spouse" regardless
    # of tenant/domain.
    _routing = getattr(incoming, "_routing", None)
    if _routing and _routing.operation == "MODIFY_WORKFLOW" and not _routing.needs_graphrag and not getattr(_routing, "needs_customer_history", False):
        _has_state = incoming._cached_neg_state
        if not _has_state:
            from db.session_store import get_last_discussed_product
            _has_state = await get_last_discussed_product(incoming.tenant_id, incoming.session_id)
        if not _has_state:
            from db.prompt_store import get_prompt
            try:
                return get_prompt(incoming, "no_active_workflow_prompt", sender_name=incoming.sender_name)
            except RuntimeError:
                return (f"Hi {incoming.sender_name}! You don't have an order in progress right now — "
                        f"what would you like to order? 😊")
        # Active state exists — fall through to normal WORKFLOW_ACTION routing
        # below, which resolves this correctly via _try_resolve_product_followup().

    # ── Intercept history-related queries (orders, offers context) ────────────
    _needs_customer_history = getattr(_routing, "needs_customer_history", False) if _routing else False
    _req_field = getattr(_routing, "requested_knowledge_field", "none") or "none"
    if _needs_customer_history and _req_field.lower() == "none":
        try:
            from ai.customer_history_handler import handle_customer_history_query
            customer_history_reply = await handle_customer_history_query(incoming, session_history)
            if customer_history_reply:
                return customer_history_reply
        except Exception as e:
            print(f"[ROUTER] customer_history_handler query failed: {e}")

    if intent in ("FAQ_KNOWLEDGE", "WORKFLOW_ACTION") or confidence < _min_conf:
        from ai.graphrag_handler import call_graphrag_api
        return await call_graphrag_api(incoming, session_history)

    from ai.handlers import handle_unknown
    return await handle_unknown(incoming)


def _load_fast_confirm_prompt(incoming) -> str:
    """Migration 014: load fast_order_confirm_check_prompt from DB."""
    from db.prompt_store import get_prompt
    return get_prompt(incoming, "fast_order_confirm_check_prompt")


async def _neg_guard(incoming, result, session_history: list) -> Optional[str]:
    """
    Guards the negotiation state machine.

    THREE PHASES:
      1. NEGOTIATING (rounds > 0, counter_offer_presented=False, awaiting_invoice_confirmation=False)
         → Normal negotiation re-entry via graphrag/product_followup

      2. COUNTER_OFFER_PRESENTED (counter_offer_presented=True, awaiting_invoice_confirmation=False)
         → Bot presented its best/final price. Customer may still be bargaining.
         → If customer says another price: reply "our final offer remains Rs.X"
         → If customer accepts: transition to AWAITING_CONFIRMATION

      3. AWAITING_CONFIRMATION (awaiting_invoice_confirmation=True)
         → Customer has accepted. Next non-qty message triggers summary+invoice.
         → This guard intercepts and handles qty changes or re-negotiation attempts.
    """
    from db.session_store import get_negotiation_state, save_negotiation_state, get_tenant_offers
    from ai.invoice_handler import _is_invoice_confirmation_request

    pre_neg_state = getattr(incoming, '_cached_neg_state', None) or \
                    await get_negotiation_state(incoming.tenant_id, incoming.session_id)
    if pre_neg_state is None:
        return None

    # Capability-driven bypass: only intercept intents that require active workflow state
    # All other intents (FAQ_KNOWLEDGE, GREETING, escalation) bypass negotiation guard and fall through
    _routing = getattr(result, "routing", None) or getattr(incoming, "_routing", None)
    if _routing:
        if not _routing.needs_workflow_state:
            return None
    elif result.intent in ("HUMAN_ESCALATION", "FAQ_KNOWLEDGE", "GREETING"):
        # Safe fallback if no routing decision metadata is attached
        return None

    # ── Phase 2: counter_offer_presented — bot has shown its best price ────────
    # Customer hasn't accepted yet. They may be making another offer (still
    # negotiating) or accepting. Handle here before falling through to normal flow.
    counter_offer_presented = pre_neg_state.get("counter_offer_presented", False)
    if counter_offer_presented and not pre_neg_state.get("awaiting_invoice_confirmation", False):
        from ai.negotiator import detect_acceptance
        accepted = await detect_acceptance(incoming.text, incoming, session_history)
        if accepted:
            # Customer accepted → show the full pre-confirm order summary using
            # PricingResult (single source of truth), then wait for "Confirm".
            from ai.pricing import PricingResult
            pr = PricingResult.from_neg_state(pre_neg_state, getattr(incoming, "gst_rate", 0.18))
            product = pre_neg_state.get("product_name", "your product")

            updated = {**pre_neg_state, "awaiting_invoice_confirmation": True,
                       "counter_offer_presented": False, "last_offer_price": pr.negotiated_unit_price}
            await save_negotiation_state(incoming.tenant_id, incoming.session_id, updated)
            return pr.to_whatsapp_summary(product, incoming.sender_name, incoming=incoming)
        else:
            # Customer still negotiating — politely hold our final price
            final_price = float(pre_neg_state.get("last_offer_price", 0))
            quantity    = int(pre_neg_state.get("quantity", 0))
            total       = round(final_price * quantity, 2)
            product     = pre_neg_state.get("product_name", "this product")
            print(f"[NEG GUARD] Customer still bargaining after final offer — holding price Rs.{final_price:,.0f}")
            from db.prompt_store import get_prompt
            try:
                return get_prompt(
                    incoming, "neg_still_bargaining_prompt",
                    sender_name=incoming.sender_name, final_price=f"{final_price:,.0f}",
                    product=product, quantity=quantity, total=f"{total:,.2f}",
                )
            except RuntimeError:
                return (
                    f"I understand, {incoming.sender_name}. 🙏 Rs.*{final_price:,.0f}/unit* is already "
                    f"our absolute best price for *{product}* — we can't reduce it further.\n\n"
                    f"For *{quantity} units*, your total would be *Rs.{total:,.2f}* + GST.\n\n"
                    f"Would you like to proceed at this price? Reply *Confirm* to place your order!"
                )

    # ── Phase 3: awaiting_invoice_confirmation — customer has accepted ──────────
    awaiting_conf = (
        pre_neg_state.get("awaiting_invoice_confirmation", False)
        and pre_neg_state.get("quantity")
        and pre_neg_state.get("last_offer_price")
    )
    if not awaiting_conf:
        return None

    # FIX: narrow type for checker
    neg_state: dict = pre_neg_state  # type: ignore[assignment]

    reply = await _handle_qty_confirm_split(incoming, neg_state, session_history)
    if reply:
        return reply

    is_actual_confirm = await _is_invoice_confirmation_request(incoming, session_history)
    if is_actual_confirm:
        return None

    print(f"[NEG GUARD] Message while awaiting confirmation — re-entering negotiation")
    return await _resume_negotiation(incoming, neg_state, session_history)


async def _handle_qty_confirm_split(incoming, neg_state: dict, session_history: list) -> Optional[str]:
    from ai.negotiator import handle_negotiation, detect_quantity_change
    from db.session_store import get_tenant_offers, save_negotiation_state, get_cached_product_by_name
    try:
        cur_qty = int(neg_state.get("quantity") or 0)
        if cur_qty <= 0:
            return None
        new_qty = await detect_quantity_change(
            incoming.text, cur_qty, neg_state.get("product_name", ""), incoming
        )
        if not new_qty or new_qty == cur_qty:
            return None

        print(f"[QTY+CONFIRM] qty change {cur_qty}→{new_qty}")
        split_state   = {**neg_state, "awaiting_invoice_confirmation": False, "quantity": cur_qty}
        global_offers = neg_state.get("global_offers")
        if not global_offers:
            to = await get_tenant_offers(incoming.tenant_id)
            global_offers = to.get("offers_text") if to else None

        # Fetch cached product data for the summary + recommendation block
        product_data = None
        try:
            product_data = await get_cached_product_by_name(
                incoming.tenant_id, neg_state.get("product_name", "")
            )
        except Exception as e:
            print(f"[QTY+CONFIRM] Product data fetch failed (non-critical): {e}")

        split_result = await handle_negotiation(
            incoming              = incoming,
            product_name          = neg_state.get("product_name", ""),
            price_num             = float(neg_state.get("price_num", 0)),
            regular_price         = float(neg_state.get("regular_price") or neg_state.get("price_num", 0)),
            graphrag_discount_pct = int(neg_state.get("graphrag_discount_pct") or 0),
            session_history       = session_history,
            negotiation_state     = split_state,
            global_offers         = global_offers,
            product_data          = product_data,
        )
        updated_state = {
            **split_result["state"],
            "awaiting_invoice_confirmation": True,
            "quantity": split_result.get("quantity", new_qty),
        }
        await save_negotiation_state(incoming.tenant_id, incoming.session_id, updated_state)

        # Bug 2 fix: compound intent — "add 1 unit AND confirm this order"
        # The customer's message contained BOTH a quantity change AND a
        # confirmation intent. After updating the quantity, we now check if the
        # message also has confirmation language, and if so, skip the "Reply
        # Confirm" screen and go straight to order confirmation + invoice.
        # Use the same LLM-based detect_acceptance that the negotiation engine uses —
        # driven by neg_detect_accept_prompt in DB. No keyword lists, no hardcoding.
        # The prompt handles all natural language: "ok", "deal", "sounds good", 👍, etc.
        from ai.negotiator import detect_acceptance as _detect_accept
        has_confirm_intent = await _detect_accept(incoming.text, incoming, session_history)
        if has_confirm_intent:
            print(f"[QTY+CONFIRM] Compound intent detected — processing qty update + confirmation together")
            try:
                return await _confirm_negotiated_order(incoming, updated_state)
            except Exception as e:
                print(f"[QTY+CONFIRM] Compound confirm failed, falling back to summary: {e}")

        return split_result.get("reply", "")
    except Exception as e:
        print(f"[QTY+CONFIRM] Split check failed: {e}")
        return None


async def _resume_negotiation(incoming, neg_state: dict, session_history: list) -> Optional[str]:
    from ai.negotiator import handle_negotiation
    from ai.graphrag_handler import call_graphrag_api
    from db.session_store import get_tenant_offers, save_negotiation_state

    product   = neg_state.get("product_name", "")
    price_num = float(neg_state.get("price_num", 0))
    reg_price = float(neg_state.get("regular_price") or price_num)
    disc_pct  = int(neg_state.get("graphrag_discount_pct") or 0)

    if not product or price_num <= 0:
        return await call_graphrag_api(incoming, session_history)

    resumed       = {**neg_state, "awaiting_invoice_confirmation": False}
    global_offers = neg_state.get("global_offers")
    if not global_offers:
        to = await get_tenant_offers(incoming.tenant_id)
        global_offers = to.get("offers_text") if to else None

    ng_result = await handle_negotiation(
        incoming              = incoming,
        product_name          = product,
        price_num             = price_num,
        regular_price         = reg_price,
        graphrag_discount_pct = disc_pct,
        session_history       = session_history,
        negotiation_state     = resumed,
        global_offers         = global_offers,
    )

    # DEFERRAL: let the main routing dispatch the deferred intent
    if ng_result.get("defer_intent") is not None:
        await save_negotiation_state(incoming.tenant_id, incoming.session_id, ng_result["state"])
        _defer_intent = ng_result["defer_intent"].intent
        incoming._deferred_intent = _defer_intent  # so main.py's debug output reflects the real routing decision
        print(f"[NEG RESUME] Negotiator deferred (intent={_defer_intent}) — falling through to main routing")
        return None

    if ng_result["order_ready"] and ng_result["agreed_price"]:
        negotiator_reply = ng_result.get("reply", "")
        a   = ng_result["agreed_price"]
        q   = ng_result["quantity"]
        # If negotiator has a counter-offer reply, deliver it and enter Phase 2.
        # Do NOT set awaiting_invoice_confirmation=True — customer hasn't accepted.
        if negotiator_reply and negotiator_reply.strip():
            if ng_result["state"].get("awaiting_invoice_confirmation"):
                await save_negotiation_state(
                    incoming.tenant_id, incoming.session_id,
                    {**ng_result["state"], "last_offer_price": a, "quantity": q}
                )
                return negotiator_reply

            await save_negotiation_state(
                incoming.tenant_id, incoming.session_id,
                {**ng_result["state"], "counter_offer_presented": True,
                 "awaiting_invoice_confirmation": False,
                 "last_offer_price": a, "quantity": q}
            )
            return negotiator_reply
        sub = round(a * q, 2)
        gst = round(sub * incoming.gst_rate, 2)
        tot = round(sub * (1 + incoming.gst_rate), 2)
        await save_negotiation_state(
            incoming.tenant_id, incoming.session_id,
            {**ng_result["state"], "awaiting_invoice_confirmation": True,
             "counter_offer_presented": False,
             "last_offer_price": a, "quantity": q}
        )
        return _build_order_summary(incoming, product, a, q, sub, gst, tot, ng_result["state"])
    else:
        await save_negotiation_state(incoming.tenant_id, incoming.session_id, ng_result["state"])
        return ng_result["reply"]


async def _invoice_guard(incoming, session_history: list) -> Optional[str]:
    """
    Handles invoice requests and order confirmations.

    CRITICAL FIX:
        Only checks for "confirmation" intent (is_fast_confirm, invoice_confirm_req)
        when awaiting_invoice_confirmation=True in the negotiation state.
        Without this guard, casual messages unrelated to confirming an order
        (e.g. "add 2 more units", "what about a different color") could be
        misclassified by the LLM as a confirmation and trigger premature
        invoice generation BEFORE the customer ever saw an order summary.

        invoice_inquiry (explicit "send my invoice" / "where is my invoice")
        is still checked unconditionally — that's always a valid standalone request.
    """
    from ai.invoice_handler import (
        handle_invoice_request, _is_invoice_inquiry, _is_invoice_confirmation_request,
    )
    from db.session_store import get_negotiation_state

    pre_neg_state = getattr(incoming, '_cached_neg_state', None) or         await get_negotiation_state(incoming.tenant_id, incoming.session_id)

    # Only treat this as a possible "confirmation" message if the bot is
    # ACTUALLY waiting on a Confirm/Proceed reply right now.
    awaiting_conf = bool(pre_neg_state and pre_neg_state.get("awaiting_invoice_confirmation", False))

    # Explicit invoice inquiry ("send invoice", "where is my invoice") is always valid
    invoice_inquiry = await _is_invoice_inquiry(incoming)

    is_fast_confirm      = False
    invoice_confirm_req  = False
    if awaiting_conf:
        is_fast_confirm = await _check_fast_confirm(incoming, pre_neg_state)
        invoice_confirm_req = await _is_invoice_confirmation_request(incoming, session_history)

    if not (invoice_inquiry or is_fast_confirm or invoice_confirm_req):
        return None

    if pre_neg_state and pre_neg_state.get("quantity") and pre_neg_state.get("last_offer_price"):
        return await _confirm_negotiated_order(incoming, pre_neg_state)

    return await handle_invoice_request(incoming)


async def _check_fast_confirm(incoming, neg_state) -> bool:
    if not neg_state or not neg_state.get("awaiting_invoice_confirmation", False):
        return False
    from utils.conversation_actions import is_quick_confirm
    if is_quick_confirm(incoming):
        print(f"[FAST CONFIRM] Phrase match (no LLM): '{incoming.text.strip()}'")
        return True
    try:
        from openai import AzureOpenAI
        from config import (
            AZURE_AI_ENDPOINT, AZURE_AI_API_KEY,
            AZURE_OPENAI_DEPLOYMENT, AZURE_AI_API_VERSION,
        )
        _client = AzureOpenAI(
            azure_endpoint=AZURE_AI_ENDPOINT, api_key=AZURE_AI_API_KEY,
            api_version=AZURE_AI_API_VERSION, timeout=10.0, max_retries=0,
        )
        resp = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=5, temperature=0,
                messages=[
                    {"role": "system", "content": _load_fast_confirm_prompt(incoming)},
                    {"role": "user", "content": incoming.text},
                ],
            )
        )
        content = resp.choices[0].message.content
        if not content:
            return False
        return "YES" in content.strip().upper()
    except Exception as e:
        print(f"[FAST CONFIRM] LLM check failed: {e}")
        return False


async def _confirm_negotiated_order(incoming, neg_state: dict) -> str:
    from ai.order_service import complete_order
    from db.session_store import clear_negotiation_state
    from ai.invoice_handler import handle_invoice_request

    neg_rounds = int(neg_state.get("rounds", 0))
    auto_price = neg_state.get("auto_offer_unit_price")
    last_price = neg_state.get("last_offer_price")

    # FIX: explicitly guard against None before float() — avoids passing
    # None into float() which Pyrefly (correctly) flags as unsafe.
    if neg_rounds > 0 and last_price is not None:
        agreed = float(last_price)
    elif auto_price is not None:
        agreed = float(auto_price)
    elif last_price is not None:
        agreed = float(last_price)
    else:
        agreed = 0.0

    quantity   = int(neg_state.get("quantity", 0))
    product    = neg_state.get("product_name", "")

    if not product or agreed <= 0 or quantity <= 0:
        return await handle_invoice_request(incoming)

    # PricingResult: single source of truth for all pricing fields
    # No recalculation anywhere else — summary, invoice, DB all use same values
    from ai.pricing import PricingResult
    pr = PricingResult.from_neg_state(neg_state, incoming.gst_rate)
    agreed = pr.negotiated_unit_price  # use PricingResult value, not raw state

    items = [{
        "product_name":   product,
        "quantity_value": quantity,
        "quantity_unit":  "units",
        "unit_price":     agreed,
        "total_price":    pr.subtotal,
    }]
    new_order = await complete_order(
        tenant_id   = incoming.tenant_id,
        session_id  = incoming.session_id,
        sender_name = incoming.sender_name,
        items       = items,
        gst_rate    = incoming.gst_rate,
        extra_fields = pr.to_invoice_fields(),
    )
    await clear_negotiation_state(incoming.tenant_id, incoming.session_id)

    # Clear stale conversational context now that the order is placed —
    # last discussed product, the numbered product-selection list, and any
    # pending order should not leak into whatever the customer asks next.
    # Fire-and-forget: never let this add latency to the invoice response.
    try:
        from db.session_store import clear_post_order_context
        asyncio.create_task(clear_post_order_context(incoming.tenant_id, incoming.session_id))
    except Exception:
        pass  # never block invoice for a context-cleanup save

    # Save negotiation outcome to Mem0 so customer profile builds over time.
    # After a few orders, get_negotiation_profile() returns avg discount accepted,
    # typical rounds, and budget range — enabling smarter opening offers.
    # Fire-and-forget: never let this block the invoice path.
    try:
        price_num = float(neg_state.get("price_num") or pr.regular_unit_price)
        asyncio.create_task(_save_neg_outcome_async(
            tenant_id     = incoming.tenant_id,
            session_id    = incoming.session_id,
            product       = product,
            opening_price = price_num,
            final_price   = pr.negotiated_unit_price,
            rounds        = int(neg_state.get("rounds", 0)),
            accepted      = True,
            quantity      = quantity,
        ))
    except Exception:
        pass   # never block invoice for a memory save
    # to handle_invoice_request, which requires a non-None negotiated_order.
    if new_order is None:
        print(f"[ORDER] create_order returned None — falling back to standard invoice flow")
        return await handle_invoice_request(incoming)

    return await handle_invoice_request(incoming, negotiated_order=new_order)


def _build_order_summary(incoming, product, agreed_price, qty, sub, gst, tot, state) -> str:
    price_raw = float(state.get("price_num") or agreed_price)
    auto_unit = float(state.get("auto_offer_unit_price") or agreed_price)
    auto_pct  = int(state.get("auto_offer_disc_pct") or 0)
    s_save    = round((price_raw - auto_unit) * qty, 2)
    n_save    = round((auto_unit - agreed_price) * qty, 2)
    tot_save  = round((price_raw - agreed_price) * qty, 2)
    gst_pct   = int(incoming.gst_rate * 100)

    from db.prompt_store import get_prompt

    # Which scenario applies is a data-availability decision (does a store
    # discount exist? was there also a negotiated reduction beyond it?) —
    # that's not wording, so it stays in Python. Each scenario's actual
    # message is fully DB-driven with a hardcoded English fallback only if
    # the tenant hasn't seeded these prompts yet.
    if auto_pct and s_save > 0 and n_save > 0:
        try:
            body = get_prompt(
                incoming, "order_summary_full_discount_prompt",
                sender_name=incoming.sender_name, product=product, qty=qty,
                price_raw=f"{price_raw:,.0f}", auto_pct=auto_pct, auto_unit=f"{auto_unit:,.0f}",
                agreed_price=f"{agreed_price:,.0f}", sub=f"{sub:,.0f}",
                gst_pct=gst_pct, gst=f"{gst:,.2f}", tot=f"{tot:,.2f}",
            )
        except RuntimeError:
            body = (
                f"Here's your updated order summary, {incoming.sender_name}! 🎉\n\n"
                f"• *Product:* {product}\n• *Quantity:* {qty} units\n"
                f"• *Regular price:* Rs.{price_raw:,.0f}/unit\n"
                f"• *Store offer {auto_pct}% OFF:* Rs.{auto_unit:,.0f}/unit\n"
                f"• *Negotiated price:* Rs.{agreed_price:,.0f}/unit\n"
                f"• *Subtotal:* Rs.{sub:,.0f}\n• *GST ({gst_pct}%):* Rs.{gst:,.2f}\n"
                f"• *Total Payable:* Rs.{tot:,.2f}"
            )
    elif auto_pct and s_save > 0:
        try:
            body = get_prompt(
                incoming, "order_summary_store_discount_only_prompt",
                sender_name=incoming.sender_name, product=product, qty=qty,
                price_raw=f"{price_raw:,.0f}", auto_pct=auto_pct, auto_unit=f"{auto_unit:,.0f}",
                sub=f"{sub:,.0f}", gst_pct=gst_pct, gst=f"{gst:,.2f}", tot=f"{tot:,.2f}",
            )
        except RuntimeError:
            body = (
                f"Here's your updated order summary, {incoming.sender_name}! 🎉\n\n"
                f"• *Product:* {product}\n• *Quantity:* {qty} units\n"
                f"• *Regular price:* Rs.{price_raw:,.0f}/unit\n"
                f"• *Store offer {auto_pct}% OFF:* Rs.{auto_unit:,.0f}/unit\n"
                f"• *Subtotal:* Rs.{sub:,.0f}\n• *GST ({gst_pct}%):* Rs.{gst:,.2f}\n"
                f"• *Total Payable:* Rs.{tot:,.2f}"
            )
    else:
        try:
            body = get_prompt(
                incoming, "order_summary_plain_price_prompt",
                sender_name=incoming.sender_name, product=product, qty=qty,
                agreed_price=f"{agreed_price:,.0f}", sub=f"{sub:,.0f}",
                gst_pct=gst_pct, gst=f"{gst:,.2f}", tot=f"{tot:,.2f}",
            )
        except RuntimeError:
            body = (
                f"Here's your updated order summary, {incoming.sender_name}! 🎉\n\n"
                f"• *Product:* {product}\n• *Quantity:* {qty} units\n"
                f"• *Price per unit:* Rs.{agreed_price:,.0f}\n"
                f"• *Subtotal:* Rs.{sub:,.0f}\n• *GST ({gst_pct}%):* Rs.{gst:,.2f}\n"
                f"• *Total Payable:* Rs.{tot:,.2f}"
            )

    if tot_save > 0:
        try:
            body += "\n\n" + get_prompt(incoming, "order_summary_savings_line_prompt", tot_save=f"{tot_save:,.0f}")
        except RuntimeError:
            body += f"\n\n🎁 *You save Rs.{tot_save:,.0f} on this order!*"

    try:
        body += "\n\n" + get_prompt(incoming, "order_summary_footer_prompt")
    except RuntimeError:
        body += "\n\nReply *Confirm* to place your order and receive your invoice! 🎉"

    return body


async def _save_neg_outcome_async(
    tenant_id: str, session_id: str, product: str,
    opening_price: float, final_price: float,
    rounds: int, accepted: bool, quantity: int,
) -> None:
    """Fire-and-forget negotiation outcome save to Postgres database."""
    try:
        from db.customer_data_service import CustomerDataService
        cds = CustomerDataService(tenant_id, session_id)
        await cds.save_negotiation_outcome(
            product       = product,
            opening_price = opening_price,
            final_price   = final_price,
            rounds        = rounds,
            accepted      = accepted,
            quantity      = quantity,
        )
    except Exception as e:
        print(f"[Database] save_negotiation_outcome failed (non-critical): {e}")