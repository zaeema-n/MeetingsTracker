from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ingestion.pack.errors import PackSchemaError

DEFAULT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schema" / "pack_schema.yaml"
)


def load_schema(schema_path: Path | None = None) -> dict[str, Any]:
    path = schema_path or DEFAULT_SCHEMA_PATH
    if not path.is_file():
        raise PackSchemaError(f"Pack schema not found: {path}")

    with path.open(encoding="utf-8") as file:
        schema = yaml.safe_load(file)

    if not isinstance(schema, dict):
        raise PackSchemaError(f"Pack schema must be a mapping: {path}")

    if "entities" not in schema or "pack" not in schema:
        raise PackSchemaError(f"Pack schema missing 'pack' or 'entities': {path}")

    files = schema.get("pack", {}).get("files")
    if not isinstance(files, dict) or not files:
        raise PackSchemaError(f"Pack schema missing pack.files: {path}")

    _validate_ingest_order(schema, path)

    return schema


def _validate_ingest_order(schema: dict[str, Any], path: Path) -> None:
    entities = schema.get("entities", {})
    if not isinstance(entities, dict) or not entities:
        raise PackSchemaError(f"Pack schema missing entities: {path}")

    ingest_order = schema.get("ingest_order")
    if not isinstance(ingest_order, list) or not ingest_order:
        raise PackSchemaError(f"Pack schema missing ingest_order: {path}")

    entity_types = set(entities)
    seen: set[str] = set()
    duplicates: list[str] = []
    unknown: list[str] = []

    for entry in ingest_order:
        if not isinstance(entry, str):
            raise PackSchemaError(
                f"Pack schema ingest_order entries must be strings: {path}"
            )
        if entry in seen:
            duplicates.append(entry)
        seen.add(entry)
        if entry not in entity_types:
            unknown.append(entry)

    if unknown:
        raise PackSchemaError(
            f"Pack schema ingest_order references unknown entity types "
            f"{sorted(unknown)}: {path}"
        )
    if duplicates:
        raise PackSchemaError(
            f"Pack schema ingest_order lists duplicate entity types "
            f"{sorted(set(duplicates))}: {path}"
        )

    missing = sorted(entity_types - seen)
    if missing:
        raise PackSchemaError(
            f"Pack schema ingest_order missing entity types {missing}: {path}"
        )


def get_entity_config(schema: dict[str, Any], entity_type: str) -> dict[str, Any]:
    entities = schema.get("entities", {})
    if entity_type not in entities:
        raise PackSchemaError(f"Unknown entity type in schema: {entity_type}")
    return entities[entity_type]


def schema_requires_active_at(schema: dict[str, Any]) -> bool:
    return bool(schema.get("pack", {}).get("requires_active_at", False))
