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

    return schema


def get_entity_config(schema: dict[str, Any], entity_type: str) -> dict[str, Any]:
    entities = schema.get("entities", {})
    if entity_type not in entities:
        raise PackSchemaError(f"Unknown entity type in schema: {entity_type}")
    return entities[entity_type]


def schema_requires_active_at(schema: dict[str, Any]) -> bool:
    return bool(schema.get("pack", {}).get("requires_active_at", False))
