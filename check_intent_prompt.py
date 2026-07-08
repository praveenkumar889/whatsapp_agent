"""
check_intent_prompt.py — Prints the current intent_system_prompt content.

Run this from your project root (same place you run uvicorn from):
    python check_intent_prompt.py

Optional args:
    python check_intent_prompt.py --tenant-id tenant_inventaa_led_001 --prompt-name intent_system_prompt
"""
import argparse
from db.session_store import _get_client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", default="tenant_inventaa_led_001")
    parser.add_argument("--prompt-name", default="intent_system_prompt")
    parser.add_argument("--language", default="en")
    args = parser.parse_args()

    client = _get_client()
    result = (
        client.table("prompt_templates")
        .select("prompt_text, version, updated_at")
        .eq("tenant_id", args.tenant_id)
        .eq("prompt_name", args.prompt_name)
        .eq("language", args.language)
        .eq("status", "active")
        .order("version", desc=True)
        .limit(1)
        .execute()
    )

    if not result.data:
        print(f"No active '{args.prompt_name}' found for tenant '{args.tenant_id}' (language={args.language}).")
        print("Either the prompt_name is wrong, or this tenant hasn't seeded it yet.")
        return

    row = result.data[0]
    print(f"prompt_name : {args.prompt_name}")
    print(f"tenant_id   : {args.tenant_id}")
    print(f"version     : {row.get('version')}")
    print(f"updated_at  : {row.get('updated_at')}")
    print("-" * 70)
    print(row["prompt_text"])
    print("-" * 70)


if __name__ == "__main__":
    main()
