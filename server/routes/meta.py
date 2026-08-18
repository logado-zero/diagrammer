"""
The two unauthenticated routes: a health probe and the model catalog.

Both are pure reads of process configuration — no database, no session — which
is why they sit apart from the rest.
"""

import os

from fastapi import APIRouter

from server.agent import resolve_claude_transport
from server.models import DEFAULT_MODEL_ID, MODEL_CATALOG, is_model_available

router = APIRouter()


@router.get("/api/health")
def health():
    """Input: none. Output: which Claude transport is active and whether either API key is configured."""
    return {
        "ok": True,
        "claudeTransport": resolve_claude_transport(),
        "hasApiKey": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "hasOpenAiApiKey": bool(os.environ.get("OPENAI_API_KEY")),
        # agent-sdk mode has no way to check credentials up front — it relies on
        # an existing local `claude` CLI login and only fails on the first request.
    }


@router.get("/api/models")
def models():
    """Input: none. Output: the model catalog (models.py) plus per-entry availability, so the client never hardcodes model options."""
    return {
        "defaultModelId": DEFAULT_MODEL_ID,
        "models": [
            {
                "id": option.id,
                "label": option.label,
                "shortLabel": option.short_label,
                "provider": option.provider,
                "description": option.description,
                "available": is_model_available(option),
            }
            for option in MODEL_CATALOG
        ],
    }
