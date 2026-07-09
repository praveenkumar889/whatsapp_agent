# ai/mcp_client.py — FastMCP Client Integration for MCP Server
#
# This module provides asynchronous integration between the WhatsApp agent
# and the GraphRAG MCP Server using FastMCP Client.

import asyncio
import logging
import json
from typing import Optional, Dict, Any, List
from fastmcp import Client
from config import MCP_SERVER_URL

logger = logging.getLogger(__name__)

async def query_mcp_catalog(
    query: str,
    session_id: Optional[str] = None,
    limit: int = 6,
    server_url: Optional[str] = None,
    state: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Calls the `search_catalog` tool on the FastMCP server.
    
    Returns a dictionary containing:
        - status: str ("success" or error)
        - intent: str (classified intent)
        - products: list of product dictionaries matching query
        - product_links: list of product links
        - response: str (natural language synthesis or advice)
    
    Returns None if connection or tool call fails, allowing seamless fallback to REST API.
    """
    url = server_url or MCP_SERVER_URL
    print(f"[MCP-CLIENT] Connecting to MCP server at {url} for query: '{query[:50]}'")
    try:
        async with Client(url) as client:
            res = await client.call_tool(
                "search_catalog",
                {
                    "query": query,
                    "limit": limit,
                    "session_id": session_id,
                    "dialogue_state": state
                }
            )
            # res.data contains the tool return value (dict) when calling via FastMCP
            data = getattr(res, "data", None)
            if data is not None and isinstance(data, dict):
                print(f"[MCP-CLIENT] Tool 'search_catalog' succeeded — returned {len(data.get('products', []))} products (intent: {data.get('intent')})")
                return data
            
            content = getattr(res, "content", None)
            if isinstance(content, list) and len(content) > 0:
                for item in content:
                    if hasattr(item, "text") and item.text:
                        try:
                            return json.loads(item.text)
                        except Exception:
                            return {"status": "success", "response": item.text, "products": []}
            if isinstance(data, dict):
                return data
            return {"status": "success", "products": [], "response": str(data or content)}
    except Exception as e:
        print(f"[MCP-CLIENT] Failed to call 'search_catalog' on {url}: {e}")
        logger.warning(f"[MCP-CLIENT] Error calling MCP tool search_catalog: {e}")
        return None

# Backward compatibility alias
query_inventaa_catalog = query_mcp_catalog

async def get_product_details_mcp(
    sku: str,
    server_url: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Calls the `get_product_details` tool on the FastMCP server by SKU.
    """
    url = server_url or MCP_SERVER_URL
    print(f"[MCP-CLIENT] Requesting product details for SKU '{sku}' via MCP ({url})")
    try:
        async with Client(url) as client:
            res = await client.call_tool("get_product_details", {"sku": sku})
            data = getattr(res, "data", None)
            if data is not None and isinstance(data, dict):
                return data
            content = getattr(res, "content", None)
            if isinstance(content, list) and len(content) > 0:
                for item in content:
                    if hasattr(item, "text") and item.text:
                        try:
                            return json.loads(item.text)
                        except Exception:
                            pass
            return None
    except Exception as e:
        print(f"[MCP-CLIENT] Failed to call 'get_product_details' for SKU '{sku}': {e}")
        return None

async def get_taxonomy_hints_mcp(
    query: str,
    server_url: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Calls the `get_taxonomy_hints` tool on the FastMCP server.
    """
    url = server_url or MCP_SERVER_URL
    print(f"[MCP-CLIENT] Requesting taxonomy hints for '{query[:50]}' via MCP ({url})")
    try:
        async with Client(url) as client:
            res = await client.call_tool("get_taxonomy_context", {"query": query})
            data = getattr(res, "data", None)
            if data is not None and isinstance(data, dict):
                return data.get("hints", {})
            content = getattr(res, "content", None)
            if isinstance(content, list) and len(content) > 0:
                for item in content:
                    if hasattr(item, "text") and item.text:
                        try:
                            parsed = json.loads(item.text)
                            return parsed.get("hints", {})
                        except Exception:
                            pass
            return {}
    except Exception as e:
        print(f"[MCP-CLIENT] Failed to call 'get_taxonomy_hints': {e}")
        return {}
