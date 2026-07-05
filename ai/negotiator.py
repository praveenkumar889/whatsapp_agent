# ai/negotiator.py — Price Negotiator (fully dynamic prompts)
#
# RULE: No prompt string in this file.
#       All prompts fetched via get_prompt(incoming, key, **vars).
#       Raises RuntimeError if any prompt missing in DB.

import asyncio
import json
import re
from typing import List, Optional, cast

from openai import AzureOpenAI
from openai.types.chat import ChatCompletionMessageParam
from config import AZURE_AI_ENDPOINT, AZURE_AI_API_KEY, AZURE_OPENAI_DEPLOYMENT, AZURE_AI_API_VERSION
from db.prompt_store import get_prompt

_client = AzureOpenAI(
    azure_endpoint=AZURE_AI_ENDPOINT, api_key=AZURE_AI_API_KEY,
    api_version=AZURE_AI_API_VERSION, timeout=30.0, max_retries=0,
)

MAX_NEGOTIATION_ROUNDS = 4
DEFAULT_FLOOR_DISC_PCT = 5


# ── Tier helpers (no prompts — pure math) ─────────────────────────────────────

def parse_global_offer_tiers(incoming, global_offers: str) -> list:
    if not global_offers or not global_offers.strip():
        return []
    try:
        from db.prompt_store import get_prompt
        _tiers_prompt = get_prompt(incoming, "parse_global_offer_tiers_prompt")
        response = _client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT, max_tokens=150, temperature=0,
            messages=[
                {"role": "system", "content": _tiers_prompt},
                {"role": "user", "content": global_offers},
            ],
        )
        content = response.choices[0].message.content
        raw    = content.strip() if content else ""
        parsed = json.loads(raw)
        if isinstance(parsed, list) and all(len(t) == 2 for t in parsed):
            return sorted(parsed, key=lambda x: x[0])
        return []
    except Exception as e:
        print(f"[NEGOTIATOR] parse_global_offer_tiers failed: {e}")
        return []


def get_negotiation_floor_disc(tiers: list) -> int:
    if len(tiers) >= 2: return tiers[1][1]
    if len(tiers) == 1: return tiers[0][1]
    return DEFAULT_FLOOR_DISC_PCT

def get_applicable_tier(order_value: float, tiers: list) -> tuple:
    applicable = (0, 0)
    for min_val, disc_pct in tiers:
        if order_value >= min_val: applicable = (min_val, disc_pct)
        else: break
    return applicable

def get_next_tier(order_value: float, tiers: list) -> Optional[tuple]:
    """
    Returns the next higher discount tier the customer hasn't reached yet.
    Used to show "spend Rs.X more to unlock Y% off" upsell messages.
    Returns None if no tiers exist or customer is already at the max tier.
    """
    for min_val, disc_pct in tiers:
        if order_value < min_val:
            return (min_val, disc_pct)
    return None

def calculate_offer(price_num: float, quantity: int, tiers: Optional[list] = None) -> dict:
    """
    Computes the negotiation offer price for a given quantity.

    FIX — tier-aware pricing:
        Previously this used a FIXED floor_disc = get_negotiation_floor_disc(tiers),
        which always returns tiers[1][1] (the SECOND tier's discount) regardless
        of the actual order value. This meant a customer at 19 units (well past
        the 3rd tier threshold of Rs.14,500 / 8% off) was still priced at the
        2nd tier discount (5%) — the price never advanced past that fixed point
        no matter how much quantity they added.

        Fix: the discount now ALWAYS reflects get_applicable_tier(order_value, tiers)
        — the highest tier the customer's CURRENT order value actually qualifies
        for. The negotiation "floor" (deepest possible discount, used as the
        absolute ceiling the negotiator will concede to) is now separately
        derived as the MAXIMUM tier discount available, not a fixed positional one.
    """
    tiers       = tiers or []
    # Use price_num * quantity to determine WHICH tier applies (tier thresholds
    # are based on the list price order value, not the discounted value).
    order_value_for_tier = price_num * quantity

    # The discount the customer's CURRENT order value actually qualifies for —
    # recalculated fresh every time quantity changes, not fixed at tier[1].
    _, current_tier_disc = get_applicable_tier(order_value_for_tier, tiers) if tiers else (0, 0)

    # The negotiation floor (deepest discount the bot will ever concede to)
    # is the highest tier available in the offers — this is the true ceiling,
    # not an arbitrary fixed position in the tier list.
    max_disc = max((d for _, d in tiers), default=0) if tiers else 0

    # Auto-applied tier discount is the customer's right NOW based on order value.
    tier_price = round(price_num * (1 - current_tier_disc / 100), 2)

    # Negotiation gives a small additional concession on top of the tier price.
    floor_price = round(price_num * (1 - max_disc / 100), 2)
    gap         = tier_price - floor_price
    offer_price = round(tier_price - gap / 3, 2) if gap > 0 else tier_price

    # FIX Bug 1: order_value used for the UPSELL THRESHOLD GAP CALCULATION must
    # match the subtotal the customer sees (offer_price * qty), not price_num * qty.
    # Previously: gap = 14500 - (price_num * qty) = 14500 - 8348 = 6152
    # Correct:    gap = 14500 - (offer_price * qty) = 14500 - 7847 = 6653
    # The customer sees "Subtotal: Rs.7,847" so the gap displayed must be
    # based on that same subtotal — otherwise the numbers are inconsistent.
    order_value = round(offer_price * quantity, 2)

    return {
        "offer_price": offer_price, "total_price": round(offer_price * quantity, 2),
        "floor_price": floor_price, "floor_disc": max_disc,
        "tier_discount_pct": current_tier_disc, "has_discount": current_tier_disc > 0 or max_disc > 0,
        "price_num": price_num, "quantity": quantity, "order_value": order_value,
        "tiers": tiers, "current_tier_disc": current_tier_disc, "max_tier_disc": max_disc,
        "order_value_for_tier": order_value_for_tier,
    }


# ── Detection helpers — all prompts from DB ───────────────────────────────────

# Regex fast-path for is_negotiation_request(). Only ever short-circuits to
# True (skip the LLM call entirely) — never to False. Anything not matched
# here still goes through the LLM exactly as before, so this can only reduce
# latency, never change behavior on ambiguous messages.
_NEG_REQUEST_FAST_PATTERNS = [
    r'\bcan (i|we) get (it |this )?for\b',
    r'\b(how about|what about)\s+\d',
    r'\b(do|make) it (for )?\d',
    r'\bwill you (take|accept)\b',
    r'\bmy (budget|price|offer) is\b',
    r'\bfinal (budget|price|offer)\b',
    r'\brs\.?\s*\d{2,}\b',
    r'\b\d{2,}\s*(rs|rupees?|/-)?\s*(per unit|each)?\s*$',
    r'\b(discount|reduce|lower|cheaper|less price|best price|lowest price|negotiate|negotiable|any offer|special price)\b',
]
_NEG_REQUEST_FAST_RE = re.compile("|".join(_NEG_REQUEST_FAST_PATTERNS), re.IGNORECASE)


async def is_negotiation_request(message: str, incoming, session_history: Optional[list] = None) -> bool:
    # Fast path: obvious negotiation language — skip the ~2s LLM round-trip.
    if _NEG_REQUEST_FAST_RE.search(message or ""):
        print(f"[NEGOTIATOR] is_negotiation_request: regex fast-path matched — skipping LLM")
        return True

    prompt = get_prompt(incoming, "neg_is_request_prompt")
    try:
        messages: List[ChatCompletionMessageParam] = [{"role": "system", "content": prompt}]
        if session_history:
            messages.extend(cast(List[ChatCompletionMessageParam], session_history[-4:]))
        messages.append({"role": "user", "content": message})
        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=5, temperature=0, messages=messages,
            )
        )
        content = r.choices[0].message.content
        if not content:
            return False
        return "YES" in content.strip().upper()
    except RuntimeError: raise
    except Exception as e:
        print(f"[NEGOTIATOR] is_negotiation_request failed: {e}")
        return False


async def extract_quantity(message: str, product_name: str, incoming, session_history: Optional[list] = None) -> Optional[int]:
    prompt = get_prompt(incoming, "neg_extract_qty_prompt", product_name=product_name)
    try:
        messages: List[ChatCompletionMessageParam] = [{"role": "system", "content": prompt}]
        if session_history:
            messages.extend(cast(List[ChatCompletionMessageParam], session_history[-4:]))
        messages.append({"role": "user", "content": message})
        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=10, temperature=0, messages=messages,
            )
        )
        content = r.choices[0].message.content
        if not content:
            return None
        raw = content.strip().upper()
        if raw == "NONE": return None
        clean = raw.replace(",","").strip()
        return int(clean) if clean.isdigit() else None
    except RuntimeError: raise
    except Exception as e:
        print(f"[NEGOTIATOR] extract_quantity failed: {e}")
        return None


async def detect_quantity_change(message: str, current_qty: int, product_name: str, incoming) -> Optional[int]:
    prompt = get_prompt(
        incoming, "neg_detect_qty_change_prompt",
        current_qty=current_qty, product_name=product_name,
    )
    try:
        messages: List[ChatCompletionMessageParam] = [
            {"role": "system", "content": prompt}, {"role": "user", "content": message},
        ]
        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=10, temperature=0,
                messages=messages,
            )
        )
        content = r.choices[0].message.content
        if not content:
            return None
        raw = content.strip().upper()
        if raw == "NONE": return None
        clean = raw.replace(",","").strip()
        result = int(clean) if clean.isdigit() else None
        if result and result != current_qty:
            print(f"[NEGOTIATOR] Qty change: {current_qty}→{result}")
        return result
    except RuntimeError: raise
    except Exception as e:
        print(f"[NEGOTIATOR] detect_quantity_change failed: {e}")
        return None


async def detect_counter_offer(
    message: str, incoming, current_price_num: Optional[float] = None,
    quantity: Optional[int] = None, session_history: Optional[list] = None,
) -> Optional[float]:
    current_total = round(current_price_num * quantity, 2) if current_price_num and quantity else 0
    prompt = get_prompt(
        incoming, "neg_detect_counter_prompt",
        current_price=f"{current_price_num:,.0f}" if current_price_num else "N/A",
        quantity=quantity or "N/A",
        current_total=f"{current_total:,.0f}",
    )
    try:
        messages: List[ChatCompletionMessageParam] = [{"role": "system", "content": prompt}]
        if session_history:
            messages.extend(cast(List[ChatCompletionMessageParam], session_history[-4:]))
        messages.append({"role": "user", "content": message})
        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=20, temperature=0, messages=messages,
            )
        )
        content = r.choices[0].message.content
        if not content:
            return None
        raw = content.strip().upper()
        if raw == "NONE" or not raw: return None
        if raw.startswith("UNIT:"):
            val = raw[5:].replace("RS.","").replace("₹","").replace(",","").strip()
            return float(val) if val.replace(".","").isdigit() else None
        if raw.startswith("TOTAL:"):
            val = raw[6:].replace("RS.","").replace("₹","").replace(",","").strip()
            total = float(val) if val.replace(".","").isdigit() else None
            if total and quantity and quantity > 0: return round(total / quantity, 2)
            return total
        return None
    except RuntimeError: raise
    except Exception as e:
        print(f"[NEGOTIATOR] detect_counter_offer failed: {e}")
        return None


async def detect_more_discount_request(message: str, incoming, session_history: Optional[list] = None) -> bool:
    prompt = get_prompt(incoming, "neg_more_discount_prompt")
    try:
        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=5, temperature=0,
                messages=[{"role": "system", "content": prompt}, {"role": "user", "content": message}],
            )
        )
        content = r.choices[0].message.content
        return "YES" in content.strip().upper() if content else False
    except RuntimeError: raise
    except Exception as e:
        print(f"[NEGOTIATOR] detect_more_discount_request failed: {e}")
        return False


async def detect_acceptance(message: str, incoming, session_history: Optional[list] = None) -> bool:
    prompt = get_prompt(incoming, "neg_detect_accept_prompt", message=message)
    try:
        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=5, temperature=0,
                messages=[{"role": "system", "content": prompt}, {"role": "user", "content": message}],
            )
        )
        content = r.choices[0].message.content
        return "YES" in content.strip().upper() if content else False
    except RuntimeError: raise
    except Exception as e:
        print(f"[NEGOTIATOR] detect_acceptance failed: {e}")
        return False


# ── Reply generators — all prompts from DB ────────────────────────────────────

async def _reply_no_discount(incoming, product_name, price_num, regular_price, discount_pct, quantity, min_units=5) -> str:
    prompt = get_prompt(
        incoming, "neg_no_discount_prompt",
        sender_name=incoming.sender_name, biz_name=incoming.biz_name,
        product_name=product_name, quantity=quantity,
        price_num=f"{price_num:,.0f}", regular_price=f"{regular_price:,.0f}",
        discount_pct=discount_pct, min_units=min_units,
    )
    try:
        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=150, temperature=0.4,
                messages=[{"role": "system", "content": prompt},
                           {"role": "user", "content": "Give the no-discount response."}],
            )
        )
        content = r.choices[0].message.content
        if not content:
            raise ValueError("Empty response from AI")
        return content.strip()
    except RuntimeError: raise
    except Exception as e:
        print(f"[NEGOTIATOR] _reply_no_discount failed: {e}")
        total = round(price_num * quantity, 2)
        return f"{incoming.sender_name}, price is *Rs.{price_num:,.0f}*/unit (Total: *Rs.{total:,.0f}*). Buy {min_units}+ units for extra discounts!"


async def build_product_summary(incoming, product_data: Optional[dict]) -> str:
    """
    Builds a short product summary + recommendation block using the
    product_summary_recommendation_prompt from DB.

    Called after quantity changes (e.g. "add 3 more units") so the customer
    sees more than just a bare price update — a short reminder of why this
    product is a good fit, using only real cached product data (rating,
    review_count, warranty, feature_descriptions). Never invents details.

    Returns "" (empty string) if product_data is missing or the prompt fails —
    callers should treat this as optional and append it only if non-empty.
    """
    if not product_data:
        return ""

    rating        = product_data.get("rating")
    review_count  = product_data.get("review_count")
    warranty      = product_data.get("warranty")
    features      = product_data.get("feature_descriptions")

    # Nothing useful to summarize — skip silently rather than show an empty block
    if not any([rating, review_count, warranty, features]):
        return ""

    product_data_block = (
        f'rating: {rating if rating else "not available"}\n'
        f'review_count: {review_count if review_count else "not available"}\n'
        f'warranty: {warranty if warranty else "not available"}\n'
        f'feature_descriptions: {features if features else "not available"}'
    )

    try:
        prompt = get_prompt(
            incoming, "product_summary_recommendation_prompt",
            sender_name=incoming.sender_name, biz_name=incoming.biz_name,
            product_data=product_data_block,
        )
    except RuntimeError as e:
        print(f"[NEGOTIATOR] build_product_summary prompt missing: {e}")
        return ""

    try:
        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=150, temperature=0.3,
                messages=[{"role": "system", "content": prompt},
                           {"role": "user", "content": "Show the product summary and recommendation."}],
            )
        )
        content = r.choices[0].message.content
        if not content:
            return ""
        return content.strip()
    except Exception as e:
        print(f"[NEGOTIATOR] build_product_summary failed: {e}")
        return ""


async def _reply_first_offer(incoming, product_name, price_num, regular_price, graphrag_discount_pct, offer, tiers: Optional[list] = None) -> str:
    prompt = get_prompt(
        incoming, "neg_first_offer_prompt",
        sender_name=incoming.sender_name, biz_name=incoming.biz_name,
        product_name=product_name, regular_price=f"{regular_price:,.0f}",
        price_num=f"{price_num:,.0f}", graphrag_discount_pct=graphrag_discount_pct,
        quantity=offer["quantity"], offer_price=f"{offer['offer_price']:,.0f}",
        offer_total=f"{offer['total_price']:,.0f}",
    )
    base_reply = None
    try:
        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=200, temperature=0.4,
                messages=[{"role": "system", "content": prompt},
                           {"role": "user", "content": "Present the offer."}],
            )
        )
        content = r.choices[0].message.content
        base_reply = content.strip() if content else ""
        if not base_reply:
            raise ValueError("Empty response from AI")
    except RuntimeError: raise
    except Exception as e:
        print(f"[NEGOTIATOR] _reply_first_offer failed: {e}")
        base_reply = f"Great news, {incoming.sender_name}! 🎉 For *{offer['quantity']} units* of *{product_name}*: *Rs.{offer['offer_price']:,.0f}*/unit (Total: *Rs.{offer['total_price']:,.0f}*). Shall we proceed?"

    # ── FIX: Append "order N more to unlock X% off" upsell hint ───────────────
    # This was previously only present on the FIRST-time order entry path in
    # product_followup.py (auto_offer_upsell). Negotiation-driven replies
    # (this function, and the qty-change path below) never showed it, so the
    # recommendation disappeared as soon as the customer entered the
    # negotiation flow instead of the plain-order flow.
    if tiers:
        next_tier = get_next_tier(offer.get("order_value", 0), tiers)
        if next_tier:
            next_min_val, next_disc_pct = next_tier
            value_gap    = round(next_min_val - offer.get("order_value", 0), 0)
            units_needed = max(1, int(value_gap / price_num) + 1)
            base_reply += (
                f"\n\n💡 Add Rs.{value_gap:,.0f} more to your order value "
                f"(approx. {units_needed} more unit(s)) to reach Rs.{next_min_val:,} "
                f"and unlock *{next_disc_pct}% off*!"
            )
        elif offer.get("current_tier_disc", 0) > 0:
            max_disc = max(d for _, d in tiers)
            base_reply += f"\n\n🎉 You've unlocked our *maximum store discount of {max_disc}% OFF*!"

    return base_reply


async def _reply_counter_offer(incoming, product_name, customer_price, new_offer, quantity, total, rounds, is_final, previous_price: float = 0, customer_offer_note: str = "") -> str:
    prompt = get_prompt(
        incoming, "neg_counter_offer_prompt",
        sender_name=incoming.sender_name, biz_name=incoming.biz_name,
        product_name=product_name, customer_price=f"{customer_price:,.0f}",
        new_offer=f"{new_offer:,.0f}", new_total=f"{total:,.0f}",
        quantity=quantity, rounds=rounds,
        # customer_offer_note: non-empty when customer has raised their budget
        # ("customer has moved from Rs.X to Rs.Y") — use it to acknowledge progress
        customer_offer_note=customer_offer_note or "none",
        is_final_msg="This IS your final offer — hold firm." if is_final else "Leave room for one more round.",
    )
    try:
        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=200, temperature=0.4,
                messages=[{"role": "system", "content": prompt},
                           {"role": "user", "content": "Respond to counter-offer."}],
            )
        )
        content = r.choices[0].message.content
        if not content:
            raise ValueError("Empty response from AI")
        return content.strip()
    except RuntimeError: raise
    except Exception as e:
        print(f"[NEGOTIATOR] _reply_counter_offer failed: {e}")
        return f"{incoming.sender_name}, we can do *Rs.{new_offer:,.0f}*/unit (Total: *Rs.{total:,.0f}* for {quantity} units). {'This is our best price.' if is_final else 'Shall we proceed?'}"


async def _reply_final_price(incoming, product_name, last_offer, quantity) -> str:
    total  = round(last_offer * quantity, 2)
    prompt = get_prompt(
        incoming, "neg_final_price_prompt",
        sender_name=incoming.sender_name, biz_name=incoming.biz_name,
        product_name=product_name, last_offer=f"{last_offer:,.0f}",
        total=f"{total:,.0f}", quantity=quantity,
    )
    try:
        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=120, temperature=0.3,
                messages=[{"role": "system", "content": prompt},
                           {"role": "user", "content": "Generate firm final price response."}],
            )
        )
        content = r.choices[0].message.content
        if not content:
            raise ValueError("Empty response from AI")
        return content.strip()
    except RuntimeError: raise
    except Exception as e:
        print(f"[NEGOTIATOR] _reply_final_price failed: {e}")
        return f"{incoming.sender_name}, *Rs.{last_offer:,.0f}/unit* is our absolute best price (Total: *Rs.{total:,.0f}*). 🙏 Would you like to proceed?"


# ══════════════════════════════════════════════════════════════════════════════
# MAIN HANDLER
# ══════════════════════════════════════════════════════════════════════════════


# Confidence threshold below which we fall back to Phase 1 individual calls.
# Tune this value based on observed fallback rate in ai_metrics.
_NEG_CONFIDENCE_THRESHOLD = 0.75


async def extract_negotiation_intent(
    msg: str, incoming, quantity: int, last_offer: float,
    product_name: str, session_history: Optional[list] = None,
) -> dict:
    """
    Phase 2 optimization: single structured LLM call replacing 4 separate
    detection calls with one JSON response.

    Returns a rich structured result with intent, confidence, and matched_phrase:
        {
            "intent":         str,          # PRIMARY: ACCEPTED | QTY_CHANGE | COUNTER_OFFER | MORE_DISCOUNT | NONE
            "matched_phrase": str,          # The exact phrase that triggered this classification
            "accepted":       {"value": bool,  "confidence": float},
            "quantity_change":{"value": int|None, "confidence": float},
            "counter_offer":  {"value": float|None, "confidence": float},
            "more_discount":  {"value": bool, "confidence": float},
        }

    BUSINESS RULE — PRECEDENCE (enforced by caller, documented here):
        1. intent=ACCEPTED       → close negotiation, ignore all other fields
        2. intent=QTY_CHANGE     → update quantity, ignore price fields
        3. intent=COUNTER_OFFER  → customer proposed a specific price
        4. intent=MORE_DISCOUNT  → vague discount request
        5. intent=NONE           → unrelated message, hold current offer

    CONFIDENCE THRESHOLD:
        If the primary intent confidence < _NEG_CONFIDENCE_THRESHOLD (0.75),
        fall back to Phase 1 parallel individual calls for safety.

    METRICS:
        Records extraction latency, confidence, and fallback rate to ai_metrics
        via incoming._updates so AIOrchestrator can flush them at pipeline end.

    Falls back gracefully on any error — returns {} to trigger Phase 1.
    """
    import time as _time
    _t_start = _time.monotonic()

    prompt = get_prompt(
        incoming, "neg_extract_negotiation_intent_prompt",
        product_name   = product_name,
        current_price  = f"{last_offer:,.0f}",
        current_qty    = quantity,
    )
    try:
        messages: List[ChatCompletionMessageParam] = [{"role": "system", "content": prompt}]
        if session_history:
            messages.extend(cast(List[ChatCompletionMessageParam], session_history[-4:]))
        messages.append({"role": "user", "content": msg})

        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _client.chat.completions.create(
                model           = AZURE_OPENAI_DEPLOYMENT,
                max_tokens      = 150,
                temperature     = 0,
                messages        = messages,
                response_format = {"type": "json_object"},
            )
        )
        raw = r.choices[0].message.content
        if not raw:
            raise ValueError("Empty response from LLM")

        parsed = json.loads(raw)

        # Extract structured fields with confidence
        def _conf(field: str) -> float:
            v = parsed.get(field, {})
            return float(v.get("confidence", 0.0)) if isinstance(v, dict) else 0.0

        def _val(field: str):
            v = parsed.get(field, {})
            return v.get("value") if isinstance(v, dict) else v

        intent          = str(parsed.get("intent", "NONE")).upper()
        matched_phrase  = str(parsed.get("matched_phrase", ""))
        primary_conf    = _conf(intent.lower().replace("_", "")) if intent != "NONE" else 0.0

        # Map intent name to confidence field
        conf_map = {
            "ACCEPTED":       _conf("accepted"),
            "QTY_CHANGE":     _conf("quantity_change"),
            "COUNTER_OFFER":  _conf("counter_offer"),
            "MORE_DISCOUNT":  _conf("more_discount"),
        }
        primary_conf = conf_map.get(intent, 0.0)

        latency_ms = round((_time.monotonic() - _t_start) * 1000)
        print(f"[NEGOTIATOR] extract_intent: intent={intent} conf={primary_conf:.2f} "
              f"phrase='{matched_phrase[:40]}' latency={latency_ms}ms")

        # Record metrics for monitoring
        if hasattr(incoming, '_updates'):
            incoming._updates["neg_intent_latency_ms"]   = latency_ms
            incoming._updates["neg_intent_confidence"]   = primary_conf
            incoming._updates["neg_intent_result"]       = intent
            incoming._updates["neg_intent_fallback"]     = False
            incoming._updates["prompt_tokens"]           = (r.usage.prompt_tokens     if r.usage else 0)
            incoming._updates["completion_tokens"]       = (r.usage.completion_tokens if r.usage else 0)

        # Confidence gate — fall back if model is uncertain
        if primary_conf < _NEG_CONFIDENCE_THRESHOLD and intent != "NONE":
            print(f"[NEGOTIATOR] Low confidence ({primary_conf:.2f} < {_NEG_CONFIDENCE_THRESHOLD}) "
                  f"for intent={intent} — falling back to Phase 1")
            if hasattr(incoming, '_updates'):
                incoming._updates["neg_intent_fallback"] = True
            return {}

        raw_qty = _val("quantity_change")
        qty_change_val = None
        if raw_qty is not None and str(raw_qty).strip() != "":
            try:
                qty_change_val = int(raw_qty)
            except (ValueError, TypeError):
                pass

        raw_counter = _val("counter_offer")
        counter_offer_val = None
        if raw_counter is not None and str(raw_counter).strip() != "":
            try:
                counter_offer_val = float(raw_counter)
            except (ValueError, TypeError):
                pass

        return {
            "intent":          intent,
            "matched_phrase":  matched_phrase,
            "accepted":        {"value": bool(_val("accepted") or False),           "confidence": _conf("accepted")},
            "quantity_change": {"value": qty_change_val,                            "confidence": _conf("quantity_change")},
            "counter_offer":   {"value": counter_offer_val,                         "confidence": _conf("counter_offer")},
            "more_discount":   {"value": bool(_val("more_discount") or False),       "confidence": _conf("more_discount")},
        }

    except Exception as e:
        latency_ms = round((_time.monotonic() - _t_start) * 1000)
        print(f"[NEGOTIATOR] extract_negotiation_intent failed ({latency_ms}ms): {e} — falling back to Phase 1")
        if hasattr(incoming, '_updates'):
            incoming._updates["neg_intent_fallback"] = True
        return {}


async def handle_negotiation(
    incoming, product_name: str, price_num: float, regular_price: float,
    graphrag_discount_pct: int, session_history: list,
    negotiation_state: dict, global_offers: Optional[str] = None,
    product_data: Optional[dict] = None,
) -> dict:
    msg      = incoming.text
    rounds   = negotiation_state.get("rounds", 0)
    quantity = negotiation_state.get("quantity")

    _cached_tiers = negotiation_state.get("_tiers")
    tiers = _cached_tiers if _cached_tiers is not None else (
        parse_global_offer_tiers(incoming, global_offers) if global_offers else []
    )
    if not _cached_tiers and global_offers:
        print(f"[NEGOTIATOR] Parsed tiers: {tiers}")

    # FIX: floor_price now uses the TRUE maximum tier discount available
    # (highest % across all tiers), not get_negotiation_floor_disc()'s fixed
    # tiers[1] lookup. This matches the corrected calculate_offer() logic
    # above and ensures the negotiation ceiling actually reflects the best
    # discount tier the store offers — not an arbitrary fixed position.
    max_disc    = max((d for _, d in tiers), default=0) if tiers else 0
    floor_disc  = max_disc if max_disc > 0 else get_negotiation_floor_disc(tiers)
    floor_price = round(price_num * (1 - floor_disc / 100), 2)
    awaiting_qty = negotiation_state.get("awaiting_quantity", False)

    _saved_auto = negotiation_state.get("auto_offer_unit_price")
    negotiation_baseline = float(_saved_auto) if _saved_auto and float(_saved_auto) < price_num else price_num
    if negotiation_baseline < price_num:
        floor_price = round(negotiation_baseline * 0.95)  # whole rupees — no fractional prices

    def _state(**kw): return {**negotiation_state, "product_name": product_name, "price_num": price_num,
                               "floor_price": floor_price, "global_offers": global_offers, "_tiers": tiers,
                               "negotiation_baseline": negotiation_baseline, **kw}

    # Step 1: Awaiting quantity
    if awaiting_qty:
        quantity = await extract_quantity(msg, product_name, incoming, session_history)
        if not quantity:
            return {"reply": f"I didn't catch that, {incoming.sender_name}. How many units of *{product_name}* would you like?",
                    "state": _state(awaiting_quantity=True, rounds=rounds),
                    "order_ready": False, "escalate": False, "agreed_price": None, "quantity": None}
        offer = calculate_offer(price_num, quantity, tiers)
        if not offer["has_discount"]:
            reply = await _reply_no_discount(incoming, product_name, price_num, regular_price, graphrag_discount_pct, quantity)
            return {"reply": reply, "state": _state(awaiting_quantity=False, quantity=quantity, rounds=0),
                    "order_ready": False, "escalate": False, "agreed_price": price_num, "quantity": quantity}
        reply = await _reply_first_offer(incoming, product_name, price_num, regular_price, graphrag_discount_pct, offer, tiers)
        return {"reply": reply,
                "state": _state(awaiting_quantity=False, quantity=quantity, rounds=1,
                                last_offer_price=offer["offer_price"],
                                auto_offer_unit_price=offer["offer_price"],
                                auto_offer_disc_pct=offer.get("current_tier_disc", 0),
                                current_tier_disc=offer.get("current_tier_disc", 0)),
                "order_ready": False, "escalate": False, "agreed_price": offer["offer_price"], "quantity": quantity}

    # Step 2: No quantity yet
    if not quantity:
        quantity = await extract_quantity(msg, product_name, incoming, session_history)
        if not quantity:
            return {"reply": f"I'd be happy to work on pricing for *{product_name}*, {incoming.sender_name}! How many units are you looking for?",
                    "state": _state(awaiting_quantity=True, rounds=0),
                    "order_ready": False, "escalate": False, "agreed_price": None, "quantity": None}
        offer = calculate_offer(price_num, quantity, tiers)
        if not offer["has_discount"]:
            reply = await _reply_no_discount(incoming, product_name, price_num, regular_price, graphrag_discount_pct, quantity)
            return {"reply": reply, "state": _state(quantity=quantity, rounds=0),
                    "order_ready": False, "escalate": False, "agreed_price": price_num, "quantity": quantity}
        reply = await _reply_first_offer(incoming, product_name, price_num, regular_price, graphrag_discount_pct, offer, tiers)
        return {"reply": reply,
                "state": _state(quantity=quantity, rounds=1,
                                last_offer_price=offer["offer_price"],
                                auto_offer_unit_price=offer["offer_price"],
                                auto_offer_disc_pct=offer.get("current_tier_disc", 0),
                                current_tier_disc=offer.get("current_tier_disc", 0)),
                "order_ready": False, "escalate": False, "agreed_price": offer["offer_price"], "quantity": quantity}

    # Step 3: Ongoing negotiation
    quantity = int(quantity)

    new_qty = None
    accepted = False
    counter = None
    more_disc = False

    # FIX: last_offer must start from the tier price (what the customer is currently
    # paying), NOT price_num (the list price). Using price_num caused the step
    # calculation to use a gap of ~300 instead of ~115, collapsing the full
    # negotiation window into a single round.
    #
    # Priority: saved last_offer_price > auto_offer_unit_price > price_num
    _lop = negotiation_state.get("last_offer_price")
    _aou = negotiation_state.get("auto_offer_unit_price")
    if _lop:
        last_offer = float(_lop)
    elif _aou and float(_aou) < price_num:
        last_offer = float(_aou)  # start from tier price, not list price
    else:
        last_offer = price_num

    # ── Phase 2: single structured extraction (fast path) ───────────────────
    # One LLM call with JSON output replaces 4 separate detection calls.
    # Falls back to Phase 1 parallel batches if this fails.
    _intent = await extract_negotiation_intent(
        msg, incoming, quantity, last_offer, product_name, session_history
    )
    if _intent:
        # Use the rich structured result — apply precedence via intent field.
        # intent is the single authoritative classification, more reliable than
        # inferring from individual boolean/numeric fields.
        _primary = _intent.get("intent", "NONE")
        _accepted_val  = (_intent.get("accepted",        {}) or {}).get("value", False)
        _qty_val       = (_intent.get("quantity_change", {}) or {}).get("value")
        _counter_val   = (_intent.get("counter_offer",   {}) or {}).get("value")
        _disc_val      = (_intent.get("more_discount",   {}) or {}).get("value", False)

        # Log matched phrase for debugging without exposing chain-of-thought
        _phrase = _intent.get("matched_phrase", "")
        if _phrase:
            print(f"[NEGOTIATOR] Matched phrase: '{_phrase}'")

        # PRECEDENCE 1: Acceptance — close negotiation immediately
        if _primary == "ACCEPTED" and _accepted_val:
            return {"reply": f"Wonderful! 🎉 Confirming *{quantity} units* of *{product_name}* at *Rs.{last_offer:,.0f}/unit*. Reply *Confirm* to place your order!",
                    "state": _state(quantity=quantity, rounds=rounds, last_offer_price=last_offer, awaiting_invoice_confirmation=True),
                    "order_ready": True, "escalate": False, "agreed_price": last_offer, "quantity": quantity}

        # PRECEDENCE 2: Quantity change — update qty, ignore price signals
        if _primary == "QTY_CHANGE" and _qty_val and _qty_val != quantity:
            new_qty = _qty_val
        else:
            new_qty = None

        # PRECEDENCE 3+4: Price signals — used in negotiation step below
        accepted  = False
        counter   = _counter_val if _primary == "COUNTER_OFFER" else None
        more_disc = _disc_val    if _primary == "MORE_DISCOUNT"  else False
        goto_qty_block = True
    else:
        goto_qty_block = False

    if not goto_qty_block:
        # ── Phase 1 fallback: parallel batches ───────────────────────────────
        # Phase 1B parallelization — Step 1: run acceptance + qty change together.
        # These are independent LLM calls — neither depends on the other's output.
        #
        # BUSINESS RULE — EXPLICIT PRECEDENCE (document here, not just in code flow):
    # If a message triggers BOTH acceptance AND a quantity change simultaneously
    # (e.g. "Yes, make it 6 units" or "Proceed with 6 units"), the rule is:
    #
    #   ACCEPTANCE WINS — the quantity change is IGNORED.
    #
    # Rationale: The customer has agreed to the current offer at the current
    # quantity. Changing quantity mid-acceptance would silently alter pricing,
    # GST, and the negotiated discount tier — potentially in the customer's
    # favor without their awareness of the pricing impact.
    #
    # If the customer wants a different quantity, they must:
    #   1. Not confirm yet, OR
    #   2. Say "add N units" first, then confirm on the updated summary.
    #
    # This rule is implemented by checking `if accepted: return` BEFORE
    # processing new_qty, so new_qty is discarded when accepted=True.
        import re as _re
        _price_patterns = [
            r'\bcan (i|we) get (it )?for\b',
            r'\bhow about\b',
            r'\bwhat about\b',
            r'\bcan we move (with|to|at)\b',
            r'\bmy (budget|price|offer) is\b',
            r'\bfinal (budget|price|offer)\b',
            r'\bcan you (do|offer|give)\b',
            r'\bwill you (take|accept)\b',
            r'\bprice.*\brs\.?\s*\d\b',
            r'\brs\.?\s*\d+.*\bper unit\b',
        ]
        _is_price_msg = any(_re.search(p, msg.lower()) for p in _price_patterns)

        # Run acceptance and qty change in parallel — saves ~1.5s vs sequential
        if _is_price_msg:
            accepted = await detect_acceptance(msg, incoming, session_history)
            new_qty  = None
        else:
            accepted, new_qty = await asyncio.gather(
                detect_acceptance(msg, incoming, session_history),
                detect_quantity_change(msg, quantity, product_name, incoming),
            )

        # Precedence: acceptance always wins — handle it before qty change
        if accepted:
            return {"reply": f"Wonderful! 🎉 Confirming *{quantity} units* of *{product_name}* at *Rs.{last_offer:,.0f}/unit*. Reply *Confirm* to place your order!",
                    "state": _state(quantity=quantity, rounds=rounds, last_offer_price=last_offer, awaiting_invoice_confirmation=True),
                    "order_ready": True, "escalate": False, "agreed_price": last_offer, "quantity": quantity}
    if new_qty and new_qty != quantity:
        quantity = new_qty
        offer    = calculate_offer(price_num, quantity, tiers)

        # ── FIX: Add "order N more to unlock X% off" upsell hint ──────────────
        # ROOT CAUSE OF MISSING RECOMMENDATION: this quantity-change reply path
        # (triggered by "add 2 more units") never called get_next_tier() — it
        # was only wired into the FIRST-time order entry path in
        # product_followup.py, not this negotiation-continuation path. So
        # every subsequent "add N more units" message lost the upsell hint
        # that the customer saw on their very first order message.
        upsell_line = ""
        next_tier = get_next_tier(offer["order_value"], tiers)
        if next_tier:
            next_min_val, next_disc_pct = next_tier
            value_gap    = round(next_min_val - offer["order_value"], 0)
            units_needed = max(1, int(value_gap / price_num) + 1)
            upsell_line = (
                f"\n\n💡 Add Rs.{value_gap:,.0f} more to your order value "
                f"(approx. {units_needed} more unit(s)) to reach Rs.{next_min_val:,} "
                f"and unlock *{next_disc_pct}% off*!"
            )
        elif tiers and offer.get("current_tier_disc", 0) > 0:
            # Only show the "maximum tier unlocked" message when the customer
            # JUST crossed into the top tier this message — not on every
            # subsequent qty update while already at the top tier.
            # Check: was the previous tier_disc less than the current one?
            prev_tier_disc = negotiation_state.get("current_tier_disc", 0)
            max_disc       = max(d for _, d in tiers)
            if offer.get("current_tier_disc", 0) >= max_disc and offer.get("current_tier_disc", 0) > prev_tier_disc:
                upsell_line = f"\n\n🎉 You've just unlocked our *maximum store discount of {max_disc}% OFF*!"
            # else: already at max tier on previous message — no need to repeat

        # ── Bugs 1,3,4,5,6,7 fix: clean single-message quantity-change reply ──
        # Bug 3+4: Never show "Negotiated price" unless negotiation has started
        #   (rounds > 0 or customer explicitly asked for discount). The gap/3
        #   concession off the tier price is NOT negotiation — it's just the
        #   auto-offer engine finding the best tier price.
        # Bug 1: Return ONE complete message, not "Updated!" + full summary separately.
        # Bug 5+6: One clear CTA, not "Shall we proceed?" + "Reply Confirm" both.
        # Bug 7: Lead with explicit acknowledgement of the quantity change.

        summary_text = await build_product_summary(incoming, product_data)
        summary_block = summary_text.strip() if summary_text else ""

        tier_price        = offer["offer_price"]
        sub_price         = round(tier_price * quantity, 2)
        gst_amount        = round(sub_price * getattr(incoming, "gst_rate", 0.18), 2)
        total_pay         = round(sub_price + gst_amount, 2)
        gst_pct           = int(getattr(incoming, "gst_rate", 0.18) * 100)
        current_tier_disc = offer.get("current_tier_disc", 0)

        prev_qty = negotiation_state.get("quantity", quantity)
        lines = [
            f"✅ Updated your order from *{prev_qty}* to *{quantity} units*, {incoming.sender_name}!",
            "",
            f"• *Product:* {product_name}",
            f"• *Quantity:* {quantity} units",
            f"• *Regular price:* Rs.{price_num:,.0f}/unit",
        ]
        if current_tier_disc > 0:
            lines.append(
                f"• *Store offer {current_tier_disc}% OFF applied:* Rs.{tier_price:,.0f}/unit"
            )
        else:
            lines.append(f"• *Unit price:* Rs.{tier_price:,.0f}")
        lines += [
            f"• *Subtotal:* Rs.{sub_price:,.2f}",
            f"• *GST ({gst_pct}%):* Rs.{gst_amount:,.2f}",
            f"• *Total Payable:* Rs.{total_pay:,.2f}",
        ]
        if upsell_line:
            lines.append(upsell_line.strip())
        if summary_block:
            lines += ["", summary_block]
        lines += ["", "Reply *Confirm* to place your order! 🎉"]

        update_reply = "\n".join(lines)

        return {"reply": update_reply,
                "state": _state(quantity=quantity, rounds=rounds, last_offer_price=offer["offer_price"],
                                current_tier_disc=offer.get("current_tier_disc", 0),
                                auto_offer_disc_pct=offer.get("current_tier_disc", 0),
                                auto_offer_unit_price=offer["offer_price"]),
                "order_ready": False, "escalate": False, "agreed_price": offer["offer_price"], "quantity": quantity}

    # Phase 1B step 2: counter-offer + more-discount are independent.
    # Only reached when acceptance=False and no qty change — safe to parallelize.
    if not goto_qty_block:
        counter, more_disc = await asyncio.gather(
            detect_counter_offer(msg, incoming, last_offer, quantity, session_history),
            detect_more_discount_request(msg, incoming, session_history),
        )

    # End of Phase 1 fallback block — counter and more_disc are now set
    # (either from Phase 2 structured extraction or Phase 1 parallel batches)

    if not counter and not more_disc:
        return {"reply": f"Our current offer is *Rs.{last_offer:,.0f}/unit* for *{quantity} units* (Total: *Rs.{round(last_offer*quantity,2):,.0f}*). Would you like to proceed?",
                "state": _state(quantity=quantity, rounds=rounds, last_offer_price=last_offer),
                "order_ready": False, "escalate": False, "agreed_price": last_offer, "quantity": quantity}

    rounds   += 1
    is_final  = rounds >= MAX_NEGOTIATION_ROUNDS

    # ── Stateful bargaining engine ────────────────────────────────────────────
    # Improvements over the fixed-step approach:
    #
    # 1. DYNAMIC STEP SIZES: escalating concession percentages per round
    #    (15% → 25% → 35% → final) feel like a real salesperson gradually
    #    giving ground rather than a robot reducing by exactly Rs.23 each time.
    #
    # 2. CAPPED REACTIVE MOVE: customer's ask influences our counter but is
    #    capped at MAX_REACTIVE_MOVE (Rs.50) so asking Rs.1 can't jump us to
    #    the floor. Reactive only applies when customer asks ABOVE floor (below-
    #    floor asks are ignored for reactive — no point moving toward impossible).
    #
    # 3. EARLY EXIT: if customer's ask is within CLOSE_THRESHOLD (Rs.25) of
    #    our current offer, accept their price immediately. This rewards customers
    #    who make reasonable offers and avoids unnecessary extra rounds.
    #
    # 4. CUSTOMER OFFER MEMORY: track trajectory so we can acknowledge
    #    when customers raise their budget ("you've moved from Rs.X to Rs.Y").
    STEP_PCTS         = [0.15, 0.25, 0.35, 0.25]   # % of total gap conceded per round
    MAX_REACTIVE_MOVE = 50    # max extra Rs. conceded toward customer's ask per round
    CLOSE_THRESHOLD   = 25    # Rs. gap below which we accept customer's offer directly

    total_gap = negotiation_baseline - floor_price

    # Track customer offer trajectory in state for memory
    prev_customer_offers = negotiation_state.get("customer_offers", [])
    if counter:
        prev_customer_offers = prev_customer_offers + [counter]

    # Early exit: if customer is very close to our current offer, meet them there
    if (counter and counter >= floor_price
            and abs(last_offer - counter) <= CLOSE_THRESHOLD):
        new_offer = round(counter)  # accept customer's price (they're close enough)
        new_total = round(new_offer * quantity, 2)
        # Early close → treat as final, set counter_offer_presented
        reply = await _reply_final_price(incoming, product_name, new_offer, quantity)
        return {"reply": reply,
                "state": _state(quantity=quantity, rounds=rounds, last_offer_price=new_offer,
                                counter_offer_presented=True, awaiting_invoice_confirmation=False,
                                customer_offers=prev_customer_offers),
                "order_ready": True, "escalate": False, "agreed_price": new_offer, "quantity": quantity}

    if total_gap > 0:
        # Dynamic step: cumulative % of gap given up through this round
        cumulative_pct = sum(STEP_PCTS[:rounds]) if rounds <= len(STEP_PCTS) else 1.0
        step_price     = round(negotiation_baseline - total_gap * cumulative_pct)

        # Reactive: only when customer asks above floor (below-floor asks ignored)
        if counter and counter > floor_price:
            reactive  = min(round((last_offer - counter) * 0.20), MAX_REACTIVE_MOVE)
        else:
            reactive  = 0

        new_offer = max(round(step_price - reactive), floor_price)
    else:
        new_offer = floor_price

    new_total = round(new_offer * quantity, 2)

    # When customer asks below floor, explain and present floor as best offer
    # Previously this jumped straight to order_ready=True (the pre-confirm
    # summary) without any explanation — the customer never learned WHY their
    # offer was rejected or what our actual best price is.
    #
    # Also fixes Bug 4 (repeated negotiation with same floor price):
    # Previously the second "can I get it for 1600?" re-entered the same path,
    # recalculated the same floor, and returned the same pre-confirm summary
    # with no feedback. Now it will explicitly state this is the final price.
    customer_asked_below_floor = counter is not None and counter < floor_price

    if is_final or (new_offer <= floor_price and rounds > 1):
        # Exhausted rounds or hit floor on round 2+: this is our final price.
        # Still do NOT set awaiting_invoice_confirmation=True — the customer must
        # explicitly accept (say "OK", "proceed", "I'll take it") before we confirm.
        # counter_offer_presented=True tells the NEG GUARD to stay in negotiation mode.
        new_offer = floor_price
        reply = await _reply_final_price(incoming, product_name, new_offer, quantity)
        return {"reply": reply,
                "state": _state(quantity=quantity, rounds=rounds, last_offer_price=new_offer,
                                counter_offer_presented=True, awaiting_invoice_confirmation=False),
                "order_ready": True, "escalate": False, "agreed_price": new_offer, "quantity": quantity}

    if customer_asked_below_floor:
        # Customer asked below our floor price. We still respond with the
        # current-round counter-offer (not the floor immediately) — this gives
        # the customer a real concession to react to and keeps the conversation
        # feeling like multi-turn negotiation, not a one-step rejection.
        # Only present floor as "final" on the last round or if we're already there.
        reply = await _reply_counter_offer(
            incoming, product_name, counter, new_offer, quantity, new_total,
            rounds, is_final=is_final,
            previous_price=float(last_offer or 0)
        )
        return {"reply": reply,
                "state": _state(quantity=quantity, rounds=rounds, last_offer_price=new_offer,
                                counter_offer_presented=is_final, awaiting_invoice_confirmation=False,
                                customer_offers=prev_customer_offers),
                "order_ready": is_final, "escalate": False, "agreed_price": new_offer, "quantity": quantity}

    # Build customer offer context for prompt (budget trajectory awareness)
    customer_offer_note = ""
    if len(prev_customer_offers) >= 2:
        first_ask = prev_customer_offers[0]
        latest_ask = prev_customer_offers[-1]
        if latest_ask > first_ask:
            customer_offer_note = f"customer has moved budget from Rs.{first_ask:,.0f} to Rs.{latest_ask:,.0f}"

    reply = await _reply_counter_offer(incoming, product_name, counter or last_offer, new_offer, quantity, new_total, rounds, is_final,
                                       previous_price=float(last_offer or 0), customer_offer_note=customer_offer_note)
    return {"reply": reply,
            "state": _state(quantity=quantity, rounds=rounds, last_offer_price=new_offer,
                            customer_offers=prev_customer_offers),
            "order_ready": False, "escalate": False, "agreed_price": new_offer, "quantity": quantity}