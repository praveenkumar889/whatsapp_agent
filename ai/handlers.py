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
from datetime import datetime, timezone
from typing import List, Optional, Any

from openai import AzureOpenAI
from openai.types.chat import ChatCompletionMessageParam
from config import AZURE_AI_ENDPOINT, AZURE_AI_API_KEY, AZURE_OPENAI_DEPLOYMENT, AZURE_AI_API_VERSION
from models.schemas import IntentResult, RoutingDecision, IncomingMessage
from db.prompt_store import get_prompt

_client = AzureOpenAI(
    azure_endpoint = AZURE_AI_ENDPOINT,
    api_key        = AZURE_AI_API_KEY,
    api_version    = AZURE_AI_API_VERSION,
    timeout        = 30.0,
    max_retries    = 0,
)

DEFAULT_VALID_INTENTS = {"WORKFLOW_ACTION", "FAQ_KNOWLEDGE", "HUMAN_ESCALATION", "GREETING", "UNKNOWN"}
DEFAULT_INTENT_MIN_CONFIDENCE = 0.50  # default — see incoming.intent_min_confidence for tenant override


# ══════════════════════════════════════════════════════════════════════════════
# INTENT ROUTER
# ══════════════════════════════════════════════════════════════════════════════

async def classify_intent(
    customer_message: str,
    session_history:  Optional[List[ChatCompletionMessageParam]] = None,
    incoming: Optional[IncomingMessage] = None,
) -> IntentResult:
    """
    Classifies intent using tenant's intent_system_prompt from DB.
    Raises RuntimeError if prompt not set in DB.
    """
    system_prompt  = get_prompt(incoming, "intent_system_prompt")
    valid_intents  = set(incoming.valid_intents) if incoming and incoming.valid_intents else DEFAULT_VALID_INTENTS
    raw = ""
    try:
        messages: List[ChatCompletionMessageParam] = [{"role": "system", "content": system_prompt}]
        if session_history:
            messages.extend(session_history)
        messages.append({"role": "user", "content": customer_message})

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT, max_tokens=150, temperature=0,
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

        routing = None
        if "operation" in parsed:
            routing = RoutingDecision(
                operation=str(parsed.get("operation", "OTHER")).upper(),
                needs_graphrag=bool(parsed.get("needs_graphrag", False)),
                needs_customer_context=bool(parsed.get("needs_customer_context", False) or parsed.get("needs_memory", False)),
                needs_workflow_state=bool(parsed.get("needs_workflow_state", False)),
                needs_product_context=bool(parsed.get("needs_product_context", False)),
                needs_customer_history=bool(parsed.get("needs_customer_history", False)),
                knowledge_domain=parsed.get("knowledge_domain", "product"),
                requested_knowledge_field=parsed.get("requested_knowledge_field"),
            )


        _min_conf = getattr(incoming, "intent_min_confidence", None) or DEFAULT_INTENT_MIN_CONFIDENCE
        if intent not in valid_intents or conf < _min_conf:
            intent, conf = "UNKNOWN", 0.0

        print(f"[INTENT ROUTER] '{customer_message[:60]}' => {intent} ({conf:.2f}) routing={routing}")
        return IntentResult(intent=intent, confidence_score=conf, raw_text=customer_message,
                             routing=routing)

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
        raise RuntimeError(f"[UNKNOWN] Failed to generate reply: {e}")

