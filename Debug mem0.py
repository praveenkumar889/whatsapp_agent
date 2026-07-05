"""
Verify agent_id isolation in Mem0 SDK 2.0.10.
Run this to confirm whether agent_id is being stored and filtered correctly.

    .venv\Scripts\python.exe debug_mem0_agent.py
"""
import os, time
from dotenv import load_dotenv
load_dotenv()

from mem0 import MemoryClient
client = MemoryClient(api_key=os.getenv("MEM0_API_KEY", ""))

USER_A  = "customer_919999000001"
USER_B  = "customer_919999000002"
TENANT1 = "tenant_inventaa_led_001"
TENANT2 = "tenant_solar_lights_002"   # simulated second tenant

print("=" * 60)
print("TEST: Multi-tenant isolation via agent_id")
print("=" * 60)

# Save memory for Tenant 1, User A
client.add(
    messages=[{"role": "user", "content": "I want Romy 12W Bollard lights"}],
    user_id  = USER_A,
    agent_id = TENANT1,
)
# Save memory for Tenant 2, User A (same user, different tenant)
client.add(
    messages=[{"role": "user", "content": "I want Solar panels for rooftop"}],
    user_id  = USER_A,
    agent_id = TENANT2,
)
print("Saved 2 memories for same user under 2 different tenants.")
print("Waiting 3 seconds for indexing...")
time.sleep(3)

# Search as Tenant 1
r1 = client.search(
    query   = "lights",
    filters = {"user_id": USER_A, "agent_id": TENANT1},
)
results1 = r1.get("results", [])
print(f"\nTenant 1 search → {len(results1)} results:")
for r in results1:
    print(f"  memory='{r.get('memory')}' user_id={r.get('user_id')} agent_id={r.get('agent_id','NOT STORED')}")

# Search as Tenant 2
r2 = client.search(
    query   = "lights",
    filters = {"user_id": USER_A, "agent_id": TENANT2},
)
results2 = r2.get("results", [])
print(f"\nTenant 2 search → {len(results2)} results:")
for r in results2:
    print(f"  memory='{r.get('memory')}' user_id={r.get('user_id')} agent_id={r.get('agent_id','NOT STORED')}")

# Search with NO agent_id (should return ALL if agent_id not stored)
r3 = client.search(
    query   = "lights",
    filters = {"user_id": USER_A},
)
results3 = r3.get("results", [])
print(f"\nNo agent_id filter → {len(results3)} results:")
for r in results3:
    print(f"  memory='{r.get('memory')}' agent_id={r.get('agent_id','NOT STORED')}")

print()
print("=" * 60)
print("DIAGNOSIS:")
if len(results1) == 1 and len(results2) == 1:
    t1_mem = results1[0].get('memory','')
    t2_mem = results2[0].get('memory','')
    if 'Romy' in t1_mem and 'Solar' not in t1_mem:
        print("✅ agent_id isolation WORKS — tenants see only their own memories")
    else:
        print("❌ agent_id isolation BROKEN — Tenant 1 sees Tenant 2 memory")
elif len(results1) == 0 and len(results2) == 0:
    print("❌ agent_id filter excludes ALL results — agent_id is not stored")
    print("   WORKAROUND: store tenant_id inside the memory text itself")
    print("   e.g. 'TENANT:tenant_inventaa_led_001 | LAST_PRODUCT: Romy 12W'")
    print("   Then filter by scanning memory text, not by agent_id metadata")
elif len(results3) > len(results1):
    print("⚠️  agent_id is stored but filtering may not work correctly")
    print("   Check if your Mem0 plan supports agent_id filtering")
print("=" * 60)