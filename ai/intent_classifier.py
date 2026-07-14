# ai/intent_classifier.py — Client-side Intent Classifier & Dialog State Tracker
#
# Runs intent classification within the WhatsApp agent before invoking the MCP server,
# ensuring zero LLM intent classification overhead on the MCP server side.

import json
import logging
import asyncio
from typing import Optional, Dict, Any
from openai import AzureOpenAI
from config import (
    AZURE_AI_ENDPOINT, AZURE_AI_API_KEY,
    AZURE_OPENAI_DEPLOYMENT, AZURE_AI_API_VERSION
)

logger = logging.getLogger(__name__)

_client = AzureOpenAI(
    azure_endpoint = AZURE_AI_ENDPOINT,
    api_key        = AZURE_AI_API_KEY,
    api_version    = AZURE_AI_API_VERSION,
)

from ai.timing import log_timing

@log_timing("IntentClassifier.classify_user_intent_client_side")
async def classify_user_intent_client_side(
    query: str,
    history_context: str = "",
    taxonomy_hints: Optional[Dict[str, Any]] = None,
    incoming: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Classify user query on the WhatsApp agent side using tenant-specific prompt from Supabase to skip server-side classification.
    """
    def _call_llm():
        prompt = f"User Query: {query}"
        if history_context:
            prompt = f"History:\n{history_context}\n\nQuery: {query}"
        tax_str = ""
        if taxonomy_hints:
            tax_str = f"\n\nCandidate Database Taxonomy Tags:\n{json.dumps(taxonomy_hints)}"
        
        system_prompt = "You are an expert conversational Intent Classifier."
        if incoming:
            try:
                from db.prompt_store import get_prompt
                tenant_prompt = get_prompt(incoming, "graphrag_intent_prompt", biz_name=getattr(incoming, "biz_name", "Inventaa LED Lights"))
                if tenant_prompt:
                    system_prompt = tenant_prompt
            except Exception as e:
                logger.warning(f"[INTENT-CLIENT] Could not load tenant intent prompt from Supabase: {e}")

        messages = [
            {"role": "system", "content": system_prompt + tax_str},
            {"role": "user", "content": prompt}
        ]
        res = _client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=messages,
            temperature=0,
            max_tokens=300
        )
        return res.choices[0].message.content.strip()

    try:
        raw = await asyncio.to_thread(_call_llm)
        if raw.startswith("```json"):
            raw = raw[7:]
        if raw.endswith("```"):
            raw = raw[:-3]
        data = json.loads(raw.strip())
        return data
    except Exception as e:
        logger.warning(f"[INTENT-CLIENT] Classification failed ({e}), using fallback find_product")
        return {
            "intent": "find_product",
            "category_keywords": [],
            "feature_keywords": [],
            "product_name": query if len(query.split()) <= 3 else None,
            "filters": {},
            "preferences": {}
        }
