#!/usr/bin/env python3
"""Validate and optionally resolve bare-id links in a ministry data pack."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

LinkRule = dict[str, Any]


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def index_acts(data: dict) -> dict[str, dict]:
    return {item["id"]: item for item in data.get("acts", [])}


def index_ministries(data: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for ministry in data.get("ministry", []):
        ministry_id = ministry.get("id")
        if ministry_id:
            index[ministry_id] = ministry
    return index


def index_departments(data: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for ministry in data.get("ministry", []):
        for department in ministry.get("department", []):
            department_id = department.get("id")
            if department_id:
                index[department_id] = department
    return index


def index_bodies(data: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for ministry in data.get("ministry", []):
        for department in ministry.get("department", []):
            for body in department.get("body", []):
                body_id = body.get("id")
                if body_id:
                    index[body_id] = body
    return index


def index_meetings(data: dict) -> dict[str, dict]:
    return {item["id"]: item for item in data.get("meetings", [])}


def index_meeting_instances(data: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for meeting in data.get("meetings", []):
        for instance in meeting.get("instances", []):
            instance_id = instance.get("id")
            if instance_id:
                index[instance_id] = instance
    return index


def index_rti_documents(data: dict) -> dict[str, dict]:
    return {item["id"]: item for item in data.get("rti_documents", [])}


INDEX_BUILDERS = {
    "act": ("acts", index_acts),
    "ministry": ("organisation", index_ministries),
    "department": ("organisation", index_departments),
    "body": ("organisation", index_bodies),
    "meeting": ("meetings", index_meetings),
    "meeting_instance": ("meetings", index_meeting_instances),
    "rti_document": ("rtis", index_rti_documents),
}


def normalize_targets(target: str | list[str]) -> list[str]:
    if isinstance(target, list):
        return target
    return [target]


def lookup_ref(
    ref_id: str, targets: list[str], indexes: dict[str, dict[str, dict]]
) -> tuple[str, dict] | None:
    for entity in targets:
        record = indexes[entity].get(ref_id)
        if record is not None:
            return entity, record
    return None


def build_indexes(
    schema: dict, pack_dir: Path
) -> tuple[dict[str, dict[str, dict]], dict[str, Any]]:
    files_cfg = schema["files"]
    data: dict[str, Any] = {}
    indexes: dict[str, dict[str, dict]] = {}

    for entity, (file_key, builder) in INDEX_BUILDERS.items():
        filename = files_cfg[file_key]
        path = pack_dir / filename
        if file_key not in data:
            data[file_key] = load_yaml(path)
        indexes[entity] = builder(data[file_key])

    return indexes, data


def check_duplicate_ids_in_sources(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    act_ids = [a["id"] for a in data["acts"].get("acts", [])]
    if len(act_ids) != len(set(act_ids)):
        errors.append("duplicate act ids in acts.yaml")

    org_ids: list[str] = []
    for ministry in data["organisation"].get("ministry", []):
        if ministry.get("id"):
            org_ids.append(ministry["id"])
        for department in ministry.get("department", []):
            if department.get("id"):
                org_ids.append(department["id"])
            for body in department.get("body", []):
                org_ids.append(body["id"])
    if len(org_ids) != len(set(org_ids)):
        errors.append(
            "duplicate id across ministry/department/body in organisation.yaml"
        )

    meeting_ids = [m["id"] for m in data["meetings"].get("meetings", [])]
    if len(meeting_ids) != len(set(meeting_ids)):
        errors.append("duplicate meeting ids in meetings.yaml")

    instance_ids: list[str] = []
    for meeting in data["meetings"].get("meetings", []):
        for instance in meeting.get("instances", []):
            instance_ids.append(instance["id"])
    if len(instance_ids) != len(set(instance_ids)):
        errors.append("duplicate meeting_instance ids in meetings.yaml")

    rti_ids = [r["id"] for r in data["rtis"].get("rti_documents", [])]
    if len(rti_ids) != len(set(rti_ids)):
        errors.append("duplicate rti_document ids in rtis.yaml")

    return errors


def match_link_rule(
    field: str, parent_key: str | None, rules: list[LinkRule]
) -> LinkRule | None:
    matched: list[LinkRule] = []
    for rule in rules:
        if rule["field"] != field:
            continue
        at = rule.get("at")
        if at is not None:
            allowed = at if isinstance(at, list) else [at]
            if parent_key not in allowed:
                continue
        matched.append(rule)
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        return matched[0]
    return None


def validate_refs(
    node: Any,
    rules: list[LinkRule],
    indexes: dict[str, dict[str, dict]],
    path: str,
    parent_key: str | None,
    errors: list[str],
) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            rule = match_link_rule(key, parent_key, rules)
            child_path = f"{path}.{key}" if path else key
            if rule:
                validate_link_value(value, rule, indexes, child_path, errors)
            else:
                validate_refs(value, rules, indexes, child_path, key, errors)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            child_path = f"{path}[{i}]"
            if isinstance(item, (str, int, float)):
                continue
            validate_refs(item, rules, indexes, child_path, parent_key, errors)


def validate_link_value(
    value: Any,
    rule: LinkRule,
    indexes: dict[str, dict[str, dict]],
    path: str,
    errors: list[str],
) -> None:
    targets = normalize_targets(rule["target"])
    many = rule.get("many", False)

    if many:
        if not isinstance(value, list):
            errors.append(f"{path}: expected list, got {type(value).__name__}")
            return
        ids = value
    else:
        if not isinstance(value, str):
            errors.append(f"{path}: expected id string, got {type(value).__name__}")
            return
        ids = [value]

    for ref_id in ids:
        if not isinstance(ref_id, str):
            errors.append(f"{path}: expected id string in list")
            continue
        if lookup_ref(ref_id, targets, indexes) is None:
            target_label = "|".join(targets)
            errors.append(f"{path}: unknown {target_label} id '{ref_id}'")


def resolve_refs(
    node: Any,
    rules: list[LinkRule],
    indexes: dict[str, dict[str, dict]],
    parent_key: str | None,
) -> Any:
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for key, value in node.items():
            rule = match_link_rule(key, parent_key, rules)
            if rule:
                result[key] = resolve_link_value(value, rule, indexes)
            else:
                result[key] = resolve_refs(value, rules, indexes, key)
        return result
    if isinstance(node, list):
        return [
            resolve_refs(item, rules, indexes, parent_key)
            if not isinstance(item, str)
            else item
            for item in node
        ]
    return node


def resolve_link_value(
    value: Any, rule: LinkRule, indexes: dict[str, dict[str, dict]]
) -> Any:
    targets = normalize_targets(rule["target"])
    many = rule.get("many", False)

    def resolve_one(ref_id: str) -> dict[str, Any]:
        found = lookup_ref(ref_id, targets, indexes)
        if found is None:
            return {"id": ref_id}
        entity, record = found
        return {
            "id": ref_id,
            "_resolved_type": entity,
            "_resolved": deepcopy(record),
        }

    if many:
        return [resolve_one(ref_id) for ref_id in value]

    return resolve_one(value)


def validate_pack(pack_dir: Path) -> list[str]:
    schema_path = pack_dir / "schema.yaml"
    if not schema_path.exists():
        return [f"missing schema.yaml in {pack_dir}"]

    schema = load_yaml(schema_path)
    indexes, data = build_indexes(schema, pack_dir)
    errors = check_duplicate_ids_in_sources(data)

    rules: list[LinkRule] = schema.get("links", [])
    for file_key in ("acts", "organisation", "meetings", "rtis"):
        validate_refs(data[file_key], rules, indexes, file_key, None, errors)

    return errors


def resolve_pack(pack_dir: Path) -> dict[str, Any]:
    schema = load_yaml(pack_dir / "schema.yaml")
    indexes, data = build_indexes(schema, pack_dir)
    rules: list[LinkRule] = schema.get("links", [])

    return {
        file_key: resolve_refs(data[file_key], rules, indexes, None)
        for file_key in ("acts", "organisation", "meetings", "rtis")
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate bare-id links in a ministry data pack."
    )
    parser.add_argument(
        "pack_dir",
        type=Path,
        help="Path to folder containing schema.yaml and data files",
    )
    parser.add_argument(
        "--resolve",
        action="store_true",
        help="Print resolved data as JSON (with _resolved on links)",
    )
    args = parser.parse_args()

    pack_dir = args.pack_dir.resolve()
    if not pack_dir.is_dir():
        print(f"error: not a directory: {pack_dir}", file=sys.stderr)
        return 1

    if args.resolve:
        errors = validate_pack(pack_dir)
        if errors:
            for err in errors:
                print(f"error: {err}", file=sys.stderr)
            return 1
        resolved = resolve_pack(pack_dir)
        print(json.dumps(resolved, indent=2, default=str))
        return 0

    errors = validate_pack(pack_dir)
    if errors:
        for err in errors:
            print(f"error: {err}", file=sys.stderr)
        return 1

    print(f"OK: {pack_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
