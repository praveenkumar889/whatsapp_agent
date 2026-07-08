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

SYSTEM_PROMPT = """You are an expert conversational Intent Classifier and Slot Extractor for an electrical & lighting catalog agent.
Analyze the user's query and conversation context, and output ONLY a valid JSON object matching this schema:

{
  "intent": "browse_category" | "find_product" | "get_product_info" | "check_policy" | "get_advice" | "unknown",
  "category_keywords": ["..."],
  "feature_keywords": ["..."],
  "product_name": "string or null",
  "filters": {
    "category": "string or null",
    "application": "string or null",
    "brand": "string or null"
  },
  "preferences": {
    "max_price": number or null
  }
}

Definitions:
- browse_category: User asks to see collections or broad categories (e.g. "show indoor lights", "what outdoor categories do you have?").
- find_product: User asks for products matching features/use case/name or comparing products (e.g. "compare athena and oxana", "solar gate lights under 1500").
- get_product_info: User asks for specific details, price, or specs of a known product.
- check_policy: User asks about warranty, returns, delivery, shipping, company info.
- get_advice: User asks for recommendation or technical guidance.
- unknown: Completely irrelevant greeting or random message.

Rules:
- Output ONLY pure JSON. No markdown backticks, no explanations.
- If the user asks for a broad group like "indoor lights", "outdoor lights", or "solar lights", set "intent": "browse_category", set "category_keywords" to the broad term (e.g. ["indoor"]), and leave "filters.category" as null so the customer is shown the collection list.
"""

async def classify_user_intent_client_side(
    query: str,
    history_context: str = "",
    taxonomy_hints: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Classify user query on the WhatsApp agent side to skip server-side classification.
    """
    def _call_llm():
        prompt = f"User Query: {query}"
        if history_context:
            prompt = f"History:\n{history_context}\n\nQuery: {query}"
        tax_str = ""
        if taxonomy_hints:
            tax_str = f"\n\nCandidate Database Taxonomy Tags:\n{json.dumps(taxonomy_hints)}"
        
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + tax_str},
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
