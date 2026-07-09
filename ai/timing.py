# ai/timing.py — Reusable performance timing wrappers and decorators
import time
import asyncio
import functools
from typing import Callable, Any, Optional
from contextlib import contextmanager, asynccontextmanager


def log_timing(label: Optional[str] = None, log_prefix: str = "[TIMING]"):
    """
    Reusable decorator for both async and sync functions to measure and log execution latency.

    Usage:
        @log_timing("Client Intent Classification")
        async def classify_user_intent_client_side(...): ...

        @log_timing()  # automatically uses the function name
        def sync_helper(...): ...
    """
    def decorator(fn: Callable) -> Callable:
        name = label or fn.__name__

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs) -> Any:
                t0 = time.perf_counter()
                try:
                    return await fn(*args, **kwargs)
                finally:
                    elapsed = time.perf_counter() - t0
                    print(f"{log_prefix} {name}: {elapsed:.3f}s")
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs) -> Any:
                t0 = time.perf_counter()
                try:
                    return fn(*args, **kwargs)
                finally:
                    elapsed = time.perf_counter() - t0
                    print(f"{log_prefix} {name}: {elapsed:.3f}s")
            return sync_wrapper
    return decorator


@asynccontextmanager
async def async_time_it(label: str, log_prefix: str = "[TIMING]"):
    """
    Async context manager to time a block of code.

    Usage:
        async with async_time_it("Querying MCP Catalog"):
            res = await query_mcp_catalog(...)
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - t0
        print(f"{log_prefix} {label}: {elapsed:.3f}s")


@contextmanager
def time_it(label: str, log_prefix: str = "[TIMING]"):
    """
    Sync context manager to time a block of code.

    Usage:
        with time_it("Database query"):
            ...
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - t0
        print(f"{log_prefix} {label}: {elapsed:.3f}s")
