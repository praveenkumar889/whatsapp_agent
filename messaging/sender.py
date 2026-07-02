import uuid
from typing import Optional
from adapter.whatsapp_adapter import send_whatsapp_reply, send_whatsapp_image
from models.schemas import IncomingMessage

async def send_reply(incoming: IncomingMessage, message: str) -> Optional[str]:
    """
    Sends a text reply. Appends the message to the incoming object's captured_replies
    for channel-agnostic logging/rendering. If the channel is 'whatsapp', calls Meta's
    API to dispatch the message using the tenant's own credentials when available.
    """
    # 1. Capture reply
    incoming.captured_replies.append({"type": "text", "body": message})

    # 2. Dispatch if whatsapp
    if incoming.channel == "whatsapp":
        return await send_whatsapp_reply(
            incoming.session_id,
            message,
            phone_number_id = incoming.phone_number_id,
            access_token    = getattr(incoming, "access_token", None),
        )
    else:
        # Streamlit or other mock channels
        print(f"[MOCK SENDER] Captured text reply to ...{incoming.session_id[-4:]}: {message[:60]}...")
        return f"mock_wamid_{uuid.uuid4().hex[:8]}"

async def send_image(incoming: IncomingMessage, image_url: str, caption: str = "") -> Optional[str]:
    """
    Sends an image. Appends the image details to the incoming object's captured_replies.
    If the channel is 'whatsapp', calls Meta's API to dispatch the image using the
    tenant's own credentials when available.
    """
    # 1. Capture image
    incoming.captured_replies.append({"type": "image", "link": image_url, "caption": caption})

    # 2. Dispatch if whatsapp
    if incoming.channel == "whatsapp":
        return await send_whatsapp_image(
            incoming.session_id,
            image_url,
            caption,
            phone_number_id = incoming.phone_number_id,
            access_token    = getattr(incoming, "access_token", None),
        )
    else:
        # Streamlit or other mock channels
        print(f"[MOCK SENDER] Captured image reply to ...{incoming.session_id[-4:]}: {image_url[:60]}...")
        return f"mock_wamid_{uuid.uuid4().hex[:8]}"
