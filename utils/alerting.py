# utils/alerting.py — Error Alerting
#
# Sends pipeline crash alerts to Slack webhook.
# Set SLACK_WEBHOOK_URL in .env to enable.
# Leave empty to disable — no alerts sent, no errors thrown.

import httpx
from config import SLACK_WEBHOOK_URL, APP_ENV


async def alert_pipeline_error(error: Exception, incoming) -> None:
    """
    Sends a Slack alert when the pipeline crashes.
    Called in run_pipeline() except block.
    Silent no-op if SLACK_WEBHOOK_URL is not set.
    """
    if not SLACK_WEBHOOK_URL:
        return
    try:
        tenant  = getattr(incoming, "tenant_id", "UNKNOWN")
        phone   = getattr(incoming, "sender_phone", "???")
        masked  = f"...{phone[-4:]}" if phone and len(phone) >= 4 else phone
        message = getattr(incoming, "text", "")[:80]
        text    = (
            f"🔴 *Pipeline crash* [{APP_ENV}]\n"
            f"• Tenant: `{tenant}`\n"
            f"• Phone: `{masked}`\n"
            f"• Message: `{message}`\n"
            f"• Error: `{type(error).__name__}: {str(error)[:200]}`"
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(SLACK_WEBHOOK_URL, json={"text": text})
    except Exception as e:
        # Never let alerting crash the main flow
        print(f"[ALERT] Failed to send Slack alert: {e}")


async def alert_mem0_degraded(error: Exception, operation: str) -> None:
    """
    Sends a Slack alert when Mem0 fails repeatedly.
    Called from memory_store.py when fallback to Postgres is triggered.
    """
    if not SLACK_WEBHOOK_URL:
        return
    try:
        text = (
            f"⚠️ *Mem0 degraded* [{APP_ENV}]\n"
            f"• Operation: `{operation}`\n"
            f"• Error: `{type(error).__name__}: {str(error)[:150]}`\n"
            f"• Falling back to Postgres — no data loss."
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(SLACK_WEBHOOK_URL, json={"text": text})
    except Exception:
        pass