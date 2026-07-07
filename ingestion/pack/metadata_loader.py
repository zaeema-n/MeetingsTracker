from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ingestion.pack.errors import PackLoadError
from ingestion.pack.models import MetadataDocument


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _validate_metadata_entries(raw_metadata: Any, *, file_label: str) -> list[dict[str, Any]]:
    if not isinstance(raw_metadata, list) or not raw_metadata:
        raise PackLoadError(f"{file_label} 'metadata' must be a non-empty list")

    validated_metadata: list[dict[str, Any]] = []
    for index, entry in enumerate(raw_metadata):
        if not isinstance(entry, dict):
            raise PackLoadError(f"{file_label} metadata[{index}] must be a mapping")

        key = entry.get("key")
        if not isinstance(key, str) or not key.strip():
            raise PackLoadError(f"{file_label} metadata[{index}].key must be a non-empty string")

        if "value" not in entry:
            raise PackLoadError(f"{file_label} metadata[{index}].value is required")

        validated_metadata.append({"key": key, "value": entry["value"]})

    return validated_metadata


def load_metadata_files(pack_dir: Path) -> list[MetadataDocument]:
    """Load and validate all ``*_metadata.json`` files from a ministry pack directory."""

    pack_dir = pack_dir.resolve()
    if not pack_dir.is_dir():
        raise PackLoadError(f"Pack directory not found: {pack_dir}")

    documents: list[MetadataDocument] = []
    for path in sorted(pack_dir.glob("*_metadata.json")):
        file_label = path.name

        try:
            payload = _load_json(path)
        except json.JSONDecodeError as exc:
            raise PackLoadError(f"Invalid JSON in {file_label}: {exc}") from exc

        if not isinstance(payload, dict):
            raise PackLoadError(f"{file_label} must be a mapping at the top level")

        entity_key = payload.get("entity_key")
        if not isinstance(entity_key, str) or not entity_key.strip():
            raise PackLoadError(f"{file_label} 'entity_key' must be a non-empty string")

        metadata = _validate_metadata_entries(payload.get("metadata"), file_label=file_label)

        try:
            documents.append(
                MetadataDocument(
                    entity_key=entity_key.strip(),
                    metadata=metadata,
                    source_path=path,
                )
            )
        except ValidationError as exc:
            raise PackLoadError(f"Invalid metadata document in {file_label}: {exc}") from exc

    return documents
