# ai/product_followup.py — Product follow-up resolution engine
#
# Extracted from main.py to keep the orchestrator lightweight.
# Contains: _try_resolve_product_followup, _parse_followup_message,
#           _get_active_product_context, _handle_comparison
# All imports must be explicit — no globals from main.py.

import re
import asyncio
import json
import time
from typing import Any, Optional, cast
from openai import AzureOpenAI

from config import (
    AZURE_AI_ENDPOINT, AZURE_AI_API_KEY,
    AZURE_OPENAI_DEPLOYMENT, AZURE_AI_API_VERSION,
)
from db.session_store import (
    get_graphrag_product_selection,
    get_negotiation_state,
    save_negotiation_state,
    clear_negotiation_state,
    get_product_api_response,
    get_cached_product_by_name,
    save_last_discussed_product,
    get_last_discussed_product,
    save_graphrag_product_selection,
    get_tenant_offers,
    save_outbound_message,
)
from db.prompt_store import get_prompt
from ai.negotiator import (
    is_negotiation_request,
    handle_negotiation,
)
from messaging import send_reply, send_image

_client = AzureOpenAI(
    azure_endpoint = AZURE_AI_ENDPOINT,
    api_key        = AZURE_AI_API_KEY,
    api_version    = AZURE_AI_API_VERSION,
    timeout        = 30.0,
    max_retries    = 0,
)
from ai.request_profiler import wrap_llm_client as _wrap_llm_client
_wrap_llm_client(_client)


def _format_faq_entry(f) -> dict:
    """External product APIs sometimes return faq entries as plain strings
    instead of {"question", "answer"} dicts — mirrors the same dual-shape
    handling already used for the "policies" field just above."""
    if isinstance(f, dict):
        return {"q": f.get("question"), "a": f.get("answer")}
    return {"q": f, "a": ""}


async def _parse_followup_message(incoming, selection: list, session_history: Optional[list] = None) -> dict:
    """
    Uses LLM to parse the follow-up message to identify if they are:
    - selecting a product by name (selected_product_name) — NAME ONLY, no numeric index selection
    - specifying quantity/unit (quantity, quantity_unit)
    - requesting comparison (is_comparison)
    - requesting images (asks_for_image)
    - performing a new category search / broad search (is_new_search)
    Zero hardcoding.
    """
    # Reuse cached quick_parsed dictionary if already processed in this request
    cached = getattr(incoming, "_cached_quick_parsed", None)
    if cached is not None:
        return cached

    product_names = [p.get("product_name") or p.get("name") or "" for p in selection]
    # Build a numbered catalog string (1-based) so the LLM can resolve
    # index references like "tell me the features of 44" → product at position 44.
    # Numbers come directly from the DB-persisted selection order — no hardcoding.
    numbered_catalog = "\n".join(
        f"{i + 1}. {name}" for i, name in enumerate(product_names) if name
    )
    try:
        recent_history = session_history[-4:] if session_history else []
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model       = AZURE_OPENAI_DEPLOYMENT,
                max_tokens  = 200,
                temperature = 0,
                messages    = [
                    {"role": "system", "content": get_prompt(
                        incoming, "pf_data_extraction_prompt",
                        biz_name=incoming.biz_name,
                        product_catalog=numbered_catalog,
                    )},
                    *recent_history,
                    {"role": "user", "content": incoming.text},
                ],
            )
        )
        raw = response.choices[0].message.content
        if not raw:
            raise ValueError("Empty or None response content")
        content = raw.strip()
        # Clean up code fence formatting if any
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
        
        res = json.loads(content)
        incoming._cached_quick_parsed = res
        return res
    except Exception as e:
        print(f"[FOLLOW-UP] LLM parser failed: {e}")
        return {
            "selected_product_name": None,
            "quantity": None,
            "quantity_unit": None,
            "selected_options": None,
            "is_comparison": False,
            "is_recommendation": False,
            "is_offer_inquiry": False,
            "asks_for_image": False,
            "is_new_search": False
        }


async def _get_active_product_context(
    incoming,
    selection: list,
    session_history: list,
) -> list:
    """
    Scans recent session history to find which specific products were
    being discussed, and returns those as the active comparison context.

    Used when customer asks "which is better for budget?" or "suggest me one"
    without naming products — we first check if specific products were recently
    in focus. Only returns [] when truly nothing was discussed (fresh browse).

    Zero hardcoding — fully LLM-driven.
    """
    if not session_history:
        return []

    product_names = [
        p.get("product_name") or p.get("name") or ""
        for p in selection
        if p.get("product_name") or p.get("name")
    ]
    if not product_names:
        return []

    try:
        product_list_str = "\n".join(f"- {p}" for p in product_names)
        session_hist_str = "\n".join(
            f"{turn.get('role', 'user')}: {turn.get('content', '')}"
            for turn in session_history[-6:]
        )
        
        system_prompt = get_prompt(
            incoming, "pf_history_resolver_prompt",
            product_list = product_list_str,
            session_history = session_hist_str
        )

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model       = AZURE_OPENAI_DEPLOYMENT,
                max_tokens  = 120,
                temperature = 0,
                messages    = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": incoming.text},
                ],
            )
        )
        
        content = response.choices[0].message.content
        if content:
            raw_content = content.strip()
            if raw_content.startswith("```"):
                lines = raw_content.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw_content = "\n".join(lines).strip()
            
            discussed_names = json.loads(raw_content)
            if isinstance(discussed_names, list):
                active_products = []
                for name in discussed_names:
                    name_lower = str(name).lower().strip()
                    for p in selection:
                        pname = (p.get("product_name") or p.get("name") or "").lower()
                        if name_lower in pname or pname in name_lower:
                            if p not in active_products:
                                active_products.append(p)
                return active_products
    except Exception as e:
        print(f"[FOLLOW-UP] _get_active_product_context failed: {e}")

    return []


async def _handle_comparison(
    incoming,
    compared: list,
    session_history: list,
    show_recommendation: bool = False,
) -> str:
    """
    Generates a side-by-side comparison response for the compared products.
    """
    if not compared:
        return "I had trouble with that right now. Which product would you like to know more about?"

    products_data_lines = []
    price_reference_lines = []
    for p in compared:
        name = p.get("product_name") or p.get("name") or "Unnamed Product"
        price = p.get("list_price") or p.get("price") or 0.0
        price_reference_lines.append(f"- {name}: Rs.{float(price):,.2f}")
        
        features = p.get("features") or []
        if isinstance(features, list):
            features_str = ", ".join(features)
        else:
            features_str = str(features)
            
        specs = p.get("specs") or []
        if isinstance(specs, list):
            specs_str = "; ".join(f"{s.get('name')}: {s.get('value')}" if isinstance(s, dict) else str(s) for s in specs)
        else:
            specs_str = str(specs)
            
        desc = p.get("description") or p.get("short_description") or p.get("feature_descriptions") or ""
        
        products_data_lines.append(
            f"Product: {name}\n"
            f"Price: Rs.{float(price):,.2f}\n"
            f"Features: {features_str}\n"
            f"Specs: {specs_str}\n"
            f"Description: {desc}\n"
        )
        
    products_data = "\n\n".join(products_data_lines)
    price_reference = "\n".join(price_reference_lines)

    system_prompt = get_prompt(
        incoming, "pf_comparison_prompt",
        sender_name = incoming.sender_name,
        biz_name = incoming.biz_name,
        products_data = products_data,
        price_reference = price_reference,
    )

    try:
        user_message = incoming.text
        if show_recommendation:
            user_message += "\n(Please emphasize a recommendation based on my query.)"

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model       = AZURE_OPENAI_DEPLOYMENT,
                max_tokens  = 900,
                temperature = 0,
                messages    = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Empty or None response content")
        return content.strip()
    except Exception as e:
        print(f"[FOLLOW-UP] Comparison/recommendation LLM failed: {e}")
        return "I had trouble with that right now. Which product would you like to know more about?"


async def _try_resolve_product_followup(incoming, session_history: list):
    """
    Checks if the customer's message is a follow-up about a product they already
    saw in a previous GraphRAG result (PRODUCT_SELECTION in workflow_sessions).

    RESOLVES TWO CASES:
        1. Name match / comparison: "tell me about Romy", "compare Romy and Reva"
           → word-score customer message against product names in selection,
             or routes to _handle_comparison for multi-product comparisons
        2. Pure follow-up: "is it aluminum?", "what's the warranty?", "1 unit"
           → scan last bot messages to find which product was last discussed

    Returns:
        str  → LLM answer using product data from cache
        None → not a product follow-up, let call_graphrag_api() handle it
    """
    # Load negotiation state early (checking incoming cache first to prevent redundant DB calls)
    neg_state = getattr(incoming, "_cached_neg_state", None)
    if neg_state is None:
        neg_state = await get_negotiation_state(incoming.tenant_id, incoming.session_id)

    selection = await get_graphrag_product_selection(incoming.tenant_id, incoming.session_id)
    if not selection:
        last_prod = await get_last_discussed_product(incoming.tenant_id, incoming.session_id)
        if not last_prod and neg_state:
            last_prod = neg_state.get("product_name")
            if last_prod:
                print(f"[FOLLOW-UP] No active selection or last discussed product found - loaded product from active negotiation state: {last_prod}")
        if last_prod:
            selection = [{
                "product_name": last_prod,
                "name": last_prod,
            }]
            if not neg_state or last_prod != neg_state.get("product_name"):
                print(f"[FOLLOW-UP] No active selection found - loaded last discussed product from DB: {last_prod}")
        else:
            return None

    # (Number selection is now handled dynamically by the DB prompt templates)

    # ── Standard follow-up parsing ────────────────────────────────────────────
    # ── Negotiation check ────────────────────────────────────────────────────
    # If customer asks for discount OR has active negotiation state, handle it.
    # Runs BEFORE standard follow-up parsing.
    # New-search guard: if customer asks for a new product category, clear
    # any stale negotiation state and route to GraphRAG instead.

    # Bypasses negotiation if the user is asking for a specific non-none knowledge field (e.g. images, installation)
    _routing = getattr(incoming, "_routing", None)
    _req_field = _routing.requested_knowledge_field if _routing else None
    _bypass_negotiation = False
    if _req_field and _req_field.lower() not in ("none", ""):
        print(f"[FOLLOW-UP] requested field '{_req_field}' — bypassing negotiation check")
        _bypass_negotiation = True
        neg_state = None

    # ── Independent early classifiers — run concurrently ─────────────────────
    # Offer-inquiry precheck, the main follow-up parser, and the dedicated
    # negotiation-intent check don't depend on each other's *inputs*, so they
    # were previously paying for their latency sequentially (~4-7s combined
    # per production timing logs) even though nothing required that ordering.
    # Running them via asyncio.gather collapses that to the slowest single
    # call (~2-2.7s).
    #
    # Trade-off: is_negotiation_request() can no longer be skipped based on
    # the offer-inquiry precheck's result (that isn't known until both calls
    # finish together) — it's still skipped when _bypass_negotiation is
    # already True from routing (known ahead of time, no LLM call needed).
    # On messages where the precheck later confirms an offer inquiry, this
    # means one extra LLM call whose result gets discarded below, traded for
    # not waiting on it sequentially.
    _t_parse_early = time.monotonic()

    async def _offer_inquiry_precheck() -> bool:
        try:
            _oiq = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _client.chat.completions.create(
                    model=AZURE_OPENAI_DEPLOYMENT, max_tokens=5, temperature=0,
                    messages=[
                        {"role": "system", "content": get_prompt(incoming, "pf_offer_inquiry_check_prompt")},
                        {"role": "user", "content": incoming.text},
                    ],
                )
            )
            _content = _oiq.choices[0].message.content
            if _content and "YES" in _content.strip().upper():
                print(f"[OFFER INQUIRY] Pre-check YES: '{incoming.text}'")
                return True
        except Exception as _oiqe:
            print(f"[OFFER INQUIRY] Pre-check failed: {_oiqe}")
        return False

    async def _neg_request_check() -> bool:
        if _bypass_negotiation:
            return False
        return await is_negotiation_request(incoming.text, incoming, session_history)

    _is_offer_inq, quick_parsed, _is_neg_req = await asyncio.gather(
        _offer_inquiry_precheck(),
        _parse_followup_message(incoming, selection, session_history),
        _neg_request_check(),
    )
    print(f"[TIMING] early classifiers (parallel — offer/parse/neg-check): {time.monotonic() - _t_parse_early:.2f}s")

    # Merge pre-check with parser
    _is_offer_inq = _is_offer_inq or quick_parsed.get("is_offer_inquiry", False)
    # Precheck/parser confirming an offer-inquiry after the fact overrides
    # whatever the concurrently-run negotiation check returned.
    if _is_offer_inq:
        _is_neg_req = False

    # is_comparison/recommendation/offer_inquiry = never a price negotiation.
    if (quick_parsed.get("is_comparison", False)
            or quick_parsed.get("is_recommendation", False)
            or _is_offer_inq):
        print(f"[FOLLOW-UP] is_comparison/recommendation/offer_inquiry — bypassing negotiation")
        neg_state = None
    elif neg_state:
        if quick_parsed.get("is_new_search", False):
            print(f"[NEGOTIATOR] New search — clearing stale negotiation state")
            await clear_negotiation_state(incoming.tenant_id, incoming.session_id)
            neg_state = None
        elif neg_state.get("product_name"):
            saved_product = (neg_state.get("product_name") or "").lower().strip()
            current_products = [
                (p.get("product_name") or p.get("name") or "").lower().strip()
                for p in selection
            ]
            product_still_active = any(
                saved_product[:10] in cp or cp[:10] in saved_product
                for cp in current_products
                if cp
            )
            if current_products and not product_still_active:
                try:
                    prod_check = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: _client.chat.completions.create(
                            model       = AZURE_OPENAI_DEPLOYMENT,
                            max_tokens  = 5,
                            temperature = 0,
                            messages    = [
                                {"role": "system", "content": get_prompt(incoming, "pf_neg_product_change_check_prompt", current_product=saved_product)},
                                {"role": "user", "content": incoming.text},
                            ],
                        )
                    )
                    content = prod_check.choices[0].message.content
                    is_new_product = content is not None and "YES" in content.strip().upper()
                except Exception:
                    is_new_product = False

                if is_new_product:
                    print(f"[NEGOTIATOR] Product changed from '{saved_product}' — clearing stale negotiation state")
                    await clear_negotiation_state(incoming.tenant_id, incoming.session_id)
                    neg_state = None

    # _is_neg_req was already computed concurrently above (in the
    # asyncio.gather with the offer-inquiry precheck and the parser).
    if neg_state or _is_neg_req:
        # Resolve which product is being negotiated — priority order:
        # 1. Active negotiation state (already has product_name)
        # 2. Last discussed product from DB
        # 3. Fallback to first in selection
        product_name = (neg_state or {}).get("product_name")

        if not product_name:
            try:
                product_name = await get_last_discussed_product(
                    incoming.tenant_id, incoming.session_id
                )
                if product_name:
                    print(f"[NEGOTIATOR] Using last discussed product: {product_name}")
            except Exception as e:
                print(f"[NEGOTIATOR] get_last_discussed_product failed: {e}")

        if not product_name and getattr(incoming, 'quoted_caption', None):
            for p in selection:
                pname = p.get("product_name") or p.get("name") or ""
                first_word = pname.lower().split()[0] if pname else ""
                if first_word and len(first_word) > 3 and first_word in incoming.quoted_caption.lower():
                    product_name = pname
                    print(f"[NEGOTIATOR] Resolved from quoted caption: {product_name}")
                    break

        if not product_name and selection:
            product_name = selection[0].get("product_name") or selection[0].get("name")
            print(f"[NEGOTIATOR] Fallback to first in selection: {product_name}")

        if product_name:
            cached = await get_cached_product_by_name(incoming.tenant_id, product_name)
            if cached:
                price_num      = float(cached.get("list_price") or 0)
                regular_price  = float(cached.get("regular_price") or price_num)
                discount_pct   = int(cached.get("discount_pct") or 0)

                # IMPORTANT: price_num must always be the TRUE LIST PRICE from the
                # product cache. Never overwrite it with auto_offer_unit_price.
                # auto_offer_unit_price is only used as the negotiation STARTING POINT
                # inside handle_negotiation — it must NOT corrupt price_num itself,
                # otherwise every subsequent quantity change re-discounts an already-
                # discounted price (compounding discount bug) and "regular price" in
                # the order summary shows the discounted price instead of the real one.

                if price_num > 0:
                    current_state = neg_state or {
                        "rounds":            0,
                        "quantity":          None,
                        "last_offer_price":  None,
                        "floor_price":       None,
                        "product_name":      product_name,
                        "price_num":         price_num,
                        "awaiting_quantity": False,
                    }

                    result = await handle_negotiation(
                        incoming               = incoming,
                        product_name           = product_name,
                        price_num              = price_num,
                        regular_price          = regular_price,
                        graphrag_discount_pct  = discount_pct,
                        session_history        = session_history,
                        negotiation_state      = current_state,
                        global_offers          = (
                            (cached.get("global_offers") if cached else None)
                            or (current_state or {}).get("global_offers")
                            or (lambda _t: _t.get("offers_text") if _t else None)(
                                await get_tenant_offers(incoming.tenant_id)
                            )
                        ),
                    )

                    await save_negotiation_state(
                        incoming.tenant_id, incoming.session_id, result["state"]
                    )

                    # DEFERRAL: the negotiator determined this message isn't
                    # negotiation-related after all (e.g. a greeting or an
                    # escalation request arriving mid-negotiation) — route it
                    # properly instead of falling through with a None reply.
                    # Negotiation state above is already saved UNCHANGED, so
                    # the customer can resume negotiating afterward.
                    if result.get("defer_intent") is not None:
                        defer_res = result["defer_intent"]
                        _defer_intent = defer_res.intent
                        incoming._deferred_intent = _defer_intent  # so main.py's debug output reflects the real routing decision
                        
                        # Update incoming._routing with the real classifier routing
                        if hasattr(defer_res, "routing") and defer_res.routing:
                            incoming._routing = defer_res.routing
                            
                        print(f"[FOLLOW-UP] Negotiator deferred (intent={_defer_intent}) — routing directly")
                        routing = getattr(defer_res, "routing", None)
                        req_field = getattr(routing, "requested_knowledge_field", "none") or "none"
                        if routing and routing.needs_customer_history and req_field.lower() == "none":
                            from ai.customer_history_handler import handle_customer_history_query
                            return await handle_customer_history_query(incoming, session_history)
                        if _defer_intent == "GREETING":
                            from ai.handlers import handle_greeting
                            return await handle_greeting(incoming)
                        elif _defer_intent == "HUMAN_ESCALATION":
                            from ai.handlers import handle_escalation
                            return await handle_escalation(incoming)

                    if result["order_ready"] and result["agreed_price"]:
                        # FIX BUGS 1,2,3,4: order_ready=True means negotiation is concluded
                        # (floor reached, acceptance detected, or rounds exhausted).
                        # But we must NOT immediately show the pre-confirm summary.
                        #
                        # The negotiator may have returned a conversational reply explaining
                        # the counter-offer (e.g. "We can't go to Rs.2100 but here's our
                        # best at Rs.2319"). Show THAT reply first. The customer's next
                        # message (Confirm / accept / reject) is what triggers the summary.
                        #
                        # Only show the pre-confirm summary immediately when the negotiator
                        # has NO reply message (i.e. pure acceptance without counter).
                        negotiator_reply = result.get("reply", "")
                        if negotiator_reply and negotiator_reply.strip():
                            # Negotiator has a conversational reply (counter-offer explanation).
                            # Deliver it and set counter_offer_presented=True so the NEG GUARD
                            # knows we're in Phase 2 (counter presented, customer hasn't accepted).
                            # Do NOT set awaiting_invoice_confirmation=True here — the customer
                            # hasn't accepted yet and may still be bargaining.
                            print(f"[NEGOTIATOR] Delivering counter-offer reply — Phase 2 (counter_offer_presented)")
                            updated = {
                                **result["state"],
                                "counter_offer_presented": True,
                                "awaiting_invoice_confirmation": False,
                                "last_offer_price": result["agreed_price"],
                                "quantity": result["quantity"],
                            }
                            await save_negotiation_state(
                                incoming.tenant_id, incoming.session_id, updated
                            )
                            return negotiator_reply

                        # Guard: if already awaiting confirmation, don't show summary again
                        already_awaiting = False
                        if neg_state is not None:
                            already_awaiting = neg_state.get("awaiting_invoice_confirmation", False)
                        if already_awaiting:
                            assert neg_state is not None
                            old_agreed = float(neg_state.get("last_offer_price", 0))
                            new_agreed = float(result["agreed_price"])
                            if abs(old_agreed - new_agreed) < 1.0:
                                print(f"[NEGOTIATOR] Already awaiting confirmation at Rs.{old_agreed} — skipping duplicate summary")
                                return f"You've already confirmed Rs.{old_agreed:,.0f}/unit, {incoming.sender_name}. Please reply *Confirm* to place your order! 🎉"

                        # Do NOT create order yet — show summary first and wait for Confirm
                        agreed  = result["agreed_price"]
                        qty     = result["quantity"]
                        sub     = round(agreed * qty, 2)
                        gst     = round(sub * incoming.gst_rate, 2)
                        total   = round(sub * (1 + incoming.gst_rate), 2)
                        updated = {
                            **result["state"],
                            "awaiting_invoice_confirmation": True,
                            "last_offer_price": agreed,
                            "quantity": qty,
                        }
                        await save_negotiation_state(
                            incoming.tenant_id, incoming.session_id, updated
                        )
                        print(f"[NEGOTIATOR] Showing order summary before invoice")
                        # BUG-071: add next-tier upsell to confirmation summary
                        _conf_upsell = ""
                        try:
                            from ai.negotiator import parse_global_offer_tiers as _pt71, get_next_tier as _gnt71
                            _go71 = result["state"].get("global_offers", "")
                            if _go71:
                                _tiers71  = _pt71(incoming, _go71)
                                _ov71     = agreed * qty
                                _next71   = _gnt71(_ov71, _tiers71)
                                if _next71:
                                    _vgap71 = round(_next71[0] - _ov71, 0)
                                    _u71 = max(1, int(_vgap71 / agreed) + 1)
                                    _conf_upsell = (f"\n\n💡 Add Rs.{_vgap71:,.0f} more to your order value "
                                                    f"(approx. {_u71} more unit(s)) to reach Rs.{_next71[0]:,} "
                                                    f"and unlock *{_next71[1]}% off*!")
                        except Exception as _e71:
                            print(f"[CONFIRM] Upsell calc failed: {_e71}")
                        # ── FIX Bug 3 (Major): only show negotiation labels when
                        # the customer ACTUALLY negotiated (rounds > 0).
                        # Previously: any tier-price concession (gap/3 off tier_price)
                        # was labeled "Negotiated price" + "Negotiation savings" even
                        # when the customer never asked for a discount. The 8% tier
                        # price is a STORE OFFER, not a negotiated price.
                        _neg_rounds     = result["state"].get("rounds", 0)
                        _actually_negotiated = _neg_rounds > 0

                        _auto_disc_pct  = result["state"].get("auto_offer_disc_pct", 0)
                        _auto_unit      = result["state"].get("auto_offer_unit_price")

                        # Determine the current tier applied (may differ from the
                        # stored auto_offer_disc_pct if qty changed since first offer)
                        _current_tier_disc = result["state"].get("current_tier_disc", _auto_disc_pct or 0)

                        # Savings vs original list price — always show total saving
                        _t_save = round((price_num - agreed) * qty, 2)
                        lines = [
                            f"Here's your order summary, {incoming.sender_name}! Please review:",
                            "",
                            f"• *Product:* {product_name}",
                            f"• *Quantity:* {qty} units",
                        ]
                        if _actually_negotiated and _auto_unit and agreed < _auto_unit:
                            # Customer DID negotiate — show 3-tier breakdown
                            _s_save = round((price_num - _auto_unit) * qty, 2)
                            _n_save = round((_auto_unit - agreed) * qty, 2)
                            lines += [
                                f"• *Regular price:* Rs.{price_num:,.0f}/unit",
                                f"• *Store offer {_auto_disc_pct}% OFF:* Rs.{_auto_unit:,.0f}/unit",
                                f"• *Negotiated price:* Rs.{agreed:,.0f}/unit",
                            ]
                            if _t_save > 0 and _s_save > 0 and _n_save > 0:
                                lines += [
                                    "",
                                    f"🎁 *Total savings: Rs.{_t_save:,.0f}*",
                                    f"   • Store offer: Rs.{_s_save:,.0f}",
                                    f"   • Negotiation: Rs.{_n_save:,.0f}",
                                ]
                        elif _current_tier_disc > 0:
                            # No negotiation — only the store tier discount applies
                            lines += [
                                f"• *Regular price:* Rs.{price_num:,.0f}/unit",
                                f"• *Store offer {_current_tier_disc}% OFF:* Rs.{agreed:,.0f}/unit",
                            ]
                            if _t_save > 0:
                                lines.append(f"\n🎁 *You save Rs.{_t_save:,.0f} on this order!*")
                        else:
                            lines.append(f"• *Price per unit:* Rs.{agreed:,.0f}")
                            if _t_save > 0:
                                lines.append(f"\n🎁 *You save Rs.{_t_save:,.0f} on this order!*")

                        lines += [
                            f"• *Subtotal:* Rs.{sub:,.0f}",
                            f"• *GST ({int(incoming.gst_rate*100)}%):* Rs.{gst:,.2f}",
                            f"• *Total Payable:* Rs.{total:,.2f}",
                        ]
                        if _conf_upsell:
                            lines.append(_conf_upsell)
                        lines += ["", "Reply *Confirm* to place your order and receive your invoice! 🎉"]
                        return "\n".join(lines)

                    if result["escalate"]:
                        await clear_negotiation_state(incoming.tenant_id, incoming.session_id)

                    incoming._graphrag_raw = json.dumps({
                        "handler": "negotiation",
                        "product": product_name,
                        "rounds": result["state"].get("rounds"),
                        "agreed_price": result.get("agreed_price"),
                        "order_ready": result.get("order_ready"),
                    })
                    return result["reply"]

    # ── Standard follow-up parsing ────────────────────────────────────────────
    # quick_parsed is always set above (moved out of the neg_state block)
    # so we always reuse it here — zero duplicate LLM calls.
    if quick_parsed is not None:
        parsed = quick_parsed
        print(f"[FOLLOW-UP] Reusing quick_parsed (skipped duplicate LLM call): {parsed}")
    else:
        _t_parse_start = time.monotonic()
        parsed = await _parse_followup_message(incoming, selection, session_history)
        print(f"[TIMING] _parse_followup_message: {time.monotonic() - _t_parse_start:.2f}s")
        print(f"[FOLLOW-UP] LLM parsed: {parsed}")
    
    # ── Check if user wants to start a new search ────────────────────────────
    # ── Guard: if message matches a product in the current selection list,
    # it is a SELECTION not a new search — even if LLM says is_new_search=True.
    # Happens when bot displays "Outdoor LED Gate Lamp Lights" and customer
    # replies with exactly that text. LLM classifies it as a category search
    # but it is actually selecting item from the list the bot showed.
    _msg_lower = incoming.text.lower().strip()
    _selection_names = [
        (p.get("product_name") or p.get("name") or "").lower().strip()
        for p in selection
    ]
    _matches_selection = any(
        _msg_lower == name
        for name in _selection_names if name
    )
    if _matches_selection and parsed.get("is_new_search", False):
        print(f"[FOLLOW-UP] is_new_search overridden — message matches selection list item: '{incoming.text}'")
        parsed["is_new_search"] = False
        # Also set selected_product_name if not already set
        if not parsed.get("selected_product_name"):
            for p in selection:
                pname = (p.get("product_name") or p.get("name") or "").lower().strip()
                if _msg_lower == pname:
                    parsed["selected_product_name"] = p.get("product_name") or p.get("name")
                    print(f"[FOLLOW-UP] Auto-resolved selected_product_name: '{parsed['selected_product_name']}'")
                    break

    # ── Numeric index guard (before is_new_search check) ─────────────────────
    # "tell me the features of 28", "details about 4", bare "28" etc.
    # The LLM sees a number and can't map it to a product name, so it returns
    # is_new_search=True. We intercept here — if the number falls in range of
    # the active selection list, override to the correct product before routing.
    if not parsed.get("selected_product_name") or parsed.get("is_new_search", False):
        import re as _re
        _num_match = _re.search(r'\b(\d{1,3})\b', incoming.text)
        if _num_match:
            _idx = int(_num_match.group(1))
            if 1 <= _idx <= len(selection):
                _resolved = selection[_idx - 1]
                _resolved_name = _resolved.get("product_name") or _resolved.get("name")
                if _resolved_name:
                    print(f"[FOLLOW-UP] Numeric guard: index {_idx} → '{_resolved_name}' (overriding is_new_search)")
                    parsed["selected_product_name"] = _resolved_name
                    parsed["is_new_search"] = False

    if parsed.get("is_new_search", False):
        print(f"[FOLLOW-UP] LLM parser identified category search/new search — routing to GraphRAG")
        # Flag on `incoming` (same pattern as incoming._routing/_cached_arc) so
        # call_graphrag_api's query-enrichment step doesn't re-inject the old
        # active_product_session's resolved_product into this new search below.
        incoming._is_new_category_search = True

        # QUERY ENRICHMENT — LLM-driven, zero hardcoded word lists.
        # Only enriches when query has purely vague references (no product info).
        # "related products for this" → enrich with last product ✅
        # "outdoor lights" → skip enrichment (already specific) ✅
        # Two-step: first check if purely vague, then rewrite only if YES.
        selected_product = parsed.get("selected_product_name")
        try:
            last_product = await get_last_discussed_product(
                incoming.tenant_id, incoming.session_id
            )
            # Only enrich when LLM resolved to same last product (vague ref)
            # AND query has no specific product/category info
            should_enrich = (
                last_product
                and selected_product
                and selected_product.lower() == last_product.lower()
            )
            if should_enrich and incoming.text:
                check_resp = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: _client.chat.completions.create(
                        model       = AZURE_OPENAI_DEPLOYMENT,
                        max_tokens  = 5,
                        temperature = 0,
                        messages    = [
                            {"role": "system", "content": get_prompt(incoming, "pf_vague_reference_check_prompt")},
                            {"role": "user", "content": incoming.text},
                        ],
                    )
                )
                content = check_resp.choices[0].message.content
                is_vague = content is not None and "YES" in content.strip().upper()
                if is_vague:
                    enrich_resp = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: _client.chat.completions.create(
                            model       = AZURE_OPENAI_DEPLOYMENT,
                            max_tokens  = 80,
                            temperature = 0,
                            messages    = [
                                {"role": "system", "content": get_prompt(incoming, "pf_vague_reference_rewriter_prompt", product_name=last_product)},
                                {"role": "user", "content": incoming.text},
                            ],
                        )
                    )
                    content_enrich = enrich_resp.choices[0].message.content
                    enriched = content_enrich.strip() if content_enrich else ""
                    if enriched and enriched != incoming.text:
                        print(f"[FOLLOW-UP] Query enriched: '{incoming.text[:50]}' → '{enriched[:80]}'")
                        incoming.text = enriched
                else:
                    print(f"[FOLLOW-UP] New category search — skipping enrichment")
        except Exception as e:
            print(f"[FOLLOW-UP] Enrichment failed (non-critical): {e}")

        return None

    # NOTE: numeric list-index selection (picking "57" to mean item #57)
    # has been REMOVED entirely. It was unreliable on long product lists
    # (90+ items) and collided with quantity parsing ("57" meaning 57 units).
    # Customers must now select products by NAME only.
    is_comparison     = parsed.get("is_comparison", False)
    is_recommendation = parsed.get("is_recommendation", False)
    is_offer_inquiry  = _is_offer_inq or parsed.get("is_offer_inquiry", False)
    asks_for_image    = parsed.get("asks_for_image", False)

    matched_product = None

    # ── Case 0: Offer inquiry ─────────────────────────────────────────────────
    # Two-layer detection — layer 2 specifically handles "offers for [product name]"
    # which layer 1 sometimes misses due to product context.
    if not is_offer_inquiry:
        try:
            _oi = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _client.chat.completions.create(
                    model       = AZURE_OPENAI_DEPLOYMENT,
                    max_tokens  = 5,
                    temperature = 0,
                    messages    = [
                        {"role": "system", "content": get_prompt(incoming, "pf_offer_inquiry_check_l2_prompt", customer_message=incoming.text)},
                        {"role": "user", "content": incoming.text},
                    ],
                )
            )
            content = _oi.choices[0].message.content
            if content and "YES" in content.strip().upper():
                is_offer_inquiry = True
                print(f"[OFFER INQUIRY] Detected via layer-2: '{incoming.text}'")
        except Exception as _e:
            print(f"[OFFER INQUIRY] Layer-2 check failed: {_e}")

    if is_offer_inquiry:
        _offers_text = None
        _price_num   = None
        _prod_name   = None

        # ── Priority 1: Use last-discussed product for price calculation ─────
        # Customer asked about Romy → "any offers?" → calculate for Romy, not
        # whatever random product is first in the selection list.
        try:
            _last_prod = await get_last_discussed_product(incoming.tenant_id, incoming.session_id)
            if _last_prod:
                _lcp = await get_cached_product_by_name(incoming.tenant_id, _last_prod)
                if _lcp:
                    _lgo = _lcp.get("global_offers")
                    if _lgo and str(_lgo).strip():
                        _offers_text = str(_lgo).strip()
                        _price_num   = float(_lcp.get("list_price") or 0)
                        _prod_name   = _last_prod
                        print(f"[OFFER INQUIRY] Using last-discussed: '{_prod_name}' @ Rs.{_price_num:,.0f}")
        except Exception as _lde:
            print(f"[OFFER INQUIRY] last_discussed_product lookup failed: {_lde}")

        # ── Priority 2: Check message for named product ───────────────────────
        if not _offers_text:
            # Extract product name from the message (e.g. "any offers for Romy 12W?")
            try:
                _pm = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: _client.chat.completions.create(
                        model=AZURE_OPENAI_DEPLOYMENT, max_tokens=30, temperature=0,
                        messages=[
                            {"role": "system", "content": get_prompt(
                                incoming, "pf_named_product_extractor_prompt",
                                product_list=json.dumps(
                                    [p.get("product_name") or p.get("name") or "" for p in selection],
                                    ensure_ascii=False,
                                ),
                                customer_message=incoming.text,
                            )},
                            {"role": "user", "content": incoming.text},
                        ],
                    )
                )
                content = _pm.choices[0].message.content
                _named = content.strip() if content else ""
                if _named and _named.upper() != "NONE":
                    for p in selection:
                        pname = p.get("product_name") or p.get("name")
                        if pname and (_named.lower()[:8] in pname.lower() or pname.lower()[:8] in _named.lower()):
                            _cp2 = await get_cached_product_by_name(incoming.tenant_id, pname)
                            _go2 = (_cp2 or p).get("global_offers")
                            if _go2 and str(_go2).strip():
                                _offers_text = str(_go2).strip()
                                _price_num   = float((_cp2 or p).get("list_price") or p.get("price_num") or 0)
                                _prod_name   = pname
                                break
            except Exception:
                pass

        # ── Priority 3: First product in selection with cached global_offers ──
        if not _offers_text:
            for p in selection[:5]:
                pname = p.get("product_name") or p.get("name")
                if pname:
                    _cp = await get_cached_product_by_name(incoming.tenant_id, pname)
                    _go = (_cp or p).get("global_offers")
                    if _go and str(_go).strip():
                        _offers_text = str(_go).strip()
                        _price_num   = float((_cp or p).get("list_price") or p.get("price_num") or 0)
                        _prod_name   = pname
                        break

        # ── Priority 4: tenant_offers table ──────────────────────────────────
        if not _offers_text:
            try:
                _to = await get_tenant_offers(incoming.tenant_id)
                if _to:
                    _offers_text = _to.get("offers_text")
            except Exception:
                pass

        if _offers_text:
            # Customer is about to see real offer content — persist this so
            # future auto-apply decisions (and negotiation baseline pricing)
            # know disclosure has happened. Tenant-configurable via
            # require_offer_disclosure — see the auto-apply gate below and
            # negotiator.py's baseline price resolution.
            try:
                _existing_neg = await get_negotiation_state(incoming.tenant_id, incoming.session_id) or {}
                _existing_neg["offer_disclosed"] = True
                await save_negotiation_state(incoming.tenant_id, incoming.session_id, _existing_neg)
                print(f"[OFFER] offer_disclosed=True persisted for {incoming.session_id}")
            except Exception as _ode:
                print(f"[OFFER] Failed to persist offer_disclosed (non-critical): {_ode}")

            _tier_ctx = ""
            try:
                from ai.negotiator import parse_global_offer_tiers as _pt
                _tiers = _pt(incoming, _offers_text)
                if _tiers and _price_num and _price_num > 0:
                    _lines = []
                    for _mv, _dp in _tiers:
                        _dp_price  = round(_price_num * (1 - _dp / 100), 2)
                        _min_units = max(1, int(_mv / _price_num) + (1 if _mv % _price_num else 0))
                        _lines.append(
                            f"  Rs.{_mv:,}+ order → {_dp}% off → "
                            f"Rs.{_dp_price:,.0f}/unit (≈{_min_units}+ units)"
                        )
                    _tier_ctx = (
                        f"\n\nCalculated prices for {_prod_name} (Rs.{_price_num:,.0f}/unit):\n"
                        + "\n".join(_lines)
                    )
            except Exception:
                pass

            try:
                _fmt = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: _client.chat.completions.create(
                        model       = AZURE_OPENAI_DEPLOYMENT,
                        max_tokens  = 350,
                        temperature = 0.3,
                        messages    = [
                            {"role": "system", "content": get_prompt(
                                incoming, "pf_offers_formatter_prompt",
                                biz_name=incoming.biz_name,
                                sender_name=incoming.sender_name,
                                offers_data=_offers_text,
                                product_context=_tier_ctx or _prod_name or "",
                            )},
                            {"role": "user", "content": incoming.text},
                        ],
                    )
                )
                content = _fmt.choices[0].message.content
                if not content:
                    raise ValueError("Empty or None response content")
                return content.strip()
            except Exception as _fe:
                return (
                    f"Here are the current offers, {incoming.sender_name}! 🎉\n\n"
                    + _offers_text + _tier_ctx
                    + "\n\nIf you'd like an extra discount, tell me how many units you need!"
                )
        else:
            return (
                f"I'll check the latest offers for you, {incoming.sender_name}! "
                f"Browse our products and I'll confirm the best available price."
            )

    if quick_parsed.get("is_comparison", False) or quick_parsed.get("is_recommendation", False):
        neg_state = None

    # ── Case 1: Comparison OR recommendation ───────────────────────────────
    if is_comparison or is_recommendation:
        # ── FIX: Resolve multi-product comparisons directly from incoming.text ──
        # ROOT CAUSE OF BUG: selected_product_name in the LLM parser output is
        # a SINGULAR field — it can only ever hold one product name. But the
        # NUMBER-SELECT resolver (above, earlier in this function) already
        # rewrote incoming.text from "compare 15 and 16" into the full
        # "compare Olly 5W Outdoor LED Wall Light and Stox 10W Outdoor LED
        # Wall Light" — containing BOTH real product names as plain text.
        #
        # The old code ignored that rewritten text entirely and only looked
        # at parsed["selected_product_name"] (always null for 2-product
        # comparisons), so it fell through to "Active context" / "Full
        # selection" fallbacks — which incorrectly grabbed whichever 3
        # products were last shown as IMAGES, not the ones the customer
        # actually asked to compare.
        #
        # Fix: scan incoming.text directly against every product name in
        # `selection` and collect ALL matches (not just one), in the order
        # they appear in the text. This correctly captures 2+ product
        # comparisons regardless of how the LLM parser filled its singular
        # selected_product_name field.
        compared = []
        text_lower = incoming.text.lower()

        # 1. Resolve compared products from the LLM parsed output (if populated)
        parsed_compared = parsed.get("compared_product_names")
        if isinstance(parsed_compared, list) and parsed_compared:
            for name in parsed_compared:
                name_lower = name.lower().strip()
                for p in selection:
                    pname = (p.get("product_name") or p.get("name") or "").lower().strip()
                    if name_lower == pname or name_lower in pname or pname in name_lower:
                        if p not in compared:
                            compared.append(p)
                            break

        # 2. Fallback: Scan text directly for exact product names
        if not compared:
            # Sort selection by name length (longest first) so we match more
            # specific names before any shorter substring could collide
            # (e.g. "Olly 5W Outdoor LED Wall Light" before a hypothetical "Olly").
            _sorted_selection = sorted(
                selection,
                key=lambda p: len((p.get("product_name") or p.get("name") or "")),
                reverse=True,
            )
            for p in _sorted_selection:
                pname = (p.get("product_name") or p.get("name") or "").lower().strip()
                if pname and pname in text_lower:
                    already_added = any(
                        (c.get("product_name") or c.get("name") or "").lower() == pname
                        for c in compared
                    )
                    if not already_added:
                        compared.append(p)

            # Preserve the order products appear in the text (e.g. "X and Y" → [X, Y])
            if len(compared) >= 2:
                compared.sort(key=lambda p: text_lower.find(
                    (p.get("product_name") or p.get("name") or "").lower()
                ))

        # ── Fallback: single-name match from LLM parser (old behavior) ─────
        # Only used if direct text scan above found nothing — covers cases
        # like "tell me more about Reva" where text scan should already work,
        # but kept as a safety net for edge cases (typos, partial names).
        if not compared:
            compared_names = []
            if parsed.get("selected_product_name"):
                compared_names.append(parsed["selected_product_name"])
            if compared_names:
                for name in compared_names:
                    name_lower = name.lower().strip()
                    for p in selection:
                        pname = (p.get("product_name") or p.get("name") or "").lower()
                        if name_lower in pname or pname in name_lower:
                            compared.append(p)
                            break

        # ── Level 2: Pronoun resolution ("compare THIS with X") ──────────
        # Detect pronoun via LLM, then inject last-discussed product.
        _has_pronoun = False
        if len(compared) <= 1:
            try:
                _last_prod = await get_last_discussed_product(incoming.tenant_id, incoming.session_id) or "the previous product"
                pronoun_resp = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: _client.chat.completions.create(
                        model       = AZURE_OPENAI_DEPLOYMENT,
                        max_tokens  = 5,
                        temperature = 0,
                        messages    = [
                            {"role": "system", "content": get_prompt(
                                incoming, "pf_vague_pronoun_resolver_l2_prompt",
                                product_name=_last_prod, customer_message=incoming.text
                            )},
                            {"role": "user", "content": incoming.text},
                        ],
                    )
                )
                content = pronoun_resp.choices[0].message.content
                _has_pronoun = content is not None and "YES" in content.strip().upper()
            except Exception:
                _has_pronoun = False

        if _has_pronoun and len(compared) <= 1:
            # ── Resolve "this" from session history first (most reliable) ─
            # The DB lookup (last_discussed_product) has a timing gap: the save
            # happens at the END of the previous pipeline run, but both messages
            # can arrive within the same second. Session history is set at the
            # START of this pipeline run so it's guaranteed to be current.
            # Use _get_active_product_context to scan bot's recent messages
            # (e.g. the Villa brief) and find which product "this" refers to.
            try:
                _context_for_pronoun = await _get_active_product_context(
                    incoming, selection, session_history
                )
                for _cp in _context_for_pronoun:
                    _cp_name = (_cp.get("product_name") or _cp.get("name") or "").lower()
                    already_in = any(
                        _cp_name[:12] in (p.get("product_name") or p.get("name") or "").lower()
                        for p in compared
                    )
                    if not already_in:
                        compared.insert(0, _cp)
                        print(f"[FOLLOW-UP] Pronoun 'this' resolved via session history: '{_cp.get('product_name') or _cp.get('name')}'")
                        break  # Only need the single most recently discussed product
            except Exception as e:
                print(f"[FOLLOW-UP] Pronoun history resolution failed: {e}")

            # ── Fallback: DB lookup if history resolution didn't find anything ─
            if len(compared) <= 1:
                try:
                    _last = await get_last_discussed_product(incoming.tenant_id, incoming.session_id)
                    if _last:
                        _last_lower = _last.lower().strip()
                        _last_p = None
                        for p in selection:
                            pname = (p.get("product_name") or p.get("name") or "").lower()
                            if _last_lower[:12] in pname or pname[:12] in _last_lower:
                                _last_p = p
                                break
                        if _last_p is None:
                            try:
                                _cached = await get_cached_product_by_name(incoming.tenant_id, _last)
                                _last_p = _cached if _cached else {"product_name": _last, "name": _last}
                            except Exception:
                                _last_p = {"product_name": _last, "name": _last}
                        already_in = any(
                            _last_lower[:12] in (p.get("product_name") or p.get("name") or "").lower()
                            for p in compared
                        )
                        if not already_in:
                            compared.insert(0, _last_p)
                            print(f"[FOLLOW-UP] Pronoun resolved via DB fallback: '{_last}'")
                except Exception as e:
                    print(f"[FOLLOW-UP] Pronoun DB fallback failed: {e}")

        # ── Level 3: Active context from session history ──────────────────
        # "suggest me one with low budget" after discussing Romy →
        # use recently-discussed products, not full 18-product list.
        if len(compared) < 2:
            context_products = await _get_active_product_context(
                incoming, selection, session_history
            )
            # Merge: add context products not already in compared
            for cp in context_products:
                cp_name = (cp.get("product_name") or cp.get("name") or "").lower()
                if not any(
                    cp_name[:12] in (p.get("product_name") or p.get("name") or "").lower()
                    for p in compared
                ):
                    compared.append(cp)

        # ── Level 4: Full selection fallback ─────────────────────────────
        # Only when nothing specific was discussed — e.g. customer just
        # received the category list and immediately asks "which is best?"
        if len(compared) < 2:
            compared = selection
            print(f"[FOLLOW-UP] No context found — using full selection ({len(compared)} products)")

        print(f"[FOLLOW-UP] Comparison set: {[c.get('product_name') or c.get('name') for c in compared[:5]]}")

        # Send images if requested
        if asks_for_image:
            for p in compared:
                pname = p.get("product_name") or p.get("name")
                if pname:
                    cached = await get_cached_product_by_name(incoming.tenant_id, pname)
                else:
                    cached = None
                img = (cached or p).get("image_url")
                if img:
                    price = float((cached or p).get("list_price") or (cached or p).get("price_num", 0) or 0)
                    caption = f"{(cached or p).get('product_name') or pname}\nRs.{price:,.0f}"
                    img_wamid = await send_image(incoming, img, caption)
                    if img_wamid:
                        await save_outbound_message(
                            tenant_id     = incoming.tenant_id,
                            session_id    = incoming.session_id,
                            message_id    = img_wamid,
                            text          = caption,
                            media_url     = img,
                            original_type = "image",
                    region        = incoming.region,
                        )

        incoming._graphrag_raw = json.dumps({
            "handler": "comparison" if is_comparison else "recommendation",
            "products": [p.get("product_name") or p.get("name") for p in compared[:5]],
        })
        return await _handle_comparison(
            incoming, compared, session_history,
            show_recommendation=is_recommendation,
        )

    # ── Case 2: Name match ──────────────────────────────────────────────────
    # Check if LLM parsed a specific product name first
    tgt_name_raw = parsed.get("selected_product_name")
    if not matched_product and tgt_name_raw:
        tgt_name = tgt_name_raw.lower().strip()
        for p in selection:
            pname = (p.get("product_name") or p.get("name") or "").lower()
            if tgt_name in pname or pname in tgt_name:
                matched_product = p
                print(f"[FOLLOW-UP] Name match via LLM parser: '{tgt_name}' -> {pname}")
                break

    # Fallback to word-score name matching
    if not matched_product:
        msg_lower = incoming.text.lower().strip()
        msg_words = set(re.findall(r'\b[a-z]+\b', msg_lower))
        best_score = 0
        
        # Skip common stopwords/prepositions/conjunctions to avoid matching on words like "with"
        _stopwords = {"with", "this", "that", "want", "order", "have", "your", "from", "here", "about", "need", "like"}
        
        for p in selection:
            pname  = (p.get("product_name") or p.get("name") or "").lower()
            pwords = set(re.findall(r'\b[a-z]+\b', pname))
            # Only count words >3 chars — skip stopwords, "led", "12w", "the", "and"
            score = sum(1 for w in pwords if len(w) > 3 and w in msg_words and w not in _stopwords)
            if score > best_score:
                best_score      = score
                matched_product = p

        if matched_product and best_score > 0:
            print(f"[FOLLOW-UP] Name match (score={best_score}): '{msg_lower}' -> {matched_product.get('product_name')}")
        else:
            matched_product = None

    # ── Deterministic bare-number resolution ───────────────────────────────────
    # A bare number means exactly ONE of three things, decided purely from the
    # bot's single most recent message — never guessed, never scanned across
    # multiple turns:
    #
    #   (a) Bot's last message was the freshly-shown product LIST itself
    #       → number is a 1-based LIST POSITION → map to that product by name,
    #         then ask "how many units?" (number is NEVER reused as quantity)
    #   (b) Bot's last message was an explicit quantity question
    #       → number is the QUANTITY for the product already in context
    #   (c) Anything else (order summary, product Q&A, installation reply, etc.)
    #       → ambiguous → ask the customer to reply with the product name
    if not matched_product:
        bare_number_only = re.fullmatch(r"\s*\d{1,4}\s*", incoming.text.strip()) is not None
        if bare_number_only and not parsed.get("selected_product_name"):

            last_bot_msg = ""
            if session_history:
                assistant_msgs = [m["content"] for m in session_history if m.get("role") == "assistant"]
                if assistant_msgs:
                    last_bot_msg = assistant_msgs[-1].lower()

            # Unique marker text that ONLY appears on a freshly-shown product list —
            # guarantees this number is the customer's first reply to THAT exact list.
            bot_just_showed_list = "reply with the product" in last_bot_msg and ("name" in last_bot_msg or "number" in last_bot_msg)

            bot_asked_quantity = (
                "how many units" in last_bot_msg
                or "how many would you like" in last_bot_msg
            )

            extracted_number = int(incoming.text.strip())

            if bot_just_showed_list:
                # (a) Map number -> product by 1-based position in the SAME list
                # that was just shown. This is deterministic: position N in the
                # list the bot displayed maps directly to selection[N-1].
                if 1 <= extracted_number <= len(selection):
                    matched_product = selection[extracted_number - 1]
                    print(f"[FOLLOW-UP] List-position pick: '{extracted_number}' -> {matched_product.get('product_name') or matched_product.get('name')} (list size={len(selection)})")
                    # Force quantity to remain unset — never reuse this number as quantity.
                    parsed["parsed_order_quantity"] = None
                    parsed["_number_was_list_position"] = True  # threaded downstream to suppress quantity inference
                else:
                    print(f"[FOLLOW-UP] '{extracted_number}' out of range for list size={len(selection)} — asking for product name")
                    return (
                        f"Hi {incoming.sender_name}! That number isn't in the list (1-{len(selection)}). "
                        f"Could you please reply with the *product name* instead? 😊"
                    )

            elif bot_asked_quantity:
                # (b) Legitimate quantity context — let existing downstream logic
                # (Case 3 / quantity injection) handle it normally.
                print(f"[FOLLOW-UP] Bot asked quantity — '{extracted_number}' treated as QUANTITY, falling through")

            else:
                # (c) Ambiguous — bot's last message was neither a list nor a
                # quantity question. Do not guess; ask for the product name.
                print(f"[FOLLOW-UP] Bare number '{extracted_number}' with no list/quantity context — asking for product name instead of guessing")
                return (
                    f"Hi {incoming.sender_name}! Could you please reply with the *product name* "
                    f"you'd like to know more about or order? 😊"
                )

    # ── New-search guard before Case 3 ──────────────────────────────────────
    # PERFORMANCE: removed a redundant second LLM call here. _parse_followup_message
    # (called above at the top of this function) already classifies is_new_search
    # using the same product list context. If it said False, we trust that result
    # instead of re-asking the same NEW_SEARCH/FOLLOW_UP question a second time —
    # this was adding a full extra sequential round-trip to every follow-up.
    if not matched_product and False:  # disabled: redundant with parsed["is_new_search"] above
        product_names_in_selection = [
            p.get("product_name", p.get("name", "")) for p in selection
            if p.get("product_name") or p.get("name")
        ]
        try:
            guard_response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _client.chat.completions.create(
                    model       = AZURE_OPENAI_DEPLOYMENT,
                    max_tokens  = 5,
                    temperature = 0,
                    messages    = [
                        {"role": "system", "content": get_prompt(incoming, "pf_new_search_followup_classifier_prompt")},
                        {"role": "user", "content": incoming.text},
                    ],
                )
            )
            content = guard_response.choices[0].message.content
            if not content:
                raise ValueError("Empty or None response content")
            classification = content.strip().upper()
            if "NEW_SEARCH" in classification:
                print(f"[FOLLOW-UP] LLM guard: NEW_SEARCH — routing to GraphRAG")
                return None
            print(f"[FOLLOW-UP] LLM guard: FOLLOW_UP — continuing to Case 3")
        except Exception as e:
            print(f"[FOLLOW-UP] LLM guard failed ({e}) — defaulting to FOLLOW_UP")

    # ── Case 3: last_discussed_product DB fallback (Prioritized State) ────────
    if not matched_product:
        try:
            _ld = await get_last_discussed_product(incoming.tenant_id, incoming.session_id)
            if _ld:
                for p in selection:
                    pname = (p.get("product_name") or p.get("name") or "").lower()
                    if _ld.lower()[:12] in pname or pname[:12] in _ld.lower():
                        matched_product = p
                        break
                if not matched_product:
                    _ldc = await get_cached_product_by_name(incoming.tenant_id, _ld)
                    if _ldc:
                        matched_product = {"product_name": _ld, "name": _ld}
                if matched_product:
                    print(f"[FOLLOW-UP] Case 3 DB fallback (prioritized): last_discussed='{_ld}'")
        except Exception as _lde:
            print(f"[FOLLOW-UP] Case 3 fallback failed: {_lde}")

    # ── Case 4: Pure follow-up — scan bot history (Heuristic Fallback) ────────
    if not matched_product and session_history:
        recent_bot_msgs = [
            m["content"] for m in session_history[-6:]
            if m.get("role") == "assistant"
        ]
        combined_bot_text = " ".join(recent_bot_msgs).lower()
        for p in selection:
            pname      = (p.get("product_name") or p.get("name") or "").lower()
            first_word = pname.split()[0] if pname else ""
            if first_word and len(first_word) > 3 and first_word in combined_bot_text:
                matched_product = p
                print(f"[FOLLOW-UP] Case 4 Bot history match: '{first_word}' -> {pname}")
                break

    if not matched_product:
        return None

    product_name = matched_product.get("product_name") or matched_product.get("name")
    if not product_name:
        return None

    # Dynamically append selected options (wattages, size, color) to product name
    selected_options = parsed.get("selected_options")
    if selected_options:
        if "(" not in product_name:
            product_name = f"{product_name} ({selected_options})"
    
    # Save as the last discussed product in the database so context is retained
    try:
        await save_last_discussed_product(incoming.tenant_id, incoming.session_id, product_name)
    except Exception as e:
        print(f"[FOLLOW-UP] Failed to save last discussed product: {e}")

    cached_product = await get_cached_product_by_name(incoming.tenant_id, product_name)

    if not cached_product:
        # product_cache miss — but matched_product already has feature_descriptions,
        # image_url, etc. saved in workflow_sessions items_json (via save_graphrag_product_selection).
        # Use that directly instead of falling through to GraphRAG which would
        # re-fetch and potentially return stale context from a different session.
        if matched_product.get("feature_descriptions"):
            print(f"[FOLLOW-UP] product_cache miss for '{product_name}' — using data from PRODUCT_SELECTION items")
            cached_product = matched_product
        else:
            print(f"[FOLLOW-UP] product_cache miss for '{product_name}' — falling through to GraphRAG")
            return None

    # Pylance type narrowing: at this point cached_product is guaranteed non-None
    # (either fetched from product_cache or assigned from matched_product above).
    assert cached_product is not None

    # ── Send image only if explicitly requested ───────────────────────────
    if asks_for_image:
        # Use LLM to decide: is this an installation/steps request or a product image request?
        # Zero hardcoding — LLM reads the actual message and decides.
        try:
            img_intent_resp = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _client.chat.completions.create(
                    model       = AZURE_OPENAI_DEPLOYMENT,
                    max_tokens  = 5,
                    temperature = 0,
                    messages    = [
                        {"role": "system", "content": get_prompt(incoming, "pf_image_installation_intent_prompt")},
                        {"role": "user", "content": incoming.text},
                    ],
                )
            )
            content = img_intent_resp.choices[0].message.content
            img_intent = content.strip().upper() if content else "PRODUCT_IMAGE"
        except Exception:
            img_intent = "PRODUCT_IMAGE"
        print(f"[FOLLOW-UP] Image intent: {img_intent}")

        # Check for structured installation assets
        installation = cached_product.get("installation")
        if isinstance(installation, str):
            try:
                installation = json.loads(installation)
            except Exception:
                installation = None
        if not isinstance(installation, dict):
            # Fallback for backward compatibility
            inst_url = (cached_product.get("installation_url") or matched_product.get("installation_url") or "").replace("http://", "https://")
            if inst_url:
                installation = {
                    "pdf_url": inst_url,
                    "has_installation": True
                }
            else:
                installation = {}

        # Sanitize installation URL fields to ensure they are string values (and not boolean True/False or other types)
        if isinstance(installation, dict):
            for k in ["pdf_url", "manual_url", "video_url"]:
                v = installation.get(k)
                if v is not None:
                    if isinstance(v, bool):
                        installation[k] = ""
                    elif not isinstance(v, str):
                        installation[k] = str(v)

        img_url  = (cached_product.get("image_url") or matched_product.get("image_url") or "").replace("http://", "https://")

        if "INSTALLATION" in img_intent and (installation.get("pdf_url") or installation.get("manual_url") or installation.get("video_url")):
            media_url = installation.get("pdf_url") or installation.get("manual_url") or installation.get("video_url") or ""
            if isinstance(media_url, bool):
                media_url = ""
            elif not isinstance(media_url, str):
                media_url = str(media_url)

            # Send image card if it is an image format, or fallback to sending the links text directly
            if media_url and (media_url.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')) or "cdn" in media_url.lower()):
                caption = f"Installation guide — {cached_product.get('product_name') or product_name}"
                inst_wamid = await send_image(incoming, media_url, caption)
                if inst_wamid:
                    print(f"[FOLLOW-UP] Installation image sent for '{product_name}' — wamid={inst_wamid}")
                    await save_outbound_message(
                        tenant_id     = incoming.tenant_id,
                        session_id    = incoming.session_id,
                        message_id    = inst_wamid,
                        text          = caption,
                        media_url     = media_url,
                        original_type = "image",
                        region        = incoming.region,
                    )

            # Generate dynamic text description of assets
            try:
                header = get_prompt(
                    incoming, "followup_installation_header_prompt",
                    product_name = cached_product.get('product_name') or product_name
                )
            except RuntimeError:
                header = f"Here is the installation guide for *{cached_product.get('product_name') or product_name}*:\n"
            text_parts = [header]

            if installation.get("pdf_url"):
                try:
                    pdf_line = get_prompt(incoming, "followup_installation_pdf_prompt", pdf_url=installation['pdf_url'])
                except RuntimeError:
                    pdf_line = f"📄 PDF Guide: {installation['pdf_url']}"
                text_parts.append(pdf_line)

            if installation.get("video_url"):
                try:
                    video_line = get_prompt(incoming, "followup_installation_video_prompt", video_url=installation['video_url'])
                except RuntimeError:
                    video_line = f"▶ Video Tutorial: {installation['video_url']}"
                text_parts.append(video_line)

            if installation.get("manual_url"):
                try:
                    manual_line = get_prompt(incoming, "followup_installation_manual_prompt", manual_url=installation['manual_url'])
                except RuntimeError:
                    manual_line = f"🔗 Manual Link: {installation['manual_url']}"
                text_parts.append(manual_line)

            quick_steps = installation.get("quick_steps")
            if quick_steps:
                try:
                    steps_header = get_prompt(incoming, "followup_installation_steps_header_prompt")
                except RuntimeError:
                    steps_header = "\n*Quick Steps:*"
                text_parts.append(steps_header)

                if isinstance(quick_steps, list):
                    for idx, step in enumerate(quick_steps, 1):
                        text_parts.append(f"{idx}. {step}")
                else:
                    text_parts.append(str(quick_steps))

            try:
                footer = get_prompt(incoming, "followup_installation_footer_prompt")
            except RuntimeError:
                footer = (
                    "\nNeed help with anything else?\n"
                    "• 💰 Pricing & offers\n"
                    "• 📦 Place an order\n"
                    "• 🔒 Warranty\n\n"
                    "Or just tell me how many units you'd like and I'll set it up for you!"
                )
            text_parts.append(footer)

            link_text = "\n".join(text_parts)
            link_wamid = await send_reply(incoming, link_text)
            if link_wamid:
                await save_outbound_message(
                    tenant_id  = incoming.tenant_id,
                    session_id = incoming.session_id,
                    message_id = link_wamid,
                    text       = link_text,
                    region        = incoming.region,
                )
            return "__ALREADY_HANDLED__"  # Sentinel: image+link already sent, skip GraphRAG + LLM reply

        elif img_url:
            # Product image only
            price   = float(cached_product.get("list_price") or matched_product.get("list_price", 0) or 0)
            caption = f"{cached_product.get('product_name') or product_name}\nRs.{price:,.0f}"
            img_wamid = await send_image(incoming, img_url, caption)
            if img_wamid:
                print(f"[FOLLOW-UP] Product image sent for '{product_name}' — wamid={img_wamid}")
                await save_outbound_message(
                    tenant_id     = incoming.tenant_id,
                    session_id    = incoming.session_id,
                    message_id    = img_wamid,
                    text          = caption,
                    media_url     = img_url,
                    original_type = "image",
                    region        = incoming.region,
                )

    # Start with a copy of all cached product fields so no custom fields (e.g. wattages) are lost
    product_context: dict[str, Any] = cached_product.copy()

    # Overlay only the formatted, casted, or external helper fields that the LLM/negotiator expect
    product_context.update({
        "name":                       cached_product.get("product_name"),
        "price":                      f"Rs.{float(cached_product.get('list_price') or 0):,.0f}",
        "list_price":                 float(cached_product.get("list_price") or 0),
        "discount_pct":               cached_product.get("discount_pct", 0),
        "list_price_num":             float(cached_product.get("list_price") or 0),
        "regular_price":              f"Rs.{float(cached_product.get('regular_price') or 0):,.0f}",
        "discount":                   f"{cached_product.get('discount_pct', 0)}% off",
        "gst_rate":                   getattr(incoming, "gst_rate", 0.18),
        "gst_rate_pct":               int(getattr(incoming, "gst_rate", 0.18) * 100),
        "delivery_policy":            [
            pol if isinstance(pol, str) else pol.get("content", "")
            for pol in cached_product.get("policies", [])
        ] if "policies" in cached_product else [],
        "faqs": [
            _format_faq_entry(f) for f in cached_product.get("faqs", [])
        ] if "faqs" in cached_product else [],
    })

    # Inject parsed quantity if present
    number_was_list_position = parsed.get("_number_was_list_position", False)
    if number_was_list_position:
        parsed_qty = None
        product_context["customer_just_selected_by_number"] = True
        print(f"[FOLLOW-UP] Number was used for list-position selection — suppressing quantity inference for this turn")
    else:
        parsed_qty = parsed.get("parsed_order_quantity")
    parsed_unit = parsed.get("quantity_unit") or "units"

    if parsed_qty is not None:
        product_context["parsed_order_quantity"] = parsed_qty
        product_context["parsed_order_unit"]     = parsed_unit

        # ── Auto-apply global offer tier to order ─────────────────────────────
        # If order value qualifies for a tier, apply it automatically and show it
        # in the order summary. The customer does NOT need to negotiate for this.
        # If they want MORE than the auto-applied tier → 5% negotiation path.
        #
        # Tenant-configurable gate: require_offer_disclosure (default False,
        # preserving this exact behavior for existing tenants). When True,
        # store discounts only apply after the customer has explicitly asked
        # about offers (see the is_offer_inquiry branch above, which persists
        # offer_disclosed=True) — a tenant may not want customers to
        # unknowingly receive a discount they never asked about.
        _require_disclosure = bool(getattr(incoming, "require_offer_disclosure", False))
        _cached_neg_for_gate = getattr(incoming, "_cached_neg_state", None)
        _offer_disclosed = bool(_cached_neg_for_gate and _cached_neg_for_gate.get("offer_disclosed"))
        if _require_disclosure and not _offer_disclosed:
            print(f"[OFFER] Auto-apply skipped — require_offer_disclosure=True and offer not yet disclosed")
        else:
            try:
                from ai.negotiator import parse_global_offer_tiers as _pt, get_applicable_tier as _gat, get_next_tier as _gnt
                _price  = cast(float, product_context.get("list_price") or 0.0)
                _go_str = cast(str, product_context.get("global_offers") or "")
                if not _go_str:
                    # Fallback: tenant_offers table
                    _to2 = await get_tenant_offers(incoming.tenant_id)
                    _go_str = cast(str, _to2.get("offers_text", "") if _to2 else "")
                if _price > 0 and _go_str:
                    _tiers      = _pt(incoming, _go_str)
                    _order_val  = _price * int(parsed_qty)
                    _, _disc    = _gat(_order_val, _tiers)
                    _next_t     = _gnt(_order_val, _tiers)
                    if _disc > 0:
                        _disc_price = round(_price * (1 - _disc / 100), 2)
                        _disc_total = round(_disc_price * int(parsed_qty), 2)
                        product_context["auto_offer_applied"]    = True
                        product_context["auto_offer_disc_pct"]   = _disc
                        product_context["auto_offer_unit_price"] = _disc_price
                        product_context["auto_offer_total"]      = _disc_total
                        if _next_t:
                            # FIX Bug 1: gap must use _disc_total (the DISPLAYED subtotal)
                            # as the base, not _order_val (which is price_num × qty, the
                            # un-discounted value). _ov_check was undefined — using the
                            # wrong value caused gap to be off by (price - disc_price) × qty.
                            _gap2next = round(_next_t[0] - _disc_total, 0)
                            _u2next   = max(1, int(_gap2next / _disc_price) + 1)
                            product_context["auto_offer_upsell"] = (
                                f"💡 Add Rs.{_gap2next:,.0f} more to your order value "
                                f"(approx. {_u2next} more unit(s)) to reach Rs.{_next_t[0]:,} "
                                f"and unlock *{_next_t[1]}% off*!"
                            )
                        elif _disc > 0:
                            # Customer already at max tier — celebrate it
                            _max_disc2 = max(d for _, d in _tiers)
                            product_context["auto_offer_upsell"] = (
                                f"🎉 You've unlocked our *maximum store discount of {_max_disc2}% OFF*!"
                            )
                        print(f"[OFFER] Auto-applied {_disc}% to {product_name} x {parsed_qty}")
            except Exception as _aoe:
                print(f"[OFFER] Auto-apply failed: {_aoe}")

    recent_history = session_history[-6:] if session_history else []

    try:
        _t_final_start = time.monotonic()
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model       = AZURE_OPENAI_DEPLOYMENT,
                max_tokens  = 400,
                temperature = 0.3,
                messages    = [
                    {"role": "system", "content": get_prompt(
                        incoming, "pf_main_followup_prompt",
                        catalog_data=json.dumps(product_context, ensure_ascii=False),
                        product_name=product_name,
                        sender_name=incoming.sender_name,
                        biz_name=incoming.biz_name,
                        # These three are the Mem0/ContextBuilder-sourced fields
                        # from the prompt's original design — that wiring isn't
                        # live in this pipeline yet (see ai/orchestrator.py's
                        # docstring), so they're empty rather than invented.
                        # Once ContextBuilder is wired in, replace these with
                        # arc.llm_context.product_context / .customer_preferences
                        # / .workflow_context.
                        product_context="",
                        customer_preferences="",
                        workflow_context="",
                        # Reuses the already-computed parse result — no new
                        # classification logic, just surfacing what's already
                        # known about this turn's intent to the prompt.
                        parsed_intent=json.dumps(parsed, ensure_ascii=False),
                    )},
                    *recent_history,
                    {"role": "user", "content": incoming.text},
                ],
            )
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Empty or None response content")
        reply = content.strip()
        print(f"[TIMING] Final answer LLM call: {time.monotonic() - _t_final_start:.2f}s")
        print(f"[FOLLOW-UP] LLM answered for product '{product_name}'")

        incoming._graphrag_raw = json.dumps({
            "handler": "product_followup",
            "product": product_name,
            "quantity": parsed_qty,
        })

        # Save pending order to DB if quantity is specified.
        # CRITICAL: only save _fresh_neg when there is NO active negotiation state
        # with a quantity. If neg_state exists (customer is mid-order, e.g. "add 2
        # more units"), _parse_followup_message extracts the raw number (2) and
        # _fresh_neg would overwrite quantity=1 with quantity=2 — then
        # detect_quantity_change sees current=2 and "add 2" → returns 4 instead of 3.
        # When neg_state exists, handle_negotiation owns the quantity via
        # detect_quantity_change. We must not interfere here.
        _active_neg = await get_negotiation_state(incoming.tenant_id, incoming.session_id)
        _has_active_qty = _active_neg and _active_neg.get("quantity")
        if parsed_qty is not None and not _has_active_qty:
            try:
                from db.session_store import save_pending_order
                await save_pending_order(
                    tenant_id      = incoming.tenant_id,
                    session_id     = incoming.session_id,
                    product_name   = product_name,
                    quantity_value = int(parsed_qty),
                    quantity_unit  = parsed_unit,
                )
                print(f"[ORDER] Saved pending order to DB: {product_name} x {parsed_qty}")
                # Clear stale state then save fresh state. Only runs for NEW orders
                # (no existing neg_state quantity) — never for quantity updates.
                _prior_offer_disclosed = bool(_active_neg and _active_neg.get("offer_disclosed"))
                await clear_negotiation_state(incoming.tenant_id, incoming.session_id)
                _list_price  = cast(float, product_context.get("list_price") or 0.0)
                _auto_offer_unit_price = product_context.get("auto_offer_unit_price")
                _auto_applied = bool(product_context.get("auto_offer_applied") and _auto_offer_unit_price is not None)
                _actual_price = cast(float, _auto_offer_unit_price) if _auto_applied else _list_price
                _fresh_neg = {
                    "product_name":      product_name,
                    "price_num":         _list_price,
                    "quantity":          int(parsed_qty),
                    "rounds":            0,
                    "awaiting_quantity": False,
                    # Carries forward disclosure state from before this order
                    # was created — asking about offers, then ordering, should
                    # not reset disclosure just because state got recreated.
                    "offer_disclosed":   _prior_offer_disclosed,
                    # Reuses the same "pending order awaiting confirmation" state
                    # that negotiated orders already use — _invoice_guard() and
                    # _confirm_negotiated_order() (router.py) work unchanged
                    # for this case; nothing negotiation-specific about them,
                    # they just need these two fields set.
                    "awaiting_invoice_confirmation": True,
                    "last_offer_price":  _actual_price,
                }
                if _auto_applied:
                    _fresh_neg["auto_offer_unit_price"] = cast(float, _auto_offer_unit_price)
                    _fresh_neg["auto_offer_disc_pct"]   = product_context.get("auto_offer_disc_pct", 0)
                await save_negotiation_state(incoming.tenant_id, incoming.session_id, _fresh_neg)
                print(f"[OFFER] Fresh neg_state qty={parsed_qty} auto={_fresh_neg.get('auto_offer_unit_price')}")
            except Exception as e:
                print(f"[ORDER] Failed to save pending order: {e}")
        elif parsed_qty is not None and _active_neg is not None and _active_neg.get("quantity"):
            print(f"[OFFER] Skipping _fresh_neg save — active neg_state qty={_active_neg.get('quantity')} exists. handle_negotiation owns quantity updates.")

        return reply

    except Exception as e:
        print(f"[FOLLOW-UP] LLM failed: {e} — falling through to GraphRAG")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# INTENT HANDLERS
# ══════════════════════════════════════════════════════════════════════════════