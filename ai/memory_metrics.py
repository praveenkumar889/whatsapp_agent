    # ai/memory_metrics.py — Lightweight observability for the memory subsystem
#
# ROLE:
#   Tracks the counters your review specifically asked for:
#     MemoryPolicy:         retrievals_allowed, retrievals_skipped, skip_reason
#     MemoryIntentDetector: rule_hit, llm_hit, (false_positive/false_negative
#                           require labeled ground truth — see note below)
#     MemoryManager:        retrieval_latency_ms, retrieval_hits, retrieval_misses,
#                           memory_types_returned
#
# WHY IN-PROCESS, NOT SUPABASE:
#   The codebase already has an ai_metrics table + AIOrchestrator.finalize()
#   built for exactly this kind of telemetry — but AIOrchestrator isn't
#   wired into the live pipeline yet (main.py's run_pipeline() still calls
#   setup_pipeline()/dispatch() directly, bypassing AIOrchestrator entirely).
#   Wiring these counters into ai_metrics belongs in that integration, not
#   bolted on here as a parallel path to Supabase. Until then, this gives
#   you real numbers to look at (via get_snapshot(), e.g. from a debug
#   endpoint or a periodic log line) without inventing a second persistence
#   mechanism that would need reconciling with ai_metrics later.
#
# WHAT'S NOT HERE:
#   false_positive / false_negative need labeled ground truth (a human or a
#   downstream signal confirming whether a retrieval actually helped) — pure
#   counters can't produce that on their own. Wire in a feedback signal
#   (e.g. "did the LLM's reply actually reference the retrieved memory?")
#   before trying to populate those two.
#
# THREAD/ASYNC SAFETY:
#   Plain dict increments. Under CPython's GIL this is safe enough for
#   approximate counters in a single-process deployment; it is NOT safe
#   across multiple worker processes (each gets its own counters). If you
#   run multiple uvicorn workers, aggregate via ai_metrics/Supabase instead
#   once that integration exists — this module's snapshot is per-process.

from __future__ import annotations

import time
from collections import defaultdict
from typing import Optional

_counters: dict = defaultdict(int)
_latencies_ms: list = []  # retrieval latencies, capped below
_MAX_LATENCY_SAMPLES = 500


def _bump(key: str, n: int = 1) -> None:
    _counters[key] += n


# ── MemoryPolicy ───────────────────────────────────────────────────────────

def record_policy_decision(retrieve: bool, reason: str) -> None:
    if retrieve:
        _bump("policy.retrievals_allowed")
    else:
        _bump("policy.retrievals_skipped")
        _bump(f"policy.skip_reason.{reason}")


# ── MemoryIntentDetector ─────────────────────────────────────────────────────

def record_detector_result(source: str, worthy: bool) -> None:
    """source: 'rule' or 'llm'."""
    _bump(f"detector.{source}_hit" if worthy else f"detector.{source}_miss")


# ── MemoryManager ──────────────────────────────────────────────────────────

def record_manager_search(latency_ms: float, hits: int, types_returned: Optional[list] = None) -> None:
    _bump("manager.searches")
    _bump("manager.retrieval_hits", 1 if hits > 0 else 0)
    _bump("manager.retrieval_misses", 0 if hits > 0 else 1)
    _latencies_ms.append(latency_ms)
    if len(_latencies_ms) > _MAX_LATENCY_SAMPLES:
        del _latencies_ms[0]
    for t in (types_returned or []):
        _bump(f"manager.memory_types_returned.{t}")


def record_manager_save(memory_type: str) -> None:
    _bump("manager.saves")
    _bump(f"manager.saves.{memory_type}")


# ── Snapshot ───────────────────────────────────────────────────────────────

def get_snapshot() -> dict:
    """
    Returns a plain dict snapshot of all counters plus basic latency stats.
    Safe to call from a debug endpoint or a periodic log line.
    """
    avg_latency = round(sum(_latencies_ms) / len(_latencies_ms), 1) if _latencies_ms else None
    p95_latency = None
    if _latencies_ms:
        _sorted = sorted(_latencies_ms)
        p95_latency = round(_sorted[int(len(_sorted) * 0.95) - 1], 1)
    return {
        "counters":            dict(_counters),
        "avg_search_latency_ms": avg_latency,
        "p95_search_latency_ms": p95_latency,
        "latency_samples":     len(_latencies_ms),
    }


def reset() -> None:
    """For tests only — clears all counters."""
    _counters.clear()
    _latencies_ms.clear()