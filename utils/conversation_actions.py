# utils/conversation_actions.py — Tenant-configured quick action fast-path
#
# PURPOSE:
#   Single helper used by both router.py and negotiator.py to check if an
#   incoming message exactly matches a tenant-configured quick action phrase,
#   bypassing the LLM entirely for deterministic cases (e.g. "yes", "confirm").
#
# ARCHITECTURE:
#   Phrases are stored as a JSONB column `quick_actions` on the tenants table:
#     {
#       "ORDER_CONFIRM": ["yes", "confirm", "done", "place order"],
#       "ORDER_CANCEL":  ["cancel", "stop"],
#       "ORDER_RESTART": ["new order", "start over"]
#     }
#
#   During _apply_tenant() in pipeline/setup.py, the raw list is normalized
#   once into a frozenset of casefold()ed strings and stored on incoming.
#   No set comprehension happens at message-time — it's already done.
#
# MATCHING RULES:
#   - casefold() + strip() exact match only
#   - No regex, no startswith(), no contains(), no fuzzy matching
#   - "yes" → fast path. "yes please" → LLM fallback. Exactly what we want.
#   - Emoji (👍, 👌) are intentionally excluded from the configured list;
#     Unicode modifiers make byte-level equality unreliable. The LLM handles them.
#
# ADDING A NEW QUICK ACTION:
#   1. Add the action key + phrases to quick_actions JSONB for the tenant in DB
#   2. Call is_quick_action(incoming, "YOUR_ACTION_KEY") at the call site
#   No code changes needed.

from typing import Optional


def is_quick_action(incoming, action: str, message: Optional[str] = None) -> bool:
    """
    Returns True if the message (or incoming.text if message is None) exactly
    matches any configured phrase for the given action key.

    Matching is case-insensitive via casefold() and whitespace-stripped.
    The phrase set is pre-normalized at tenant load time — no allocation here.
    """
    quick_actions: Optional[dict] = getattr(incoming, "quick_actions", None)
    if not quick_actions:
        return False
    phrase_set: Optional[frozenset] = quick_actions.get(action)
    if not phrase_set:
        return False
    text_to_check = message if message is not None else incoming.text
    return text_to_check.casefold().strip() in phrase_set


def is_quick_confirm(incoming, message: Optional[str] = None) -> bool:
    """Convenience wrapper — checks ORDER_CONFIRM action."""
    return is_quick_action(incoming, "ORDER_CONFIRM", message)


def is_quick_cancel(incoming, message: Optional[str] = None) -> bool:
    """Convenience wrapper — checks ORDER_CANCEL action."""
    return is_quick_action(incoming, "ORDER_CANCEL", message)

