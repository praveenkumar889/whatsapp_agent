import os
import sys

# Load env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

MEM0_API_KEY = os.getenv("MEM0_API_KEY", "")
if not MEM0_API_KEY:
    print("[ERROR] MEM0_API_KEY not set in .env")
    sys.exit(1)

import mem0
print(f"[VERSION] mem0 SDK version: {mem0.__version__}")

from mem0 import MemoryClient
import inspect

client = MemoryClient(api_key=MEM0_API_KEY)

# Print search signature
sig = inspect.signature(client.search)
print(f"[SIGNATURE] client.search{sig}")

# ── Test save ────────────────────────────────────────────────────────────────
TEST_USER   = "test_user_version_check"
TEST_AGENT  = "tenant_inventaa_led_001"
TEST_MEMORY = "Lexa Eco LED Street Light - preferred by customer, IP65 rated"

print(f"\n[TEST] Saving test memory...")
try:
    client.add(
        messages = [{"role": "system", "content": f"LAST_PRODUCT: {TEST_MEMORY}"}],
        user_id  = TEST_USER,
        agent_id = TEST_AGENT,
    )
    print(f"[TEST] Save done")
except Exception as e:
    print(f"[TEST] Save failed: {e}")

import time
time.sleep(2)  # wait for indexing

# ── Test format A: top-level params ──────────────────────────────────────────
print(f"\n[TEST A] Search with top-level user_id/agent_id params...")
try:
    results_a = client.search(
        query    = "LED Street Light",
        user_id  = TEST_USER,
        agent_id = TEST_AGENT,
        limit    = 3,
    )
    count_a = len(results_a) if isinstance(results_a, list) else len(results_a.get("results", []))
    print(f"[TEST A] Results: {count_a}  (raw type: {type(results_a).__name__})")
    if count_a > 0:
        print(f"[TEST A] YES: TOP-LEVEL PARAMS WORK - use this format")
    else:
        print(f"[TEST A] 0 results with top-level params")
except Exception as e:
    print(f"[TEST A] FAILED: {e}")

# ── Test format B: filters= dict ─────────────────────────────────────────────
print(f"\n[TEST B] Search with filters={{}} dict...")
try:
    results_b = client.search(
        query   = "LED Street Light",
        filters = {"user_id": TEST_USER, "agent_id": TEST_AGENT},
        limit   = 3,
    )
    count_b = len(results_b) if isinstance(results_b, list) else len(results_b.get("results", []))
    print(f"[TEST B] Results: {count_b}  (raw type: {type(results_b).__name__})")
    if count_b > 0:
        print(f"[TEST B] YES: FILTERS DICT WORKS - use this format")
    else:
        print(f"[TEST B] 0 results with filters dict")
except Exception as e:
    print(f"[TEST B] FAILED: {e}")

print(f"\n[CONCLUSION]")
print("Use whichever format returned results above.")
print("If neither returned results, check that MEM0_API_KEY is correct.")
