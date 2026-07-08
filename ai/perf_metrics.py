# ai/perf_metrics.py — LLM prompt-call latency instrumentation, tenant-aware
#
# ROLE:
#   Answers "which prompt calls are contributing to response latency, and
#   for which tenants?" — aggregated, not just per-call print statements
#   that require manually eyeballing a wall of [TIMING] logs.
#
# WHY IN-PROCESS, NOT SUPABASE:
#   Same reasoning as ai/memory_metrics.py — the ai_metrics table +
#   AIOrchestrator.finalize() already exist for this, but AIOrchestrator
#   isn't wired into the live pipeline (main.py's run_pipeline() bypasses
#   it entirely). This gives real numbers today via get_snapshot() without
#   inventing a second persistence mechanism to reconcile with ai_metrics
#   later, once that integration happens.
#
# WHAT'S CAPTURED vs. WHAT'S NOT:
#   Captured: prompt_name, tenant_id, workflow, latency_ms.
#   NOT captured: tokens, cost, model, cache_hit. Adding those honestly
#   requires reading them off the actual LLM response object (r.usage.*,
#   r.model, etc.) at each call site — this module can aggregate them once
#   they're passed in, but it can't invent them from nothing. Extend
#   record()'s signature when a call site is updated to pass them.
#
# USAGE:
#   import time
#   from ai import perf_metrics
#
#   _t0 = time.monotonic()
#   response = await ...llm call...
#   perf_metrics.record("pf_data_extraction_prompt", (time.monotonic() - _t0) * 1000,
#                        tenant_id=incoming.tenant_id, workflow="BROWSING")

from __future__ import annotations

import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Optional

_MAX_EVENTS = 5000
_events: list = []  # bounded list of raw event dicts — source of truth for all aggregation


def record(
    prompt_name: str,
    latency_ms:  float,
    tenant_id:   Optional[str] = None,
    workflow:    Optional[str] = None,
) -> None:
    _events.append({
        "prompt_name": prompt_name,
        "tenant_id":   tenant_id,
        "workflow":    workflow,
        "latency_ms":  latency_ms,
        "ts":          time.time(),
    })
    if len(_events) > _MAX_EVENTS:
        del _events[: len(_events) - _MAX_EVENTS]


@asynccontextmanager
async def timed(prompt_name: str, tenant_id: Optional[str] = None, workflow: Optional[str] = None):
    """Usage: async with perf_metrics.timed('some_prompt', tenant_id=incoming.tenant_id): await llm_call()"""
    _t0 = time.monotonic()
    try:
        yield
    finally:
        record(prompt_name, (time.monotonic() - _t0) * 1000, tenant_id=tenant_id, workflow=workflow)


def _aggregate_by(dimension: str) -> list:
    """
    Groups all recorded events by the given field name (e.g. "prompt_name",
    "tenant_id", "workflow") and returns per-group stats sorted by total
    time spent, descending — the biggest contributors float to the top.
    Events missing that dimension (None) are grouped under "unknown".
    """
    groups: dict = defaultdict(list)
    for e in _events:
        key = e.get(dimension) or "unknown"
        groups[key].append(e["latency_ms"])

    rows = []
    for key, latencies in groups.items():
        total = sum(latencies)
        rows.append({
            dimension:  key,
            "calls":    len(latencies),
            "total_ms": round(total, 1),
            "avg_ms":   round(total / len(latencies), 1) if latencies else 0,
            "max_ms":   round(max(latencies), 1) if latencies else 0,
        })
    rows.sort(key=lambda r: r["total_ms"], reverse=True)
    return rows


def get_snapshot() -> dict:
    """Per-prompt aggregate stats — 'which prompt causes the most total time?'"""
    return {"prompts": _aggregate_by("prompt_name")}


def get_tenant_snapshot() -> dict:
    """Per-tenant aggregate stats — 'which tenant generates the highest latency?'"""
    return {"tenants": _aggregate_by("tenant_id")}


def get_workflow_snapshot() -> dict:
    """Per-workflow aggregate stats."""
    return {"workflows": _aggregate_by("workflow")}


def get_full_snapshot() -> dict:
    """All three views in one call, for a debug endpoint or periodic log line."""
    return {
        "by_prompt":   _aggregate_by("prompt_name"),
        "by_tenant":   _aggregate_by("tenant_id"),
        "by_workflow": _aggregate_by("workflow"),
        "total_events": len(_events),
    }


def reset() -> None:
    """For tests only."""
    _events.clear()
