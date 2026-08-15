from __future__ import annotations

import re

# Provider model fields are edited by humans in the admin console, while some
# upstream APIs (notably chat2api) require exact machine model IDs. Normalize
# only known aliases here so all downstream users of provider_manager receive
# the canonical ID without rewriting the value shown in the admin UI/database.
from app import provider_manager as _provider_manager

_original_image_model_candidates = _provider_manager.image_model_candidates


def _image_model_candidates_with_aliases(provider: dict[str, object]) -> list[str]:
    normalized: list[str] = []
    for value in _original_image_model_candidates(provider):
        raw = str(value or "").strip()
        token = re.sub(r"[\s_]+", "-", raw.lower())
        if token in {"gpt-image", "gptimage"}:
            raw = "gpt-image"
        if raw and raw not in normalized:
            normalized.append(raw)
    return normalized


_provider_manager.image_model_candidates = _image_model_candidates_with_aliases
