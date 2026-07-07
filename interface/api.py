import os
import httpx
from typing import Optional, Any, Dict

# All defaults loaded from env — no hardcoded tenant/phone/URL values.
# Set these in .env for local dev; override in production deployment config.
BACKEND_URL          = os.getenv("BACKEND_URL",             "http://localhost:8000")
DEFAULT_PHONE_ID     = os.getenv("DEFAULT_PHONE_NUMBER_ID", "1124766240726230")
DEFAULT_TENANT_ID    = os.getenv("DEFAULT_TENANT_ID",       "tenant_inventaa_led_001")
DEFAULT_SENDER_NAME  = os.getenv("DEFAULT_SENDER_NAME",     "Test User")


def send_message_to_backend(
    phone:           str,
    message:         str,
    sender_name:     str = DEFAULT_SENDER_NAME,
    phone_number_id: str = DEFAULT_PHONE_ID,
) -> Optional[Dict[str, Any]]:
    """
    Sends a message to the FastAPI backend /chat endpoint and returns the captured response.
    BACKEND_URL, phone_number_id and tenant defaults are read from environment variables.
    """
    url     = f"{BACKEND_URL}/chat"
    payload = {
        "phone":           phone,
        "message":         message,
        "sender_name":     sender_name,
        "phone_number_id": phone_number_id,
    }
    try:
        with httpx.Client(timeout=45.0) as client:
            response = client.post(url, json=payload)
            if response.status_code == 200:
                return response.json()
            print(f"[API ERROR] Status {response.status_code}: {response.text}")
            return {
                "replies": [{"type": "text", "body": f"⚠️ Error: Backend returned status code {response.status_code}"}],
                "debug":   {"intent": "ERROR", "confidence": 0.0, "latency": 0.0,
                            "route": "API Error", "tenant_id": "None"},
            }
    except Exception as e:
        print(f"[API CONNECTION ERROR] Failed to connect to backend: {e}")
        return {
            "replies": [{"type": "text", "body": f"⚠️ Connection Error: Is the FastAPI backend running on {BACKEND_URL}?"}],
            "debug":   {"intent": "CONNECTION_ERROR", "confidence": 0.0, "latency": 0.0,
                        "route": "None", "tenant_id": "None"},
        }


def reset_session_on_backend(phone: str, tenant_id: str = DEFAULT_TENANT_ID) -> bool:
    """
    Calls the FastAPI backend /reset endpoint to clear session state and history.
    tenant_id defaults to DEFAULT_TENANT_ID from environment.
    """
    url     = f"{BACKEND_URL}/reset"
    payload = {"phone": phone, "tenant_id": tenant_id}
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload)
            return response.status_code == 200
    except Exception as e:
        print(f"[API RESET ERROR] Failed to reset session on backend: {e}")
        return False