# config.py — Central Configuration Loader
from dotenv import load_dotenv
import os

load_dotenv()

# ── WhatsApp / Meta Cloud API ─────────────────────────────────────────────────
PHONE_NUMBER_ID  = os.getenv("PHONE_NUMBER_ID")
WABA_ID          = os.getenv("WABA_ID")
ACCESS_TOKEN     = os.getenv("ACCESS_TOKEN")
VERIFY_TOKEN     = os.getenv("VERIFY_TOKEN")
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "")

# ── Azure OpenAI ──────────────────────────────────────────────────────────────
AZURE_AI_ENDPOINT       = os.getenv("AZURE_AI_ENDPOINT") or ""
AZURE_AI_API_KEY        = os.getenv("AZURE_AI_API_KEY") or ""
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
AZURE_AI_API_VERSION    = os.getenv("AZURE_AI_API_VERSION", "2024-12-01-preview")

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL            = os.getenv("SUPABASE_URL") or ""
SUPABASE_SERVICE_KEY    = os.getenv("SUPABASE_SERVICE_KEY") or ""
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "whatsapp-media")

# ── Products / GraphRAG APIs ──────────────────────────────────────────────────
PRODUCTS_API_URL  = os.getenv("PRODUCTS_API_URL", "")
GRAPHRAG_API_URL  = os.getenv("GRAPHRAG_API_URL", "")

# ── Mem0 ──────────────────────────────────────────────────────────────────────
# Get your API key from https://app.mem0.ai
# This replaces: get_session_history() DB reads and workflow_sessions table
MEM0_API_KEY = os.getenv("MEM0_API_KEY", "")

# ── Application ───────────────────────────────────────────────────────────────
APP_NAME      = os.getenv("APP_NAME", "WhatsApp AI Agent")
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "WhatsApp AI Agent")
APP_ENV       = os.getenv("APP_ENV", "production")

# ── Alerting ──────────────────────────────────────────────────────────────────
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")