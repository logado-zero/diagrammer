"""
The single source of truth for every model the UI's picker can offer.
Add a model by adding one entry to MODEL_CATALOG — nothing else needs to
change (server/routes/meta.py's GET /api/models and agent.py both just read this
file).
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelOption:
    # Stable id sent by the client in ChatRequestBody.model and returned by GET /api/models.
    id: str
    label: str
    # Short form shown in the composer's model badge, e.g. "Opus 5".
    short_label: str
    # "claude" or "openai" — src/types.ts has the real union; Python has no
    # runtime check here either way.
    provider: str
    # The underlying model id passed to the provider SDK.
    model: str
    description: str | None = None


# Every selectable model, in the order shown in the picker.
MODEL_CATALOG: list[ModelOption] = [
    ModelOption(
        id="claude-opus-5",
        label="Claude Opus 5",
        short_label="Opus 5",
        provider="claude",
        model="claude-opus-5",
        description="Most capable Claude model — best for complex or ambiguous diagrams.",
    ),
    ModelOption(
        id="claude-sonnet-5",
        label="Claude Sonnet 5",
        short_label="Sonnet 5",
        provider="claude",
        model="claude-sonnet-5",
        description="Fast, balanced Claude model.",
    ),
    ModelOption(
        id="claude-haiku-4-5",
        label="Claude Haiku 4.5",
        short_label="Haiku 4.5",
        provider="claude",
        model="claude-haiku-4-5",
        description="Fastest, most economical Claude model.",
    ),
    ModelOption(
        id="gpt-4o",
        label="GPT-4o",
        short_label="GPT-4o",
        provider="openai",
        model="gpt-4o",
        description="OpenAI multimodal model.",
    ),
    ModelOption(
        id="gpt-4o-mini",
        label="GPT-4o mini",
        short_label="GPT-4o mini",
        provider="openai",
        model="gpt-4o-mini",
        description="Faster, cheaper OpenAI model.",
    ),
    ModelOption(
        id="gpt-5.6-luna",
        label="GPT-5.6 Luna",
        short_label="Luna",
        provider="openai",
        model="gpt-5.6-luna",
        description="OpenAI reasoning model.",
    ),
]

# Used whenever the client sends no model id, or an id not in MODEL_CATALOG.
DEFAULT_MODEL_ID = "gpt-5.6-luna"

_BY_ID = {option.id: option for option in MODEL_CATALOG}


def is_model_available(option: ModelOption) -> bool:
    """
    Whether a catalog entry can actually be used right now.
    Input: a ModelOption. Output: True/False.

    Read by GET /api/models (to grey the picker) *and* by POST /api/chat (to
    refuse the turn) — one gate, both places, so the picker is presentation
    and this is enforcement.

    Normally: Claude always resolves to a transport (api or agent-sdk — see
    agent.py), so it never needs a key; OpenAI needs OPENAI_API_KEY.
    """
    # TEMPORARY: the picker is pinned to GPT-5.6 Luna at the user's request.
    # Delete these two lines to restore the full catalog — nothing else needs
    # to change, and the Claude transports are still wired and working.
    if option.id != "gpt-5.6-luna":
        return False
    if option.provider == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    return True


def find_model_option(model_id: str | None) -> ModelOption:
    """
    Resolves a client-supplied model id to a catalog entry.
    Input: model_id (or None if the client sent none).
    Output: the matching ModelOption, or the DEFAULT_MODEL_ID entry if the
    id is missing or unrecognized.
    """
    found = _BY_ID.get(model_id) if model_id else None
    if found:
        return found
    fallback = _BY_ID.get(DEFAULT_MODEL_ID)
    if fallback is None:
        raise RuntimeError(f"DEFAULT_MODEL_ID {DEFAULT_MODEL_ID} is missing from MODEL_CATALOG")
    return fallback


def _demo() -> None:
    """Self-check, run with `python -m server.models`."""
    default = find_model_option(DEFAULT_MODEL_ID)
    assert find_model_option(None) is default, "no model id must fall back to the default"
    assert find_model_option("not-a-real-model-id") is default, "an unknown id must fall back to the default"
    assert find_model_option("gpt-5.6-luna") is _BY_ID["gpt-5.6-luna"]

    # Coupled to the TEMPORARY one-model pin in is_model_available() above —
    # update this assertion (and the loop below) when that pin is removed.
    assert is_model_available(_BY_ID["gpt-5.6-luna"]) == bool(os.environ.get("OPENAI_API_KEY"))
    for option in MODEL_CATALOG:
        if option.id != "gpt-5.6-luna":
            assert not is_model_available(option), f"{option.id} must be gated off while the pin is in place"

    print("models: all checks passed")


if __name__ == "__main__":
    _demo()
