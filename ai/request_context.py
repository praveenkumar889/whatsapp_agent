# ai/request_context.py — AIRequestContext
#
# ROLE:
#   Single object that flows through the entire pipeline per request.
#   Replaces the pattern of passing incoming + ctx + pricing + workflow + tenant
#   as separate arguments to every handler.
#
# BEFORE:
#   await negotiator.handle(incoming, product_name, price_num, regular_price,
#                           graphrag_discount_pct, session_history,
#                           negotiation_state, global_offers, product_data)
#
# AFTER:
#   await negotiator.handle(arc)    ← everything in one object
#
# LIFECYCLE:
#   1. Created at request entry in main.py
#   2. Enriched by ContextBuilder (adds llm_context)
#   3. Enriched by PricingResult (adds pricing)
#   4. Passed to handlers — they read what they need
#   5. Handlers update arc.updates → flushed to DB/Mem0 at end of pipeline

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class PromptContext:
    """
    Assembled semantic context for LLM prompt injection.
    All fields are plain strings, safe to inject into {variable} slots.
    """
    product_context:      str = ""
    customer_preferences: str = ""
    negotiation_profile:  str = ""
    workflow_context:     str = ""
    conversation_summary: str = ""
    customer_context:     str = ""  # Added for unified customer context
    active_product_session: bool = False
    resolved_product:     Optional[str] = None
    knowledge_state:      dict = field(default_factory=dict)
    knowledge_context:    dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "product_context":      self.product_context,
            "customer_preferences": self.customer_preferences,
            "negotiation_profile":  self.negotiation_profile,
            "workflow_context":     self.workflow_context,
            "conversation_summary": self.conversation_summary,
            "customer_context":     self.customer_context,
            "active_product_session": self.active_product_session,
            "resolved_product":     self.resolved_product,
            "knowledge_state":      self.knowledge_state,
            "knowledge_context":    self.knowledge_context,
        }

    def has_content(self) -> bool:
        # Check standard context strings or if knowledge has been loaded
        return any(
            isinstance(v, str) and bool(v) for v in [
                self.product_context,
                self.customer_preferences,
                self.negotiation_profile,
                self.workflow_context,
                self.conversation_summary,
                self.customer_context
            ]
        ) or bool(self.knowledge_state.get("available"))



@dataclass
class AIRequestContext:
    """
    Single context object flowing through the entire pipeline per request.

    Owns:
      - incoming:      original WhatsApp message (immutable — don't add state here)
      - result:        intent classification result
      - session_history: recent message turns from Postgres
      - llm_context:   assembled context for prompts (set by ContextBuilder)
      - neg_state:     negotiation state dict loaded once from DB
      - pricing:       PricingResult if active order (set by pricing engine)
      - _updates:      deferred writes flushed at end of pipeline
      - resolved_product: active resolved product (cascade/waterfall resolved)
      - customer_context: unified customer history data formatting block
    """

    # ── Core request data ──────────────────────────────────────────────────
    incoming:        Any                      # IncomingMessage (typed as Any to avoid circular)
    result:          Any = None               # IntentResult
    session_history: list = field(default_factory=list)

    # ── Context assembled by ContextBuilder ───────────────────────────────
    llm_context:     PromptContext = field(default_factory=PromptContext)

    # ── State loaded once from DB ─────────────────────────────────────────
    neg_state:       Optional[dict] = None    # negotiation_state from DB
    workflow_state:  Optional[dict] = None    # workflow_sessions from DB

    # ── Pricing ───────────────────────────────────────────────────────────
    pricing:         Optional[Any] = None     # PricingResult if active

    # ── Centralized Context envelope ──────────────────────────────────────
    resolved_product: Optional[str] = None
    customer_context: str = ""

    # ── Deferred writes (flushed at pipeline end) ─────────────────────────
    _updates:        dict = field(default_factory=dict)

    # ── Convenience accessors ─────────────────────────────────────────────

    @property
    def tenant_id(self) -> str:
        return self.incoming.tenant_id

    @property
    def session_id(self) -> str:
        return self.incoming.session_id

    @property
    def sender_name(self) -> str:
        return self.incoming.sender_name

    @property
    def text(self) -> str:
        return self.incoming.text

    @property
    def intent(self) -> str:
        return self.result.intent if self.result else "UNKNOWN"

    @property
    def workflow(self) -> str:
        """
        Derives current workflow phase from loaded state.
        Used by ContextBuilder to select the right memory strategy.
        """
        if self.neg_state and self.neg_state.get("rounds", 0) > 0:
            if self.neg_state.get("awaiting_invoice_confirmation"):
                return "CONFIRMING"
            if self.neg_state.get("counter_offer_presented"):
                return "COUNTER_PRESENTED"
            return "NEGOTIATING"
        if self.neg_state and self.neg_state.get("quantity"):
            return "ORDERING"
        return "BROWSING"

    @property
    def current_product(self) -> str:
        """Current product being discussed, from neg_state or workflow_state."""
        if self.neg_state:
            return str(self.neg_state.get("product_name", "") or "")
        if self.workflow_state:
            return str(self.workflow_state.get("product_name", "") or "")
        return ""

    def queue_update(self, key: str, value: Any) -> None:
        """Queue a deferred write to be flushed at end of pipeline."""
        self._updates[key] = value

    def get_prompt_vars(self) -> dict:
        """Returns llm_context as prompt variables, merged with request basics."""
        return {
            **self.llm_context.to_dict(),
            "sender_name": self.sender_name,
            "biz_name":    getattr(self.incoming, "biz_name", ""),
        }