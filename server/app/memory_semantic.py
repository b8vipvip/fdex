from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata


def parse_tag_response(content: str, expected_count: int) -> list[list[str]]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("missing JSON")
    payload = json.loads(cleaned[start : end + 1])
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or len(items) != expected_count:
        raise ValueError("item count mismatch")
    normalized: list[list[str]] = []
    for raw_tags in items:
        if not isinstance(raw_tags, list):
            raise ValueError("tags must be arrays")
        tags: list[str] = []
        for raw in raw_tags:
            if not isinstance(raw, str):
                continue
            tag = unicodedata.normalize("NFKC", raw).strip().lower()
            tag = re.sub(r"\s+", " ", tag)
            if 1 <= len(tag) <= 120 and tag not in tags:
                tags.append(tag)
            if len(tags) >= 48:
                break
        if len(tags) < 4:
            raise ValueError("too few tags")
        normalized.append(tags)
    return normalized


def hash_tags(tags: list[str], dimension: int) -> list[float]:
    vector = [0.0] * dimension
    for rank, tag in enumerate(tags):
        digest = hashlib.sha256(tag.encode("utf-8")).digest()
        weight = 1.0 / math.sqrt(rank + 1)
        for projection in range(4):
            offset = projection * 4
            index = int.from_bytes(digest[offset : offset + 4], "big") % dimension
            sign = 1.0 if digest[16 + projection] & 1 else -1.0
            vector[index] += sign * weight * 0.5
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        raise ValueError("empty semantic vector")
    return [value / norm for value in vector]
