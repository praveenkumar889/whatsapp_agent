# db/db_utils.py — Shared helper for offloading blocking Supabase/CPU calls
#
# The supabase-py client is synchronous (blocking network I/O). Calling it
# directly from inside `async def` functions blocks the single asyncio event
# loop for the duration of every DB round-trip — stalling every other
# tenant's in-flight conversation, not just the one making the call.
#
# run_sync() offloads any blocking callable to a worker thread so `await`
# actually yields control back to the event loop. Works identically for
# every tenant/table/query — nothing here is tenant- or client-specific.

import asyncio
import time
from typing import Callable, TypeVar

T = TypeVar("T")


async def run_sync(fn: Callable[[], T]) -> T:
    """Runs a blocking callable (e.g. a Supabase query chain, PDF render, or
    storage upload) in a worker thread instead of on the event loop."""
    from ai.request_profiler import add as _profile_add
    _t0 = time.monotonic()
    try:
        return await asyncio.to_thread(fn)
    finally:
        # Note: this is the single choke point for every Supabase call, but
        # also for the (much rarer) PDF-render/storage-upload work in
        # utils/invoice.py — both go through run_sync. For requests that
        # don't touch invoicing (the common case), this is a clean "DB time"
        # number; for invoice-generation requests it also includes that work.
        _profile_add("db", (time.monotonic() - _t0) * 1000)


class TTLCache:
    """Generic in-memory TTL cache, keyed by string.

    Not tenant- or table-specific by design — callers build a scoped key
    (e.g. f"{tenant_id}::{key}") so one instance works dynamically for every
    tenant/table without any hardcoding. Mirrors the cache pattern already
    proven in db/prompt_store.py (5-minute TTL, same get/set/invalidate shape).
    """

    def __init__(self, ttl_seconds: float = 300):
        self._ttl_seconds = ttl_seconds
        self._store: dict = {}

    def get(self, key: str):
        entry = self._store.get(key)
        if entry and entry[1] > time.monotonic():
            return entry[0]
        return None

    def set(self, key: str, value) -> None:
        self._store[key] = (value, time.monotonic() + self._ttl_seconds)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)
