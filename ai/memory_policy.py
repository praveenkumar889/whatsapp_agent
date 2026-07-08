# ai/memory_policy.py — MemoryPolicy: centralized Mem0 retrieval gate
#
# ROLE:
#   Single reusable decision point for "should this request query Mem0, and
#   with what parameters?". Mem0 is long-term, cross-session memory
#   (customer preferences, past purchases, successful negotiation history)
#   — it is NOT part of the critical path for every message. PostgreSQL
#   (workflow_sessions, product_cache, negotiation_state, messages) already
#   answers everything needed for the current conversation.
#
# MemoryPolicy NEVER CALLS AN LLM — read this before changing that.
#   An earlier revision of this file had a LLMDetector fallback that let
#   MemoryPolicy make its own LLM call when no upstream classification was
#   available. That was a mistake: it hid an AI call inside what should be
#   a pure decision layer. The correct shape is:
#
#       Unified classifier (LLM call already happening upstream, e.g.
#       product_followup.py's pf_data_extraction_prompt parse)
#                       │
#                       ▼  (needs_long_term_memory: True/False/None)
#                 MemoryPolicy.evaluate()   ← pure function, zero I/O
#                       │
#                       ▼  (if decision.retrieve)
#                 MemoryManager.search()    ← the only thing that talks to Mem0
#
#   If the upstream classifier hasn't produced a signal yet (None — e.g. a
#   tenant hasn't updated their pf_data_extraction_prompt to include
#   needs_long_term_memory), MemoryPolicy does NOT go fetch one itself. It
#   fails safe: no retrieval, log it clearly, move on. The fix for that
#   tenant is updating their classifier prompt (a DB edit), not adding a
#   second, hidden classification call inside the policy.
#
# MULTI-TENANT CORRECTNESS:
#   No regex anywhere in this module (or anywhere in the app's business
#   logic) — a hardcoded English pattern list silently fails for any tenant
#   whose customers write differently, in any language, in any domain.
#   All classification lives in tenant-configurable DB prompts, upstream of
#   this file.
#
# RICH DECISION, NOT A BOOLEAN:
#   evaluate() returns a MemoryDecision (retrieve, types, max_results,
#   reason) instead of True/False — a recommendation ask needs preferences
#   + purchase history; a product follow-up (when it does qualify) only
#   needs product_context. should_retrieve() remains as a thin bool-only
#   wrapper for callers that don't need the extra detail.
#
# CONTEXT OBJECT:
#   Takes one object exposing `.text`, `.intent`, `.workflow`, and
#   `.needs_long_term_memory`. ai/request_context.py's AIRequestContext is
#   the intended long-term home for this shape (see module docstring in
#   that file once it grows a needs_long_term_memory field) — it already
#   satisfies `.text`/`.intent`/`.workflow` structurally today. MemoryRequest
#   below is the interim DTO for call sites that don't build a full
#   AIRequestContext yet (see "WHAT THIS FILE DOES NOT DO YET" below).
#
# WHAT THIS FILE DOES NOT DO YET (honest, not silently skipped):
#   1. A single conversation-wide classifier upstream of every module
#      (negotiator, GraphRAG, memory, invoice), all sharing one
#      AIRequestContext. Today the classification this file depends on is
#      produced inside product_followup.py's own parse call, and
#      AIRequestContext isn't threaded through main.py → router.py →
#      graphrag_handler.py → product_followup.py's call chain at all (that
#      chain currently passes `incoming` + raw locals, not an arc — see
#      ai/orchestrator.py's module docstring for the same gap noted
#      elsewhere: AIOrchestrator/AIRequestContext exist but aren't wired
#      into the live pipeline). Threading a shared context object through
#      that whole chain is a real, valuable next milestone — it is a
#      multi-file signature change across the live request path, not
#      something to bundle into a policy-layer fix. Doing it carelessly
#      risks regressing a heavily-tested flow. Scope it as its own piece
#      of work.
#   2. memory_strategy-driven workflow deny-list (replacing
#      _DENY_WORKFLOWS below with DB rows) and a TenantCapabilityRegistry
#      (feature flags per tenant) are both real, sensible next steps, not
#      implemented here — same reasoning as always: repurposing/extending
#      a live table's semantics, or introducing a new one, deserves its
#      own migration and rollout plan, not a drive-by addition to this file.
#   3. Renaming pf_data_extraction_prompt to something more general
#      (e.g. interaction_classifier_prompt) is a good idea once it's
#      actually promoted to a conversation-wide classifier (see #1) — doing
#      it before that would just rename the same narrowly-scoped prompt.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from ai import memory_metrics


@runtime_checkable
class MemoryContext(Protocol):
    """
    Structural type — anything with these attributes works, including
    ai.request_context.AIRequestContext (once it carries
    needs_long_term_memory) and the MemoryRequest below.
    """
    text:                   str
    intent:                 Optional[str]
    workflow:               Optional[str]
    needs_long_term_memory: Optional[bool]


@dataclass
class MemoryRequest:
    """
    Interim context object for call sites that don't build a full
    AIRequestContext yet (see module docstring, gap #1). Structurally
    identical in shape to what AIRequestContext needs, so migrating a call
    site later is a constructor swap, not a redesign.

    `needs_long_term_memory` MUST be populated by an upstream classifier
    (e.g. product_followup.py's unified pf_data_extraction_prompt parse)
    — MemoryPolicy will not compute it itself. Leave it None only when no
    classification has happened yet; MemoryPolicy will then fail safe
    (no retrieval) rather than guess.
    """
    text:                    str
    intent:                  Optional[str] = None
    workflow:                Optional[str] = None
    tenant_id:               Optional[str] = None
    session_id:              Optional[str] = None
    current_product:         Optional[str] = None
    negotiation_active:      bool = False
    needs_long_term_memory:  Optional[bool] = None


@dataclass
class MemoryDecision:
    """
    MemoryPolicy's output. `retrieve=False` means types/max_results are
    meaningless (leave at defaults). `reason` is for observability
    (memory_metrics.record_policy_decision) — "deny_workflow",
    "allow_intent", "worthy", "not_worthy", or "no_signal" (upstream
    classifier hasn't produced needs_long_term_memory yet for this tenant).
    """
    retrieve:     bool
    types:        list = field(default_factory=list)
    max_results:  int = 3
    reason:       str = ""


class MemoryPolicy:
    """
    Single reusable Mem0 retrieval gate. Pure decision function — zero I/O,
    zero LLM calls, zero Mem0 access. Reads a signal that must already be
    computed upstream; never computes it itself.

    Usage:
        from ai.memory_policy import MemoryPolicy, MemoryRequest

        ctx = MemoryRequest(
            text=incoming.text,
            workflow="ORDERING",
            needs_long_term_memory=quick_parsed.get("needs_long_term_memory"),
        )
        decision = MemoryPolicy.evaluate(ctx)
        if decision.retrieve:
            results = await memory_manager.search(decision.types, query=..., max_results=decision.max_results)
    """

    _DENY_WORKFLOWS = {"NEGOTIATING", "COUNTER_PRESENTED", "CONFIRMING", "ORDERING"}
    _ALLOW_INTENTS = {"RECOMMENDATION", "PERSONALIZATION", "PREVIOUS_PURCHASE", "CUSTOMER_PROFILE"}

    # Which memory types to fetch depending on why retrieval was allowed.
    # Mirrors the shape memory_strategy already uses for ContextBuilder —
    # intentional, since the documented DB-driven extension (module
    # docstring, gap #2) will eventually replace these literals with rows
    # from that table.
    _TYPES_BY_INTENT = {
        "RECOMMENDATION":     (["customer_preference", "purchase_summary"], 5),
        "PERSONALIZATION":    (["customer_preference"], 3),
        "PREVIOUS_PURCHASE":  (["purchase_summary"], 5),
        "CUSTOMER_PROFILE":   (["customer_preference", "negotiation_outcome"], 5),
    }
    _DEFAULT_TYPES = (["product_context", "customer_preference", "negotiation_outcome"], 3)

    @classmethod
    def evaluate(cls, context: MemoryContext) -> MemoryDecision:
        """
        Pure function: reads context.needs_long_term_memory (must already
        be classified upstream), context.intent, context.workflow — and
        decides. No I/O, no LLM call, no fallback classification.
        """
        intent    = getattr(context, "intent", None)
        workflow  = getattr(context, "workflow", None)
        upstream  = getattr(context, "needs_long_term_memory", None)

        if upstream is None:
            # No upstream classification yet for this tenant/call site.
            # Fail safe — do NOT call an LLM here to find out. The fix is
            # updating the tenant's classifier prompt, not this file.
            decision = MemoryDecision(retrieve=False, reason="no_signal")
            memory_metrics.record_policy_decision(decision.retrieve, decision.reason)
            return decision

        worthy = bool(upstream)

        if workflow in cls._DENY_WORKFLOWS and not worthy:
            decision = MemoryDecision(retrieve=False, reason="deny_workflow")
        elif intent in cls._ALLOW_INTENTS:
            types, max_results = cls._TYPES_BY_INTENT.get(intent, cls._DEFAULT_TYPES)
            decision = MemoryDecision(retrieve=True, types=types, max_results=max_results, reason="allow_intent")
        elif worthy:
            types, max_results = cls._DEFAULT_TYPES
            decision = MemoryDecision(retrieve=True, types=types, max_results=max_results, reason="worthy")
        else:
            decision = MemoryDecision(retrieve=False, reason="not_worthy")

        memory_metrics.record_policy_decision(decision.retrieve, decision.reason)
        return decision

    @classmethod
    def should_retrieve(cls, context: MemoryContext) -> bool:
        """Thin bool-only wrapper over evaluate(), for callers that don't need types/limit."""
        return cls.evaluate(context).retrieve
