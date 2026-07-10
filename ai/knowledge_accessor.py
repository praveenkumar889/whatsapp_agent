# ai/knowledge_accessor.py — KnowledgeAccessor
#
# ROLE:
#   Lightweight utility to extract capability/knowledge fields (e.g. installation guide, warranty, images)
#   from structured PromptContext.knowledge_context dictionaries dynamically.
#   Uses mappings loaded from tenant_configurations table (default mappings as fallback).

from typing import Optional, Any
from db.session_store import get_tenant_config

class KnowledgeAccessor:
    @staticmethod
    async def get(incoming: Any, knowledge_context: dict, domain: str, field_name: str) -> Optional[dict]:
        """
        Dynamically extracts a capability/knowledge field from the knowledge_context.
        
        Uses the mapping loaded from tenant_configurations table (key: 'knowledge_field_mappings').
        Returns:
            dict: { "type": "text"|"image"|"document"|"video"|"faq", "value": Any }
            None: If not found or mapping is undefined.
        """
        if not knowledge_context or not domain or not field_name:
            return None
            
        tenant_id = getattr(incoming, "tenant_id", None)
        if not tenant_id:
            return None

        # Load configurations dynamically
        mappings = await get_tenant_config(tenant_id, "knowledge_field_mappings")
        if not mappings:
            # Code-defined default fallback mappings
            mappings = {
                "product": {
                    "installation": {
                        "type": "document",
                        "paths": ["assets.installation_url", "documents.installation"]
                    },
                    "manual": {
                        "type": "document",
                        "paths": ["documents.manual", "assets.manual_url"]
                    },
                    "warranty": {
                        "type": "text",
                        "paths": ["metadata.warranty"]
                    },
                    "images": {
                        "type": "image",
                        "paths": ["assets.images", "assets.image_url"]
                    },
                    "specifications": {
                        "type": "text",
                        "paths": ["specifications"]
                    },
                    "faq": {
                        "type": "faq",
                        "paths": ["faq"]
                    },
                    "faqs": {
                        "type": "faq",
                        "paths": ["faq"]
                    },
                    "videos": {
                        "type": "video",
                        "paths": ["assets.videos", "assets.video_url"]
                    },
                    "certifications": {
                        "type": "text",
                        "paths": ["specifications.certifications", "metadata.certifications"]
                    }
                }
            }

        domain_lower = domain.lower().strip()
        field_lower = field_name.lower().strip()
        
        domain_mapping = mappings.get(domain_lower, {})
        field_config = domain_mapping.get(field_lower)
        
        # If no explicit config found, fallback to direct path scan in the domain context
        if not field_config:
            paths = [
                field_lower,
                f"assets.{field_lower}",
                f"assets.{field_lower}_url",
                f"documents.{field_lower}",
                f"metadata.{field_lower}",
                f"specifications.{field_lower}"
            ]
            t = "text"
            if "url" in field_lower or "guide" in field_lower:
                t = "document"
            elif "image" in field_lower or "photo" in field_lower:
                t = "image"
            elif "video" in field_lower:
                t = "video"
            field_config = {
                "type": t,
                "paths": paths
            }

        domain_data = knowledge_context.get(domain_lower, {})
        if not domain_data:
            return None

        paths = field_config.get("paths", [])
        field_type = field_config.get("type", "text")

        for path in paths:
            val = KnowledgeAccessor._traverse(domain_data, path)
            if val is not None and val != "" and val != []:
                return {
                    "type": field_type,
                    "value": val
                }
        return None

    @staticmethod
    def _traverse(data: dict, path: str) -> Optional[Any]:
        """Traverses a nested dictionary using a dotted path."""
        parts = path.split(".")
        curr = data
        for p in parts:
            if isinstance(curr, dict) and p in curr:
                curr = curr[p]
            else:
                return None
        return curr
