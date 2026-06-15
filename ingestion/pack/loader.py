from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from ingestion.pack.errors import PackLoadError
from ingestion.pack.models import IngestMode, IngestRecord, PackState, ResolveContext
from ingestion.pack.schema_loader import PackSchema, load_pack_schema

META_KEYS = {"ingest"}


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def _ingest_mode(node: dict[str, Any], entity_type: str, pack_schema: PackSchema) -> IngestMode:
    explicit = node.get("ingest")
    if explicit is not None:
        if explicit not in ("resolve", "create"):
            raise PackLoadError(
                f"Invalid ingest mode '{explicit}' for {entity_type}; use 'resolve' or 'create'"
            )
        return explicit

    default = pack_schema.entity_config(entity_type).get("default_ingest", "create")
    if default not in ("resolve", "create"):
        raise PackLoadError(f"Invalid default_ingest for {entity_type}: {default}")
    return default


def _entity_payload(
    node: dict[str, Any], entity_type: str, pack_schema: PackSchema
) -> dict[str, Any]:
    nested = set(pack_schema.nested_children.get(entity_type, ()))
    return {
        key: value
        for key, value in node.items()
        if key not in META_KEYS and key not in nested
    }


def _require_id_if_create(
    entity_type: str,
    node: dict[str, Any],
    mode: IngestMode,
    path: str,
    pack_schema: PackSchema,
) -> None:
    if mode != "create":
        return

    id_field = pack_schema.entity_config(entity_type).get("id_field", "id")
    record_id = node.get(id_field)
    if not record_id or not str(record_id).strip():
        raise PackLoadError(
            f"{entity_type} at {path} requires '{id_field}' when not using ingest: resolve"
        )


def _append_record(
    records: list[IngestRecord],
    entity_type: str,
    node: dict[str, Any],
    path: str,
    context: dict[str, Any],
    pack_schema: PackSchema,
) -> IngestMode:
    mode = _ingest_mode(node, entity_type, pack_schema)
    _require_id_if_create(entity_type, node, mode, path, pack_schema)
    records.append(
        IngestRecord(
            entity_type=entity_type,
            ingest_mode=mode,
            data=_entity_payload(node, entity_type, pack_schema),
            path=path,
            context=deepcopy(context),
        )
    )
    return mode


def _walk_entity_list(
    nodes: list[Any],  # YAML list at this tree level
    entity_type: str,  # schema type of each item in nodes
    path_prefix: str,  # path before [index], e.g. "government" or "meeting[0].meeting_instance"
    context: dict[str, Any],  # ancestor paths and _tree_parent_type for this level
    records: list[IngestRecord],  # shared output; records appended in walk order
    pack_schema: PackSchema,  # nested children, ingest defaults, id fields
    file_label: str,  # source filename for error messages
) -> None:
    """Walk a YAML entity list: append IngestRecords, then recurse into nested children."""
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise PackLoadError(f"{file_label}.{path_prefix}[{index}] must be a mapping")

        path = f"{path_prefix}[{index}]"
        _append_record(records, entity_type, node, path, context, pack_schema)

        for child_type in pack_schema.nested_children.get(entity_type, ()):
            children = node.get(child_type)
            if not children:
                continue
            if not isinstance(children, list):
                raise PackLoadError(f"{path}.{child_type} must be a list")

            child_context = dict(context)
            child_context[PackSchema.parent_path_context_key(entity_type)] = path
            child_context["_tree_parent_type"] = entity_type
            child_path_prefix = f"{path}.{child_type}"
            _walk_entity_list(
                children,
                child_type,
                child_path_prefix,
                child_context,
                records,
                pack_schema,
                file_label,
            )


def _walk_file(
    file_data: Any,
    file_key: str,
    records: list[IngestRecord],
    pack_schema: PackSchema,
    file_label: str,
) -> None:
    if not isinstance(file_data, dict):
        raise PackLoadError(f"{file_label} must be a mapping at the top level")

    for entity_type in pack_schema.root_entities_for_file(file_key):
        yaml_key = pack_schema.yaml_key_for(entity_type)
        nodes = file_data.get(yaml_key, [])
        if not nodes:
            continue
        if not isinstance(nodes, list):
            raise PackLoadError(f"{file_label} '{yaml_key}' must be a list")

        _walk_entity_list(
            nodes,
            entity_type,
            yaml_key,
            {},
            records,
            pack_schema,
            file_label,
        )


def _build_indexes(
    records: list[IngestRecord], ingest_order: tuple[str, ...]
) -> dict[str, dict[str, dict]]:
    """
    Build id → record lookups for create-path entities.

    Resolve records (government, president, ministry, department) are skipped because they
    have no pack id. Mappers use these indexes to resolve bare-id link fields
    (e.g. mandated_by, meetings, discovered_events) to the correct entity type.
    """
    indexes: dict[str, dict[str, dict]] = {entity_type: {} for entity_type in ingest_order}

    for record in records:
        if record.ingest_mode != "create":
            continue
        record_id = record.record_id
        if record_id:
            indexes[record.entity_type][record_id] = record.data

    return indexes


def load_pack(
    pack_dir: Path,
    active_at: str,
    schema_path: Path | None = None,
) -> PackState:
    """
    Load a ministry pack directory and produce ingest records with resolve/create modes.

    Args:
        pack_dir: Path to data/<Ministry name>/ containing acts, organisations, meetings, rtis.
        active_at: ISO date used for resolve lookups and create timestamps (required).
        schema_path: Optional override for schema/pack_schema.yaml.
    """
    pack_dir = pack_dir.resolve()
    if not pack_dir.is_dir():
        raise PackLoadError(f"Pack directory not found: {pack_dir}")

    active_at = str(active_at).strip()
    if not active_at:
        raise PackLoadError("active_at is required")

    pack_schema = load_pack_schema(schema_path)
    if pack_schema.requires_active_at() and not active_at:
        raise PackLoadError("pack schema requires --active-at")

    raw_files: dict[str, Any] = {}
    records: list[IngestRecord] = []

    for file_key, filename in pack_schema.files.items():
        file_path = pack_dir / filename
        if not file_path.is_file():
            raise PackLoadError(f"Missing pack file {filename} in {pack_dir}")
        raw_files[file_key] = load_yaml(file_path)
        _walk_file(raw_files[file_key], file_key, records, pack_schema, filename)

    resolve_context = ResolveContext(active_at=active_at)

    return PackState(
        pack_dir=pack_dir,
        pack_schema=pack_schema,
        active_at=active_at,
        raw_files=raw_files,
        records=records,
        indexes=_build_indexes(records, pack_schema.ingest_order),
        resolve_context=resolve_context,
    )
