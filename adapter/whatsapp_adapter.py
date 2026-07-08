# adapter/whatsapp_adapter.py — Communication Adapter (WhatsApp → IncomingMessage)
# Supabase Storage added — uploads raw image/audio binary after download.

import os
import uuid
import httpx
from datetime import datetime, timezone
from typing import Optional

from supabase import create_client, Client  # type: ignore[import]
from models.schemas import IncomingMessage
from config import (
    WABA_ID, PHONE_NUMBER_ID, BUSINESS_NAME, ACCESS_TOKEN,
    SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_STORAGE_BUCKET,
)

# Meta Graph API version — override via GRAPH_API_VERSION in .env if Meta
# deprecates v21.0 before this gets a proper config.py entry. Not tenant
# config (platform-wide, not per-business), so this stays an env var.
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v21.0")

# ── Supabase client — lazy singleton ──────────────────────────────────────────
# Created on first use (not at import time) so a missing/placeholder .env
# does not crash uvicorn at startup before credentials are filled in.
_supabase: Optional[Client] = None

def _get_client() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _supabase


async def download_media(media_id: str) -> Optional[bytes]:
    """
    Two-step Meta media download.
    Step 1: Resolve media_id → temporary signed URL.
    Step 2: Download binary from that URL (Bearer token required on both calls).

    Returns:
        bytes → raw binary content (JPEG, PNG, OGG, MP4 etc.)
        None  → if either step fails
    """
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # ── Step 1: Resolve media_id → temporary signed download URL ──────────
        step1 = await client.get(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{media_id}",
            headers=headers
        )
        if step1.status_code != 200:
            print(f"[MEDIA] Step 1 failed: {step1.status_code}")
            return None

        media_url = step1.json().get("url")
        if not media_url:
            print(f"[MEDIA] Step 1 returned no URL")
            return None

        print(f"[MEDIA] Step 1 OK — resolved URL for media_id={media_id}")

        # ── Step 2: Download the binary ────────────────────────────────────────
        # Authorization header is MANDATORY here too — the URL is not public.
        step2 = await client.get(media_url, headers=headers)
        if step2.status_code != 200:
            print(f"[MEDIA] Step 2 failed: {step2.status_code}")
            return None

        print(f"[MEDIA] Step 2 OK — {len(step2.content)} bytes downloaded")
        return step2.content


def get_file_extension(mime_type: str) -> str:
    """
    Maps a MIME type string to a file extension for storage naming.
    Strips codec suffix from audio types (e.g. "audio/ogg; codecs=opus" → "audio/ogg").
    Returns ".bin" as a safe fallback for unknown types.
    """
    base = mime_type.split(";")[0].strip().lower()
    return {
        "image/jpeg": ".jpg",
        "image/png":  ".png",
        "image/webp": ".webp",
        "audio/ogg":  ".ogg",
        "audio/mp4":  ".mp4",
        "audio/mpeg": ".mp3",
    }.get(base, ".bin")


def upload_to_storage(
    binary:    bytes,
    mime_type: str,
    folder:    str,
    file_name: str,
    tenant_id: str = "shared",
) -> Optional[str]:
    """
    Uploads raw binary to the Supabase Storage bucket.

    Storage path: {tenant_id}/{folder}/{file_name}
    Scoping by tenant_id prevents file name collisions across tenants
    and enables per-tenant storage policies/quotas in the future.

    Args:
        binary:    Raw file bytes to upload.
        mime_type: MIME type for the Content-Type header.
        folder:    Subfolder inside the tenant dir ("images" or "audio").
        file_name: Filename including extension.
        tenant_id: Tenant identifier — used as top-level folder.

    Returns:
        str  → permanent public URL of the uploaded file.
        None → if upload fails (pipeline continues without storage).
    """
    try:
        path         = f"{tenant_id}/{folder}/{file_name}"
        content_type = mime_type.split(";")[0].strip()
        # Strip codec suffix so Supabase gets a clean MIME type for the Content-Type header.

        _get_client().storage.from_(SUPABASE_STORAGE_BUCKET).upload(
            path         = path,
            file         = binary,
            file_options = {"content-type": content_type},
        )

        # Build the permanent public URL — works because the bucket is set to Public.
        public_url = (
            f"{SUPABASE_URL}/storage/v1/object/public"
            f"/{SUPABASE_STORAGE_BUCKET}/{path}"
        )
        print(f"[STORAGE] Uploaded → {public_url}")
        return public_url

    except Exception as e:
        print(f"[STORAGE] Upload failed: {e}")
        return None
        # media_url will be None in IncomingMessage — that's acceptable.


async def parse_webhook(data: dict) -> Optional[IncomingMessage]:
    """
    Translates raw Meta webhook JSON → clean, platform-neutral IncomingMessage.

    For text:  extracts message body directly — no HTTP calls needed.
    For image: downloads binary from Meta, uploads to Supabase Storage, stores URL.
    For audio: same as image flow.

    Returns None for delivery receipts, read receipts, and unsupported types.
    """
    try:
        entry = data.get("entry", [])
        if not entry:
            return None

        value    = entry[0]["changes"][0]["value"]
        messages = value.get("messages", [])
        if not messages:
            # No "messages" key = delivery/read receipt — skip silently.
            return None

        msg     = messages[0]
        contact = value["contacts"][0]
        meta    = value.get("metadata", {})

        msg_type    = msg.get("type")
        received_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        timestamp   = int(msg["timestamp"])

        # Extract quoted message context
        context = msg.get("context", {})
        quoted_message_id = context.get("id")

        # Initialise all media fields to None — text messages leave them None.
        media_id = media_mime_type = media_binary = media_url = None

        # ── Route by message type ──────────────────────────────────────────────

        if msg_type == "text":
            # Fastest path — text is already in the payload, no HTTP calls needed.
            text = msg["text"]["body"]
            print(f"[ADAPTER] Text: '{text[:60]}'")

        elif msg_type == "image":
            media_id        = msg["image"]["id"]
            media_mime_type = msg["image"].get("mime_type", "image/jpeg")
            print(f"[ADAPTER] Image — media_id={media_id} mime={media_mime_type}")

            media_binary = await download_media(media_id)
            if media_binary is None:
                print(f"[ADAPTER] Image download failed — skipping message")
                return None

            print(f"[ADAPTER] Image binary ready — {len(media_binary)} bytes")

            # Upload binary to Supabase Storage → get permanent URL.
            # Path is scoped under tenant_id to prevent cross-tenant collisions.
            # Note: tenant_id is not available at parse_webhook time (resolved in main.py).
            # We use the phone_number_id as a proxy scope during ingestion.
            _scope = meta.get("phone_number_id", "unresolved")
            file_name = f"{msg['from']}_{timestamp}{get_file_extension(media_mime_type)}"
            media_url = upload_to_storage(media_binary, media_mime_type, "images", file_name, _scope)
            # media_url is None if upload failed — pipeline continues either way.

            # OCR not yet implemented. Use an internal marker, never
            # customer-facing text — tenant_id isn't resolved yet at this
            # point (that happens in pipeline/setup.py), so we can't render
            # a DB-driven prompt here. setup.py detects this marker right
            # after tenant resolution and replaces it with the tenant's own
            # media_unsupported_prompt (see migration 018).
            text = "__MEDIA_UNSUPPORTED_IMAGE__"

        elif msg_type == "audio":
            media_id        = msg["audio"]["id"]
            media_mime_type = msg["audio"].get("mime_type", "audio/ogg")
            print(f"[ADAPTER] Audio — media_id={media_id} mime={media_mime_type}")

            media_binary = await download_media(media_id)
            if media_binary is None:
                print(f"[ADAPTER] Audio download failed — skipping message")
                return None

            print(f"[ADAPTER] Audio binary ready — {len(media_binary)} bytes")

            # Upload binary to Supabase Storage → get permanent URL.
            _scope = meta.get("phone_number_id", "unresolved")
            file_name = f"{msg['from']}_{timestamp}{get_file_extension(media_mime_type)}"
            media_url = upload_to_storage(media_binary, media_mime_type, "audio", file_name, _scope)

            # STT not yet implemented — same internal-marker approach as
            # the image case above; see setup.py for where this becomes a
            # real, tenant-configurable customer-facing message.
            text = "__MEDIA_UNSUPPORTED_AUDIO__"

        else:
            # Sticker, reaction, location, contact, document — not in scope for Phase 1.
            print(f"[ADAPTER] Unsupported type: '{msg_type}' — skipping")
            return None

        # ── Build the platform-neutral IncomingMessage ─────────────────────────
        return IncomingMessage(
            trace_id          = f"trace_{uuid.uuid4().hex[:8]}",
            message_id        = msg["id"],
            session_id        = msg["from"],
            channel           = "whatsapp",
            timestamp         = timestamp,
            tenant_id         = "UNRESOLVED",
            waba_id           = meta.get("waba_id", WABA_ID),
            phone_number_id   = meta.get("phone_number_id", PHONE_NUMBER_ID),
            biz_name          = BUSINESS_NAME,
            region            = "unresolved",  # overwritten by tenant resolution in setup.py
            timezone          = "UTC",  # neutral placeholder until tenant resolution overwrites it
            language          = "en",
            sender_name       = contact["profile"]["name"],
            sender_phone      = contact["wa_id"],
            text              = text,
            original_type     = msg_type,
            received_at       = received_at,
            media_id          = media_id,
            media_mime_type   = media_mime_type,
            media_binary      = media_binary,
            media_url         = media_url,
            quoted_message_id = quoted_message_id,
            quoted_caption    = None,
            raw               = data,
        )

    except (KeyError, IndexError, TypeError) as e:
        print(f"[ADAPTER ERROR] Malformed webhook payload: {e}")
        return None


# ── Outbound message senders ─────────────────────────────────────────────────
async def send_whatsapp_reply(
    to:             str,
    message:        str,
    phone_number_id: Optional[str] = None,
    access_token:   Optional[str] = None,
) -> Optional[str]:
    """
    Sends a text reply to a WhatsApp user via Meta Graph API.

    phone_number_id and access_token are per-tenant when provided.
    Falls back to the global env-var constants for single-tenant deployments.
    Returns the message ID (wamid) if successful, None otherwise.
    """
    _pid   = phone_number_id or PHONE_NUMBER_ID
    _token = access_token    or ACCESS_TOKEN
    url     = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{_pid}/messages"
    headers = {"Authorization": f"Bearer {_token}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type":    "individual",
        "to":                to,
        "type":              "text",
        "text":              {"body": message},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            print(f"[WHATSAPP] Reply sent to ...{to[-4:]}")
            try:
                res_data = response.json()
                msg_id = res_data.get("messages", [{}])[0].get("id")
                return msg_id or "unknown_wamid"
            except Exception:
                return "unknown_wamid"
        else:
            print(f"[WHATSAPP] Error {response.status_code}: {response.text}")
            return None


async def send_whatsapp_image(
    to:             str,
    image_url:      str,
    caption:        str = "",
    phone_number_id: Optional[str] = None,
    access_token:   Optional[str] = None,
) -> Optional[str]:
    """
    Sends a product image to a WhatsApp user via Meta Graph API.

    phone_number_id and access_token are per-tenant when provided.
    Falls back to the global env-var constants for single-tenant deployments.
    Returns the message ID (wamid) if successful, None otherwise.
    """
    try:
        _pid   = phone_number_id or PHONE_NUMBER_ID
        _token = access_token    or ACCESS_TOKEN
        url     = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{_pid}/messages"
        headers = {"Authorization": f"Bearer {_token}", "Content-Type": "application/json"}
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type":    "individual",
            "to":                to,
            "type":              "image",
            "image": {
                "link":    image_url,
                "caption": caption,
            },
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                print(f"[WHATSAPP] Image sent to ...{to[-4:]} — {image_url[:60]}")
                try:
                    res_data = response.json()
                    msg_id = res_data.get("messages", [{}])[0].get("id")
                    return msg_id or "unknown_wamid"
                except Exception:
                    return "unknown_wamid"
            else:
                print(f"[WHATSAPP] Image failed {response.status_code}: {response.text[:100]}")
                return None
    except Exception as e:
        print(f"[WHATSAPP] Image send error: {e}")
        return None