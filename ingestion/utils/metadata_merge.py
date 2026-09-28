from __future__ import annotations

from copy import deepcopy
from typing import Any


def deep_merge_values(existing: Any, incoming: Any) -> Any:
    """Deep-merge two JSON-like values.

    - dict + dict: recurse; keys only in existing are kept; shared keys merge again
    - list + list: concatenate (existing then incoming)
    - otherwise: incoming replaces existing (scalars / type mismatch)
    """
    if isinstance(existing, dict) and isinstance(incoming, dict):
        result = deepcopy(existing)
        for key, value in incoming.items():
            if key in result:
                result[key] = deep_merge_values(result[key], value)
            else:
                result[key] = deepcopy(value)
        return result

    if isinstance(existing, list) and isinstance(incoming, list):
        return deepcopy(existing) + deepcopy(incoming)

    return deepcopy(incoming)


def normalize_metadata_response(data: Any) -> list[dict[str, Any]]:
    """Normalize a GET /metadata response into ``[{key, value}, ...]``."""
    if data is None:
        return []

    if isinstance(data, list):
        return _coerce_entry_list(data)

    if isinstance(data, dict):
        for wrap_key in ("body", "metadata", "data"):
            if wrap_key in data:
                return normalize_metadata_response(data[wrap_key])

        if "key" in data and "value" in data:
            key = data["key"]
            if isinstance(key, str) and key.strip():
                return [{"key": key, "value": data["value"]}]
            return []

        return [
            {"key": key, "value": value}
            for key, value in data.items()
            if isinstance(key, str) and key.strip()
        ]

    return []


def _coerce_entry_list(entries: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if not isinstance(key, str) or not key.strip():
            continue
        if "value" not in entry:
            continue
        result.append({"key": key, "value": entry["value"]})
    return result


def merge_metadata(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge sidecar metadata onto existing entity metadata.

    Top-level entries are keyed by ``key``. Shared keys deep-merge their values;
    keys only in existing are preserved; keys only in incoming are appended.
    """
    merged: dict[str, Any] = {}
    order: list[str] = []

    for entry in existing:
        key = entry["key"]
        if key not in merged:
            order.append(key)
        merged[key] = deepcopy(entry["value"])

    for entry in incoming:
        key = entry["key"]
        if key in merged:
            merged[key] = deep_merge_values(merged[key], entry["value"])
        else:
            order.append(key)
            merged[key] = deepcopy(entry["value"])

    return [{"key": key, "value": merged[key]} for key in order]
