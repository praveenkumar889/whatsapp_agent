# ai/request_profiler.py — Per-request latency breakdown by category
#
# ROLE:
#   Answers "for THIS one request, how much time went to DB calls vs LLM
#   calls vs GraphRAG vs the dispatch stage?" — a category breakdown for a
#   single request, distinct from ai/perf_metrics.py (which aggregates
#   per-prompt-name latency across many requests over time).
#
# HOW IT'S WIRED (all additive — no call site's logic changes):
#   - db/db_utils.py's run_sync() reports into the "db" category (single
#     choke point — every Supabase call in the codebase goes through it).
#   - The AzureOpenAI client instances outside ai/graphrag_handler.py have
#     their .chat.completions.create method wrapped once at import time via
#     wrap_llm_client() — reports into the "llm" category.
#   - pipeline/router.py's two call_graphrag_api() call sites are timed
#     from the caller side into the "graphrag" category — GraphRAG's own
#     internals are deliberately left unmodified/unmeasured-in-detail (any
#     LLM/DB work it does internally is folded into this one wall-clock
#     span, not decomposed) per the standing instruction not to touch
#     GraphRAG.
#   - main.py's run_pipeline() resets the profile at the start of each
#     request and times the dispatch() call into the "dispatch" category.
#
# Uses contextvars so concurrent requests (different asyncio tasks) don't
# clobber each other's totals. asyncio.to_thread (used by db_utils.run_sync)
# already copies the calling context into its worker thread, so db_ms
# attributes correctly with no extra work. run_in_executor does NOT do this
# (verified: a bare ContextVar.get() inside a run_in_executor callable sees
# the default, not the caller's value) — and every LLM call in this codebase
# uses run_in_executor(None, lambda: ...), never to_thread. _patch_run_in_executor()
# below applies the identical copy-context wrapping asyncio.to_thread uses
# internally, once, process-wide, so llm_ms attributes correctly too.

from __future__ import annotations

import asyncio
import contextvars
import time
from contextvars import ContextVar
from typing import Optional

_profile: ContextVar[Optional[dict]] = ContextVar("_profile", default=None)

_run_in_executor_patched = False


def _patch_run_in_executor() -> None:
    """
    Makes run_in_executor propagate the calling context into its worker
    thread, matching asyncio.to_thread's existing behavior. Idempotent —
    safe to call more than once (only patches on the first call). Verified
    to preserve positional args, exceptions, and custom executors.
    """
    global _run_in_executor_patched
    if _run_in_executor_patched:
        return
    _run_in_executor_patched = True

    _original = asyncio.BaseEventLoop.run_in_executor

    def _context_preserving_run_in_executor(self, executor, func, *args):
        ctx = contextvars.copy_context()
        return _original(self, executor, ctx.run, func, *args)

    asyncio.BaseEventLoop.run_in_executor = _context_preserving_run_in_executor


_patch_run_in_executor()


def start() -> None:
    """Call once at the top of a request to reset this request's totals."""
    _profile.set({"db_ms": 0.0, "llm_ms": 0.0, "graphrag_ms": 0.0, "dispatch_ms": 0.0,
                  "db_calls": 0, "llm_calls": 0, "graphrag_calls": 0})


def add(category: str, ms: float) -> None:
    """Adds elapsed time to the current request's running total for category."""
    p = _profile.get()
    if p is None:
        return  # profiling not active (e.g. a script/test not using run_pipeline) — no-op
    p[f"{category}_ms"] = p.get(f"{category}_ms", 0.0) + ms
    p[f"{category}_calls"] = p.get(f"{category}_calls", 0) + 1


def snapshot() -> dict:
    """Returns the current request's accumulated totals (rounded, ms)."""
    p = _profile.get() or {}
    return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in p.items()}


def wrap_llm_client(client) -> None:
    """
    Wraps client.chat.completions.create with timing that reports into the
    "llm" category. Call once, right after constructing a module-level
    AzureOpenAI client — idempotent-safe to call multiple times on the same
    client (re-wrapping just adds a redundant layer, doesn't break).
    """
    original_create = client.chat.completions.create

    def _timed_create(*args, **kwargs):
        _t0 = time.monotonic()
        try:
            return original_create(*args, **kwargs)
        finally:
            add("llm", (time.monotonic() - _t0) * 1000)

    client.chat.completions.create = _timed_create
