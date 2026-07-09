# ai/handlers.py — All AI handlers using DB prompts only
#
# REPLACES: ai/intent_router.py, ai/response_handlers.py
# (entity extraction was removed as dead code — extract_entities/EntityResult had zero callers anywhere in the live pipeline)
#
# RULE: No prompt string exists in this file.
#       Every prompt is fetched via get_prompt(incoming, key).
#       If a prompt is missing in DB → RuntimeError is raised → fix it in DB.

import asyncio
import json
<<<<<<< HEAD
from datetime import datetime, timezone
from typing import List, Optional, Any
=======
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Any, cast
>>>>>>> 2ded835 (fix: resolve import error for DEFAULT_INTENT_MIN_CONFIDENCE and update prompt/session/adapter logic)

from openai import AzureOpenAI
from openai.types.chat import ChatCompletionMessageParam
from config import AZURE_AI_ENDPOINT, AZURE_AI_API_KEY, AZURE_OPENAI_DEPLOYMENT, AZURE_AI_API_VERSION
<<<<<<< HEAD
from models.schemas import IntentResult, IncomingMessage
=======
from models.schemas import IntentResult, EntityResult, OrderItem, IncomingMessage
>>>>>>> 2ded835 (fix: resolve import error for DEFAULT_INTENT_MIN_CONFIDENCE and update prompt/session/adapter logic)
from db.prompt_store import get_prompt

_client = AzureOpenAI(
    azure_endpoint = AZURE_AI_ENDPOINT,
    api_key        = AZURE_AI_API_KEY,
    api_version    = AZURE_AI_API_VERSION,
    timeout        = 30.0,
    max_retries    = 0,
)

DEFAULT_VALID_INTENTS = {"WORKFLOW_ACTION", "FAQ_KNOWLEDGE", "HUMAN_ESCALATION", "GREETING", "UNKNOWN"}
<<<<<<< HEAD
DEFAULT_INTENT_MIN_CONFIDENCE = 0.50  # default — see incoming.intent_min_confidence for tenant override
=======
DEFAULT_INTENT_MIN_CONFIDENCE = 0.50
>>>>>>> 2ded835 (fix: resolve import error for DEFAULT_INTENT_MIN_CONFIDENCE and update prompt/session/adapter logic)


# ══════════════════════════════════════════════════════════════════════════════
# INTENT ROUTER
# ══════════════════════════════════════════════════════════════════════════════

async def classify_intent(
    customer_message: str,
<<<<<<< HEAD
    session_history:  Optional[List[ChatCompletionMessageParam]] = None,
=======
    session_history:  Optional[List[Any]] = None,
>>>>>>> 2ded835 (fix: resolve import error for DEFAULT_INTENT_MIN_CONFIDENCE and update prompt/session/adapter logic)
    incoming: Optional[IncomingMessage] = None,
) -> IntentResult:
    """
    Classifies intent using tenant's intent_system_prompt from DB.
    Raises RuntimeError if prompt not set in DB.
    """
    system_prompt  = get_prompt(incoming, "intent_system_prompt")
<<<<<<< HEAD
    valid_intents  = set(incoming.valid_intents) if incoming and incoming.valid_intents else DEFAULT_VALID_INTENTS
=======
    valid_intents  = set(incoming.valid_intents) if (incoming and incoming.valid_intents) else DEFAULT_VALID_INTENTS
>>>>>>> 2ded835 (fix: resolve import error for DEFAULT_INTENT_MIN_CONFIDENCE and update prompt/session/adapter logic)
    raw = ""
    try:
        messages: List[ChatCompletionMessageParam] = [{"role": "system", "content": system_prompt}]
        if session_history:
            messages.extend(session_history)
        messages.append({"role": "user", "content": customer_message})

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=60, temperature=0,
                messages=messages,
            )
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Empty or None response content")
        raw    = content.strip()
        parsed = json.loads(raw)

        intent = str(parsed.get("intent", "UNKNOWN")).upper()
        conf   = float(parsed.get("confidence_score", 0.0))
        conf   = max(0.0, min(1.0, conf))

        _min_conf = getattr(incoming, "intent_min_confidence", None) or DEFAULT_INTENT_MIN_CONFIDENCE
        if intent not in valid_intents or conf < _min_conf:
            intent, conf = "UNKNOWN", 0.0

        print(f"[INTENT ROUTER] '{customer_message[:60]}' => {intent} ({conf:.2f})")
        return IntentResult(intent=intent, confidence_score=conf, raw_text=customer_message)

    except json.JSONDecodeError as e:
        print(f"[INTENT ROUTER] JSON parse error: {e} | raw='{raw}'")
    except RuntimeError:
        raise  # prompt missing — re-raise so it's visible
    except Exception as e:
        print(f"[INTENT ROUTER ERROR] {e}")

    return IntentResult(intent="UNKNOWN", confidence_score=0.0, raw_text=customer_message)


# ══════════════════════════════════════════════════════════════════════════════
# GREETING HANDLER
# ══════════════════════════════════════════════════════════════════════════════

def _get_time_greeting(timezone_str: str) -> tuple:
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(timezone_str or "UTC")
    except Exception:
        tz = timezone.utc
    hour = datetime.now(tz).hour
    if hour < 12:   return "morning",   "Good morning"
    if hour < 17:   return "afternoon", "Good afternoon"
    return "evening", "Good evening"


async def handle_greeting(incoming: IncomingMessage) -> str:
    time_of_day, time_greeting = _get_time_greeting(incoming.timezone)

    system_prompt = get_prompt(
        incoming, "greeting_system_prompt",
        time_of_day   = time_of_day,
        time_greeting = time_greeting,
        sender_name   = incoming.sender_name,
        biz_name      = incoming.biz_name,
    )

    try:
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=200, temperature=0.7,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": f"[SERVER_TIME: {time_of_day}]\n{incoming.text}"},
                ],
            )
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Empty or None response content")
        raw    = content.strip()
        parsed = json.loads(raw)
        reply  = parsed.get("reply", "")
        if not reply:
            raise ValueError("Empty reply in greeting response")
        print(f"[GREETING] type={parsed.get('type')} time={time_of_day}")
        return reply
    except RuntimeError:
        raise
    except Exception as e:
        print(f"[GREETING] GPT failed: {e}")
        raise RuntimeError(f"[GREETING] Failed to generate reply: {e}")


async def handle_escalation(incoming: IncomingMessage) -> str:
    system_prompt = get_prompt(
        incoming, "escalation_prompt",
        sender_name = incoming.sender_name,
        biz_name    = incoming.biz_name,
    )
    try:
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=150, temperature=0.7,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": incoming.text},
                ],
            )
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Empty or None response content")
        return content.strip()
    except RuntimeError:
        raise
    except Exception as e:
        print(f"[ESCALATION] GPT failed: {e}")
        raise RuntimeError(f"[ESCALATION] Failed to generate reply: {e}")


async def handle_unknown(incoming: IncomingMessage) -> str:
    system_prompt = get_prompt(
        incoming, "unknown_prompt",
        sender_name = incoming.sender_name,
        biz_name    = incoming.biz_name,
    )
    try:
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=150, temperature=0.7,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": incoming.text},
                ],
            )
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Empty or None response content")
        return content.strip()
    except RuntimeError:
        raise
    except Exception as e:
<<<<<<< HEAD
        raise RuntimeError(f"[UNKNOWN] Failed to generate reply: {e}")
=======
        raise RuntimeError(f"[UNKNOWN] Failed to generate reply: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# ENTITY EXTRACTOR
# ══════════════════════════════════════════════════════════════════════════════

NEW_ORDER_TRIGGERS = [
    "i want to order", "i want to place", "place an order",
    "new order", "i want to buy", "i need to order", "can i order",
]

def _get_relevant_history(session_history: List[Any], current_message: str) -> List[Any]:
    if not session_history:
        return []
    if any(t in current_message.lower() for t in NEW_ORDER_TRIGGERS):
        return []
    last_idx = -1
    for i, msg in enumerate(session_history):
        if msg.get("role") == "user" and any(t in msg.get("content","").lower() for t in NEW_ORDER_TRIGGERS):
            last_idx = i
    if last_idx >= 0:
        return session_history[last_idx:]
    return session_history[-6:] if len(session_history) > 6 else session_history


def _default_delivery_date() -> str:
    ist = timezone(timedelta(hours=5, minutes=30))
    return (datetime.now(ist) + timedelta(days=5)).strftime("%Y-%m-%d")


async def extract_entities(
    customer_message: str,
    incoming: IncomingMessage,
    session_history:  Optional[List[Any]] = None,
    force_new_order:  bool = False,
    cached_items:     Optional[List[Any]] = None,
) -> EntityResult:
    """
    Extracts products and quantities.
    Uses entity_system_prompt from DB (via incoming object).
    Raises RuntimeError if prompt not set in DB.
    """
    raw = ""
    try:
        relevant_history = [] if force_new_order else _get_relevant_history(
            session_history or [], customer_message
        )

        base_prompt = get_prompt(incoming, "entity_system_prompt")

        # Append pending items context if multi-product workflow
        if cached_items:
            pending = "\n".join([
                f"  - {i.product_name or 'Unknown'} (qty: {i.quantity_value or 'not specified'})"
                for i in cached_items
            ])
            system_prompt = base_prompt + f"""

IMPORTANT — Products pending in this order:
{pending}

If customer gives a quantity without specifying products, extract quantity for EACH pending product.
Example:
  Pending: [Aeris Gate Light, Villa Gate Light]
  Customer: "I want 2 units"
  → [{{"product_name": "Aeris Gate Light", "quantity_value": 2, "quantity_unit": "units"}},
     {{"product_name": "Villa Gate Light",  "quantity_value": 2, "quantity_unit": "units"}}]"""
        else:
            system_prompt = base_prompt

        messages: List[ChatCompletionMessageParam] = [{"role": "system", "content": system_prompt}]
        if relevant_history:
            messages.extend(relevant_history)
        messages.append({"role": "user", "content": f"[tenant: {incoming.tenant_id}]\n{customer_message}"})

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=500, temperature=0,
                messages=messages,
            )
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Empty or None response content")
        raw    = content.strip()
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            parsed = [parsed]

        items = []
        for p in parsed:
            qty = p.get("quantity_value")
            if qty is not None:
                try:    qty = int(qty)
                except: qty = None
            # Keep product name exactly as customer said — no SKU filtering
            items.append(OrderItem(
                product_name   = p.get("product_name") or None,
                quantity_value = qty,
                quantity_unit  = p.get("quantity_unit"),
            ))

        if not items:
            items = [OrderItem(None, None, None)]

        missing = []
        for i, item in enumerate(items):
            prefix = f"item_{i+1}_" if len(items) > 1 else ""
            if not item.product_name:       missing.append(f"{prefix}product_name")
            if item.quantity_value is None: missing.append(f"{prefix}quantity")

        print(f"[ENTITY] items={len(items)} missing={missing}")
        return EntityResult(
            items=items, delivery_date=_default_delivery_date(),
            invoice_number=None, payment_reference=None,
            missing_entities=missing, raw_text=customer_message,
            tenant_id=incoming.tenant_id,
        )

    except RuntimeError:
        raise
    except json.JSONDecodeError as e:
        print(f"[ENTITY] JSON parse error: {e} | raw='{raw}'")
    except Exception as e:
        print(f"[ENTITY ERROR] {e}")

    return EntityResult(
        items=[OrderItem(None, None, None)], delivery_date=_default_delivery_date(),
        invoice_number=None, payment_reference=None,
        missing_entities=["product_name", "quantity"], raw_text=customer_message,
        tenant_id=incoming.tenant_id,
    )
>>>>>>> 2ded835 (fix: resolve import error for DEFAULT_INTENT_MIN_CONFIDENCE and update prompt/session/adapter logic)
