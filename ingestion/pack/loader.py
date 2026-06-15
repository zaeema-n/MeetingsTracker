from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from ingestion.pack.errors import PackLoadError
from ingestion.pack.models import IngestMode, IngestRecord, PackState, ResolveContext
from ingestion.pack.schema_loader import (
    get_entity_config,
    load_pack_schema,
)

# Keys that hold nested entity lists — stripped from parent record `data`
NESTED_KEYS_BY_ENTITY: dict[str, set[str]] = {
    "government": {"president"},
    "president": {"ministry"},
    "ministry": {"department"},
    "department": {"board", "council"},
    "meeting": {"meeting_instance"},
}

META_KEYS = {"ingest"}


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def _ingest_mode(node: dict[str, Any], entity_type: str, schema: dict[str, Any]) -> IngestMode:
    explicit = node.get("ingest")
    if explicit is not None:
        if explicit not in ("resolve", "create"):
            raise PackLoadError(
                f"Invalid ingest mode '{explicit}' for {entity_type}; use 'resolve' or 'create'"
            )
        return explicit

    default = get_entity_config(schema, entity_type).get("default_ingest", "create")
    if default not in ("resolve", "create"):
        raise PackLoadError(f"Invalid default_ingest for {entity_type}: {default}")
    return default


def _entity_payload(node: dict[str, Any], entity_type: str) -> dict[str, Any]:
    nested = NESTED_KEYS_BY_ENTITY.get(entity_type, set())
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
    schema: dict[str, Any],
) -> None:
    if mode != "create":
        return

    id_field = get_entity_config(schema, entity_type).get("id_field", "id")
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
    schema: dict[str, Any],
) -> IngestMode:
    mode = _ingest_mode(node, entity_type, schema)
    _require_id_if_create(entity_type, node, mode, path, schema)
    records.append(
        IngestRecord(
            entity_type=entity_type,
            ingest_mode=mode,
            data=_entity_payload(node, entity_type),
            path=path,
            context=deepcopy(context),
        )
    )
    return mode


def _walk_organisations(
    organisations: dict[str, Any],
    records: list[IngestRecord],
    schema: dict[str, Any],
) -> None:
    governments = organisations.get("government")
    if not isinstance(governments, list):
        raise PackLoadError("organisations.yaml must contain a 'government' list at the root")

    for government_index, government in enumerate(governments):
        if not isinstance(government, dict):
            raise PackLoadError(f"government[{government_index}] must be a mapping")

        government_path = f"government[{government_index}]"
        government_context: dict[str, Any] = {}
        _append_record(
            records, "government", government, government_path, government_context, schema
        )

        presidents = government.get("president", [])
        if not isinstance(presidents, list):
            raise PackLoadError(f"{government_path}.president must be a list")

        for president_index, president in enumerate(presidents):
            if not isinstance(president, dict):
                raise PackLoadError(
                    f"{government_path}.president[{president_index}] must be a mapping"
                )

            president_path = f"{government_path}.president[{president_index}]"
            president_context = {
                "_parent_government_path": government_path,
            }
            _append_record(records, "president", president, president_path, president_context, schema)

            ministries = president.get("ministry", [])
            if not isinstance(ministries, list):
                raise PackLoadError(f"{president_path}.ministry must be a list")

            for ministry_index, ministry in enumerate(ministries):
                if not isinstance(ministry, dict):
                    raise PackLoadError(
                        f"{president_path}.ministry[{ministry_index}] must be a mapping"
                    )

                ministry_path = f"{president_path}.ministry[{ministry_index}]"
                ministry_context = {
                    "_parent_government_path": government_path,
                    "_parent_president_path": president_path,
                }
                _append_record(records, "ministry", ministry, ministry_path, ministry_context, schema)

                departments = ministry.get("department", [])
                if not isinstance(departments, list):
                    raise PackLoadError(f"{ministry_path}.department must be a list")

                for department_index, department in enumerate(departments):
                    if not isinstance(department, dict):
                        raise PackLoadError(
                            f"{ministry_path}.department[{department_index}] must be a mapping"
                        )

                    department_path = f"{ministry_path}.department[{department_index}]"
                    department_context = {
                        "_parent_government_path": government_path,
                        "_parent_president_path": president_path,
                        "_parent_ministry_path": ministry_path,
                    }
                    _append_record(
                        records, "department", department, department_path, department_context, schema
                    )

                    for collection, entity_type in (("board", "board"), ("council", "council")):
                        items = department.get(collection, [])
                        if not items:
                            continue
                        if not isinstance(items, list):
                            raise PackLoadError(f"{department_path}.{collection} must be a list")

                        for item_index, item in enumerate(items):
                            if not isinstance(item, dict):
                                raise PackLoadError(
                                    f"{department_path}.{collection}[{item_index}] must be a mapping"
                                )
                            item_path = f"{department_path}.{collection}[{item_index}]"
                            item_context = {
                                "_parent_government_path": government_path,
                                "_parent_president_path": president_path,
                                "_parent_ministry_path": ministry_path,
                                "_parent_department_path": department_path,
                            }
                            _append_record(
                                records, entity_type, item, item_path, item_context, schema
                            )


def _load_collection_records(
    file_data: dict[str, Any],
    collection_key: str,
    entity_type: str,
    records: list[IngestRecord],
    schema: dict[str, Any],
    file_label: str,
) -> None:
    items = file_data.get(collection_key, [])
    if items is None:
        return
    if not isinstance(items, list):
        raise PackLoadError(f"{file_label} '{collection_key}' must be a list")

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise PackLoadError(f"{file_label}.{collection_key}[{index}] must be a mapping")

        path = f"{collection_key}[{index}]"
        _append_record(records, entity_type, item, path, {}, schema)

        if entity_type == "meeting":
            meeting_instances = item.get("meeting_instance", [])
            if not meeting_instances:
                continue
            if not isinstance(meeting_instances, list):
                raise PackLoadError(f"{path}.meeting_instance must be a list")

            for instance_index, instance in enumerate(meeting_instances):
                if not isinstance(instance, dict):
                    raise PackLoadError(
                        f"{path}.meeting_instance[{instance_index}] must be a mapping"
                    )

                instance_path = f"{path}.meeting_instance[{instance_index}]"
                instance_context = {"_parent_meeting_path": path}
                _append_record(
                    records,
                    "meeting_instance",
                    instance,
                    instance_path,
                    instance_context,
                    schema,
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

    files_cfg = pack_schema.files
    schema = pack_schema.raw
    raw_files: dict[str, Any] = {}
    records: list[IngestRecord] = []

    for file_key, filename in files_cfg.items():
        file_path = pack_dir / filename
        if not file_path.is_file():
            raise PackLoadError(f"Missing pack file {filename} in {pack_dir}")
        raw_files[file_key] = load_yaml(file_path)

    organisations = raw_files.get("organisations")
    if not isinstance(organisations, dict):
        raise PackLoadError("organisations.yaml must be a mapping at the top level")
    _walk_organisations(organisations, records, schema)

    acts = raw_files.get("acts", {})
    if isinstance(acts, dict):
        _load_collection_records(acts, "act", "act", records, schema, "acts.yaml")

    meetings = raw_files.get("meetings", {})
    if isinstance(meetings, dict):
        _load_collection_records(meetings, "meeting", "meeting", records, schema, "meetings.yaml")

    rtis = raw_files.get("rtis", {})
    if isinstance(rtis, dict):
        _load_collection_records(rtis, "rti_document", "rti_document", records, schema, "rtis.yaml")

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
