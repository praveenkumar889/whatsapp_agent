import os, sys, time
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

MEM0_API_KEY = os.getenv("MEM0_API_KEY", "")
if not MEM0_API_KEY:
    print("[ERROR] MEM0_API_KEY not set in .env")
    sys.exit(1)

if len(sys.argv) < 2:
    print("Usage: python verify_search_format.py <session_id> [tenant_id]")
    sys.exit(1)

SESSION_ID = sys.argv[1]
TENANT_ID  = sys.argv[2] if len(sys.argv) > 2 else "tenant_inventaa_led_001"
QUERY      = "product"

from mem0 import MemoryClient
client = MemoryClient(api_key=MEM0_API_KEY)

print("=" * 70)
print(f"Testing session_id='{SESSION_ID}'  tenant_id='{TENANT_ID}'  query='{QUERY}'")
print("=" * 70)

print("\n[TEST 0] client.get_all(user_id=...) -- sanity check")
try:
    all_mems = client.get_all(user_id=SESSION_ID)
    count0 = len(all_mems) if isinstance(all_mems, list) else len(all_mems.get("results", []))
    print(f"[TEST 0] Total memories for this user_id: {count0}")
    if count0 == 0:
        print("[TEST 0] WARNING: No memories exist for this session_id at all.")
except Exception as e:
    print(f"[TEST 0] FAILED: {e}")

time.sleep(1)

print(f"\n[TEST A] client.search(query='{QUERY}', user_id='{SESSION_ID}')")
try:
    results_a = client.search(query=QUERY, user_id=SESSION_ID, limit=10)
    list_a = results_a if isinstance(results_a, list) else results_a.get("results", [])
    print(f"[TEST A] Results: {len(list_a)}  (type: {type(results_a).__name__})")
    for r in list_a[:3]:
        print(f"    memory='{str(r.get('memory',''))[:70]}'")
except Exception as e:
    print(f"[TEST A] FAILED: {e}")
    list_a = []

time.sleep(1)

print(f"\n[TEST B] client.search(query='{QUERY}', filters={{'user_id': '{SESSION_ID}'}})")
try:
    results_b = client.search(query=QUERY, filters={"user_id": SESSION_ID}, limit=10)
    list_b = results_b if isinstance(results_b, list) else results_b.get("results", [])
    print(f"[TEST B] Results: {len(list_b)}  (type: {type(results_b).__name__})")
    for r in list_b[:3]:
        print(f"    memory='{str(r.get('memory',''))[:70]}'")
except Exception as e:
    print(f"[TEST B] FAILED: {e}")
    list_b = []

print("\n" + "=" * 70)
print("DIAGNOSIS")
print("=" * 70)
if len(list_a) > 0 and len(list_b) == 0:
    print("CONFIRMED: top-level user_id= works, filters={} is IGNORED.")
    print("   -> memory_store.py fix (user_id=) is correct. Already applied.")
elif len(list_a) == 0 and len(list_b) == 0:
    print("BOTH returned 0. filters= is NOT the only bug (or not the bug).")
    print("   Check TEST 0 -- if also 0, memories were never saved correctly.")
elif len(list_a) > 0 and len(list_b) > 0:
    print("BOTH formats work. filters={} was never the problem.")
    print("   Look at _matches_tenant() prefix filtering -- may be dropping results.")
else:
    print("UNEXPECTED: filters={} returned results but top-level user_id= did not.")
print("=" * 70)
