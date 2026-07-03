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

# ── Tenant DB (configuration & dynamic prompts) ─────────────────────────────
TENANT_SUPABASE_URL     = os.getenv("TENANT_SUPABASE_URL") or SUPABASE_URL
TENANT_SUPABASE_SECRET_KEY = os.getenv("TENANT_SUPABASE_SECRET_KEY") or SUPABASE_SERVICE_KEY


# ── Products / GraphRAG APIs ──────────────────────────────────────────────────
PRODUCTS_API_URL        = os.getenv("PRODUCTS_API_URL", "")
GRAPHRAG_API_URL        = os.getenv("GRAPHRAG_API_URL", "")
MCP_SERVER_URL          = os.getenv("MCP_SERVER_URL", os.getenv("INVENTAA_MCP_SERVER_URL", "http://localhost:8008/mcp"))
INVENTAA_MCP_SERVER_URL = MCP_SERVER_URL  # Backward compatibility alias
USE_MCP_SERVER          = os.getenv("USE_MCP_SERVER", "true").lower() == "true"


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