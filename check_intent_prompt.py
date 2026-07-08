import argparse
from typing import cast
from db.session_store import _get_client


def main():
    parser = argparse.ArgumentParser(description="Check a prompt in prompt_templates")
    parser.add_argument("--tenant_id",   required=True)
    parser.add_argument("--prompt_name", required=True)
    parser.add_argument("--language",    default="en")
    args = parser.parse_args()

    result = (
        _get_client()
        .table("prompt_templates")
        .select("prompt_text, version, updated_at")
        .eq("tenant_id",   args.tenant_id)
        .eq("prompt_name", args.prompt_name)
        .eq("language",    args.language)
        .eq("status",      "active")
        .order("version",  desc=True)
        .limit(1)
        .execute()
    )

    if not result.data:
        print(f"No active '{args.prompt_name}' found for tenant '{args.tenant_id}' (language={args.language}).")
        print("Either the prompt_name is wrong, or this tenant hasn't seeded it yet.")
        return

    row = cast(dict, result.data[0])

    print(f"prompt_name : {args.prompt_name}")
    print(f"tenant_id   : {args.tenant_id}")
    print(f"version     : {row.get('version')}")
    print(f"updated_at  : {row.get('updated_at')}")
    print("-" * 70)
    print(row["prompt_text"])
    print("-" * 70)


if __name__ == "__main__":
    main()
