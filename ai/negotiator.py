# ai/negotiator.py — Price Negotiator (fully dynamic prompts)
#
# RULE: No prompt string in this file.
#       All prompts fetched via get_prompt(incoming, key, **vars).
#       Raises RuntimeError if any prompt missing in DB.

import asyncio
import json
from dataclasses import dataclass
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
        # 1. Check database cache first (synchronously via Supabase client)
        from db.session_store import _get_client
        db_res = _get_client().table("tenant_offers") \
            .select("tiers_json") \
            .eq("tenant_id", incoming.tenant_id) \
            .limit(1) \
            .execute()
        if db_res.data and isinstance(db_res.data, list) and len(db_res.data) > 0:
            first_row = db_res.data[0]
            if isinstance(first_row, dict):
                tiers_raw = first_row.get("tiers_json")
                if isinstance(tiers_raw, str):
                    parsed = json.loads(tiers_raw)
                    if isinstance(parsed, list) and all(len(t) == 2 for t in parsed):
                        return sorted(parsed, key=lambda x: x[0])

        # 2. If missing/invalid, parse via LLM
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
            sorted_tiers = sorted(parsed, key=lambda x: x[0])
            # 3. Cache the parsed result back to Postgres
            try:
                from datetime import datetime, timezone
                row = {
                    "tenant_id":   incoming.tenant_id,
                    "offers_text": global_offers.strip(),
                    "tiers_json":  json.dumps(sorted_tiers),
                    "updated_at":  datetime.now(timezone.utc).isoformat(),
                }
                _get_client().table("tenant_offers") \
                    .upsert(row, on_conflict="tenant_id") \
                    .execute()
                print(f"[NEGOTIATOR] Cached tiers_json to DB for {incoming.tenant_id}")
            except Exception as save_err:
                print(f"[NEGOTIATOR] Failed to cache tiers_json: {save_err}")
            return sorted_tiers
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

async def is_negotiation_request(message: str, incoming, session_history: Optional[list] = None) -> bool:
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
    from utils.conversation_actions import is_quick_confirm
    if is_quick_confirm(incoming, message):
        print(f"[NEGOTIATOR] detect_acceptance: fast-path match (no LLM) for '{message.strip()}'")
        return True
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


# ── Consolidated negotiation-intent extraction ────────────────────────────────
# Replaces the 4 separate detect_* calls above (detect_acceptance,
# detect_quantity_change, detect_counter_offer, detect_more_discount_request)
# with ONE LLM call to neg_extract_negotiation_intent_prompt (migration 017,
# enhanced with UNIT/TOTAL price disambiguation by migration 027).
#
# The four detectors above are kept in the file (still used by is_negotiation_request
# and extract_quantity's callers elsewhere) but handle_negotiation()'s Step 3
# now calls extract_negotiation_intent() instead of calling all four in sequence.
#
# RULE: the LLM only classifies. All arithmetic (SET/ADD/REMOVE quantity math,
# TOTAL-to-per-unit price division) happens in Python — see
# _apply_quantity_operation() and _resolve_counter_offer_price() below.

@dataclass
class NegotiationIntent:
    intent:                    str             # ACCEPTED | QTY_CHANGE | COUNTER_OFFER | MORE_DISCOUNT | NONE
    matched_phrase:            str
    accepted:                  bool
    quantity_value:            Optional[int]   # raw number customer said — NOT computed
    quantity_operation:        Optional[str]   # SET | ADD | REMOVE
    counter_offer_value:       Optional[float]  # raw number customer said — NOT divided
    counter_offer_price_type:  Optional[str]   # UNIT | TOTAL
    more_discount:             bool
    confidence:                float


def _apply_quantity_operation(current_qty: int, operation: Optional[str], value: Optional[int]) -> Optional[int]:
    """Pure Python arithmetic for SET/ADD/REMOVE — the LLM never computes this, only classifies."""
    if value is None or operation is None:
        return None
    if operation == "SET":
        return value
    if operation == "ADD":
        return current_qty + value
    if operation == "REMOVE":
        return max(0, current_qty - value)
    return None


def _resolve_counter_offer_price(value: Optional[float], price_type: Optional[str], quantity: int) -> Optional[float]:
    """Converts a TOTAL price ask into a per-unit price. UNIT passes through unchanged.
    The LLM only classifies UNIT vs TOTAL — this division is the only arithmetic, done here."""
    if value is None:
        return None
    if price_type == "TOTAL" and quantity and quantity > 0:
        return round(value / quantity, 2)
    return float(value)


async def extract_negotiation_intent(
    message: str, product_name: str, current_price: float, current_qty: int,
    incoming, session_history: Optional[list] = None,
) -> NegotiationIntent:
    """
    Single consolidated negotiation-intent extraction. Replaces detect_acceptance
    + detect_quantity_change + detect_counter_offer + detect_more_discount_request
    (4 sequential LLM calls, ~6-8s combined) with 1 call (~2-3s).

    Interprets the customer's message ONCE, holistically — not 4 separate
    times through 4 separate lenses — which also resolves cases like
    "I'll take 5 units if you can do 1500" where the old sequential detectors
    could produce inconsistent independent answers.

    On any parse failure, returns intent="NONE" with everything else empty —
    callers treat this exactly like the old "none of the four detectors
    matched" case (the stalemate/deferral branch in handle_negotiation()).
    """
    current_total = round(current_price * current_qty, 2) if current_price and current_qty else 0
    prompt = get_prompt(
        incoming, "neg_extract_negotiation_intent_prompt",
        product_name=product_name,
        current_price=f"{current_price:,.0f}" if current_price else "N/A",
        current_qty=current_qty or "N/A",
        current_total=f"{current_total:,.0f}",
    )
    try:
        messages: List[ChatCompletionMessageParam] = [{"role": "system", "content": prompt}]
        if session_history:
            messages.extend(cast(List[ChatCompletionMessageParam], session_history[-4:]))
        messages.append({"role": "user", "content": message})

        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=300, temperature=0, messages=messages,
            )
        )
        content = r.choices[0].message.content
        if not content:
            raise ValueError("Empty response content")
        raw = content.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        parsed  = json.loads(raw)
        intent  = str(parsed.get("intent", "NONE")).upper()
        acc_obj = parsed.get("accepted") or {}
        qty_obj = parsed.get("quantity_change") or {}
        cnt_obj = parsed.get("counter_offer") or {}
        mdc_obj = parsed.get("more_discount") or {}

        # Report the confidence relevant to the winning intent (each field
        # carries its own — there is no single top-level confidence).
        _conf_by_intent = {
            "ACCEPTED":      acc_obj.get("confidence"),
            "QTY_CHANGE":    qty_obj.get("confidence"),
            "COUNTER_OFFER": cnt_obj.get("confidence"),
            "MORE_DISCOUNT": mdc_obj.get("confidence"),
        }
        confidence = float(_conf_by_intent.get(intent) or 0.0)

        result = NegotiationIntent(
            intent=intent,
            matched_phrase=str(parsed.get("matched_phrase", message))[:80],
            accepted=bool(acc_obj.get("value", False)),
            quantity_value=qty_obj.get("value"),
            quantity_operation=qty_obj.get("operation"),
            counter_offer_value=cnt_obj.get("value"),
            counter_offer_price_type=cnt_obj.get("price_type"),
            more_discount=bool(mdc_obj.get("value", False)),
            confidence=confidence,
        )
        print(f"[NEGOTIATOR] extract_intent: intent={result.intent} conf={result.confidence:.2f} "
              f"phrase='{result.matched_phrase}'")
        return result

    except RuntimeError:
        raise
    except Exception as e:
        print(f"[NEGOTIATOR] extract_negotiation_intent failed: {e} — treating as NONE")
        return NegotiationIntent(
            intent="NONE", matched_phrase=message[:80], accepted=False,
            quantity_value=None, quantity_operation=None,
            counter_offer_value=None, counter_offer_price_type=None,
            more_discount=False, confidence=0.0,
        )


# ── Reply generators — all prompts from DB ────────────────────────────────────

async def _reply_no_discount(incoming, product_name, price_num, regular_price, discount_pct, quantity, min_units=None) -> str:
    if min_units is None:
        min_units = getattr(incoming, "neg_min_units", 5) or 5
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
        return get_prompt(incoming, "neg_no_discount_fallback", sender_name=incoming.sender_name, price_num=f"{price_num:,.0f}", total=f"{total:,.0f}", min_units=min_units)


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
        base_reply = get_prompt(incoming, "neg_first_offer_fallback", sender_name=incoming.sender_name, quantity=offer["quantity"], product_name=product_name, offer_price=f"{offer['offer_price']:,.0f}", offer_total=f"{offer['total_price']:,.0f}")

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
            upsell_line = get_prompt(
                incoming, "neg_upsell_hint_prompt",
                value_gap=f"{value_gap:,.0f}", units_needed=units_needed,
                next_min_val=f"{next_min_val:,}", next_disc_pct=next_disc_pct
            )
            base_reply += "\n\n" + upsell_line
        elif offer.get("current_tier_disc", 0) > 0:
            max_disc = max(d for _, d in tiers)
            max_disc_line = get_prompt(incoming, "neg_max_discount_unlocked_prompt", max_disc=max_disc)
            base_reply += "\n\n" + max_disc_line

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
        return get_prompt(incoming, "neg_counter_offer_fallback", sender_name=incoming.sender_name, new_offer=f"{new_offer:,.0f}", new_total=f"{total:,.0f}", quantity=quantity, closing_line="This is our best price." if is_final else "Shall we proceed?")


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
        return get_prompt(incoming, "neg_final_price_fallback", sender_name=incoming.sender_name, last_offer=f"{last_offer:,.0f}", total=f"{total:,.0f}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN HANDLER
# ══════════════════════════════════════════════════════════════════════════════

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
            retry_msg = get_prompt(incoming, "neg_ask_quantity_retry_prompt", sender_name=incoming.sender_name, product_name=product_name)
            return {"reply": retry_msg,
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
            ask_msg = get_prompt(incoming, "neg_ask_quantity_prompt", sender_name=incoming.sender_name, product_name=product_name)
            return {"reply": ask_msg,
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

    parsed = await extract_negotiation_intent(msg, product_name, last_offer, quantity, incoming, session_history)

    accepted = parsed.intent == "ACCEPTED"
    if accepted:
        confirm_msg = get_prompt(
            incoming, "neg_accepted_confirmation_prompt",
            quantity=quantity, product_name=product_name, last_offer=f"{last_offer:,.0f}"
        )
        return {"reply": confirm_msg,
                "state": _state(quantity=quantity, rounds=rounds, last_offer_price=last_offer, awaiting_invoice_confirmation=True),
                "order_ready": True, "escalate": False, "agreed_price": last_offer, "quantity": quantity}

    # SET/ADD/REMOVE arithmetic happens here, in Python — extract_negotiation_intent()
    # only classified the operation and raw value, never computed the new total itself.
    new_qty = None
    if parsed.intent == "QTY_CHANGE":
        new_qty = _apply_quantity_operation(quantity, parsed.quantity_operation, parsed.quantity_value)
    if new_qty and new_qty != quantity:
        quantity = new_qty
        offer    = calculate_offer(price_num, quantity, tiers)

        upsell_line = ""
        next_tier = get_next_tier(offer["order_value"], tiers)
        if next_tier:
            next_min_val, next_disc_pct = next_tier
            value_gap    = round(next_min_val - offer["order_value"], 0)
            units_needed = max(1, int(value_gap / price_num) + 1)
            upsell_line = get_prompt(
                incoming, "neg_upsell_hint_prompt",
                value_gap=f"{value_gap:,.0f}", units_needed=units_needed,
                next_min_val=f"{next_min_val:,}", next_disc_pct=next_disc_pct
            )
        elif tiers and offer.get("current_tier_disc", 0) > 0:
            prev_tier_disc = negotiation_state.get("current_tier_disc", 0)
            max_disc       = max(d for _, d in tiers)
            if offer.get("current_tier_disc", 0) >= max_disc and offer.get("current_tier_disc", 0) > prev_tier_disc:
                upsell_line = get_prompt(incoming, "neg_max_discount_unlocked_prompt", max_disc=max_disc)

        summary_text = await build_product_summary(incoming, product_data)
        summary_block = summary_text.strip() if summary_text else ""

        # Same tenant-configurable disclosure gate as product_followup.py's
        # auto-apply block — a store discount the customer never asked about
        # shouldn't silently appear in a quantity-update reply, or become the
        # baseline price they end up negotiating against.
        _require_disclosure = bool(getattr(incoming, "require_offer_disclosure", False))
        _offer_disclosed = bool(negotiation_state.get("offer_disclosed"))
        _disclosure_blocked = _require_disclosure and not _offer_disclosed

        _eff_tier_disc = 0 if _disclosure_blocked else offer.get("current_tier_disc", 0)
        _eff_price     = price_num if _disclosure_blocked else offer["offer_price"]

        sub_price         = round(_eff_price * quantity, 2)
        gst_amount        = round(sub_price * getattr(incoming, "gst_rate", 0.18), 2)
        total_pay         = round(sub_price + gst_amount, 2)
        gst_pct           = int(getattr(incoming, "gst_rate", 0.18) * 100)
        current_tier_disc = _eff_tier_disc

        prev_qty = negotiation_state.get("quantity", quantity)
        if current_tier_disc > 0:
            update_reply = get_prompt(
                incoming, "neg_qty_update_with_discount_prompt",
                prev_qty=prev_qty, quantity=quantity, sender_name=incoming.sender_name,
                product_name=product_name, price_num=f"{price_num:,.0f}",
                current_tier_disc=current_tier_disc, tier_price=f"{_eff_price:,.0f}",
                sub_price=f"{sub_price:,.2f}", gst_pct=gst_pct,
                gst_amount=f"{gst_amount:,.2f}", total_pay=f"{total_pay:,.2f}"
            )
        else:
            update_reply = get_prompt(
                incoming, "neg_qty_update_no_discount_prompt",
                prev_qty=prev_qty, quantity=quantity, sender_name=incoming.sender_name,
                product_name=product_name, price_num=f"{price_num:,.0f}",
                tier_price=f"{_eff_price:,.0f}", sub_price=f"{sub_price:,.2f}",
                gst_pct=gst_pct, gst_amount=f"{gst_amount:,.2f}", total_pay=f"{total_pay:,.2f}"
            )

        if upsell_line and not _disclosure_blocked:
            update_reply += "\n\n" + upsell_line.strip()
        if summary_block:
            update_reply += "\n\n" + summary_block
        update_reply += "\n\n" + get_prompt(incoming, "neg_qty_update_footer_prompt")

        return {"reply": update_reply,
                "state": _state(quantity=quantity, rounds=rounds, last_offer_price=_eff_price,
                                current_tier_disc=_eff_tier_disc,
                                auto_offer_disc_pct=_eff_tier_disc,
                                auto_offer_unit_price=_eff_price),
                "order_ready": False, "escalate": False, "agreed_price": _eff_price, "quantity": quantity}

    counter = _resolve_counter_offer_price(
        parsed.counter_offer_value, parsed.counter_offer_price_type, quantity
    ) if parsed.intent == "COUNTER_OFFER" else None
    more_disc = parsed.intent == "MORE_DISCOUNT"

    if not counter and not more_disc:
        # DEFERRAL: none of accepted / quantity-change / counter-offer /
        # more-discount matched — this message likely isn't about the
        # negotiation at all (e.g. a greeting or an escalation request
        # arriving mid-negotiation). Previously this branch unconditionally
        # repeated the current offer regardless of what was actually said.
        # Check with a real classifier before assuming continued haggling.
        # Only runs in this rare stalemate branch, not the common negotiation
        # path, so the extra LLM call only costs latency on genuinely
        # off-topic interruptions.
        try:
            from ai.handlers import classify_intent
            _defer_result = await classify_intent(msg, session_history, incoming)
        except Exception as e:
            print(f"[NEGOTIATOR] Deferral classify_intent failed (non-critical): {e}")
            _defer_result = None

        if _defer_result and _defer_result.intent in ("GREETING", "HUMAN_ESCALATION", "FAQ_KNOWLEDGE"):
            print(f"[NEGOTIATOR] Message not negotiation-related (intent={_defer_result.intent}) "
                  f"— deferring to normal routing, negotiation state preserved")
            return {"reply": None, "defer_intent": _defer_result,
                    "state": _state(quantity=quantity, rounds=rounds, last_offer_price=last_offer),
                    "order_ready": False, "escalate": False, "agreed_price": last_offer, "quantity": quantity}

        total_val = round(last_offer*quantity, 2)
        stalemate_msg = get_prompt(
            incoming, "neg_stalemate_reply_prompt",
            last_offer=f"{last_offer:,.0f}", quantity=quantity, total=f"{total_val:,.0f}"
        )
        return {"reply": stalemate_msg,
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