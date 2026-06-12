from __future__ import annotations

import uuid
from typing import Any

from ingestion.models.schema import AddRelation, AddRelationValue, EntityCreate, Kind, NameValue
from ingestion.mappers.errors import MapError
from ingestion.mappers.models import MappedEntity, ParentRelationship
from ingestion.pack.models import IngestRecord, PackState
from ingestion.pack.schema_loader import get_entity_config

INGEST_ENTITY_TYPES = frozenset(
    {
        "president",
        "ministry",
        "department",
        "act",
        "meeting",
        "meeting_instance",
        "board",
        "council",
        "rti_document",
    }
)


def _normalize_targets(target: str | list[str]) -> list[str]:
    if isinstance(target, list):
        return target
    return [target]


def _normalize_id(value: Any) -> str:
    return str(value).strip()


class EntityMapper:
    """Map create-path pack records to OpenGIN EntityCreate payloads."""

    def __init__(self, pack_state: PackState):
        self.pack_state = pack_state
        self.schema = pack_state.schema
        self.active_at = pack_state.active_at
        self.resolve_context = pack_state.resolve_context
        self.indexes = pack_state.indexes

    def map_record(self, record: IngestRecord) -> MappedEntity:
        if record.ingest_mode != "create":
            raise MapError(
                f"{record.entity_type} at {record.path} uses ingest: resolve and is not mappable"
            )

        entity_cfg = get_entity_config(self.schema, record.entity_type)
        record_id = record.record_id
        if not record_id or not str(record_id).strip():
            id_field = entity_cfg.get("id_field", "id")
            raise MapError(f"{record.entity_type} at {record.path} is missing '{id_field}'")

        name_field = entity_cfg.get("name_field", "name")
        name = record.data.get(name_field)
        if not name or not str(name).strip():
            raise MapError(f"{record.entity_type} at {record.path} is missing '{name_field}'")

        child_id = str(record_id).strip()
        kind = self._require_kind(record, entity_cfg)
        parent_relationships = self._build_parent_relationships(record, entity_cfg, child_id)
        link_relationships, deferred_link_relationships = self._build_link_relationships(
            record, child_id
        )
        parent_relationships.extend(deferred_link_relationships)

        return MappedEntity(
            entity=EntityCreate(
                id=child_id,
                kind=kind,
                created=self.active_at,
                name=NameValue(value=str(name).strip(), startTime=self.active_at),
                relationships=link_relationships,
            ),
            parent_relationships=parent_relationships,
        )

    def parent_entity_update(self, parent_relationship: ParentRelationship) -> EntityCreate:
        """Build a minimal parent EntityCreate for attaching an edge to a child."""
        return EntityCreate(
            id=parent_relationship.parent_id,
            relationships=[
                self._make_relation(
                    parent_relationship.relation,
                    parent_relationship.child_id,
                )
            ],
        )

    def _require_kind(self, record: IngestRecord, entity_cfg: dict[str, Any]) -> Kind:
        kind_cfg = entity_cfg.get("kind")
        if not isinstance(kind_cfg, dict):
            raise MapError(
                f"{record.entity_type} at {record.path}: schema missing 'kind' "
                "(major and minor required)"
            )

        major = str(kind_cfg.get("major", "")).strip()
        minor = str(kind_cfg.get("minor", "")).strip()
        if not major:
            raise MapError(
                f"{record.entity_type} at {record.path}: schema kind.major is required "
                "and cannot be empty"
            )
        if not minor:
            raise MapError(
                f"{record.entity_type} at {record.path}: schema kind.minor is required "
                "and cannot be empty"
            )
        return Kind(major=major, minor=minor)

    def _build_parent_relationships(
        self,
        record: IngestRecord,
        entity_cfg: dict[str, Any],
        child_id: str,
    ) -> list[ParentRelationship]:
        parent_relationships: list[ParentRelationship] = []
        for parent_cfg in entity_cfg.get("parent_relationships", []):
            parent_id_from = parent_cfg.get("parent_id_from")
            if not parent_id_from:
                continue

            parent_id = self._resolve_parent_id(record, parent_id_from)
            relation_name = parent_cfg.get("relation", "")
            if not relation_name:
                raise MapError(
                    f"{record.entity_type} at {record.path}: parent relationship missing 'relation'"
                )

            parent_relationships.append(
                ParentRelationship(
                    parent_id=parent_id,
                    relation=relation_name,
                    child_id=child_id,
                )
            )
        return parent_relationships

    def _build_link_relationships(
        self, record: IngestRecord, child_id: str
    ) -> tuple[list[AddRelation], list[ParentRelationship]]:
        """Map schema links rules to child and deferred target-side edges.

        Walks pack_schema.yaml links: reads each rule's YAML field on the record,
        validates referenced ids against pack indexes. Rules with on: target (or
        default on: child) produce either a deferred ParentRelationship
        (target --relation--> child) or an AddRelation on the child EntityCreate
        (child --relation--> target). Skips rules with missing values or relation names.
        """
        child_relationships: list[AddRelation] = []
        deferred_relationships: list[ParentRelationship] = []
        for rule in self.schema.get("links", []):
            ref_values = self._read_link_values(record, rule)
            if not ref_values:
                continue

            relation_name = rule.get("relation", "")
            if not relation_name:
                continue

            edge_on = rule.get("on", "child")
            if edge_on not in ("child", "target"):
                raise MapError(
                    f"{record.entity_type} at {record.path}: link rule for "
                    f"'{rule['field']}' has invalid on: '{edge_on}' (use 'child' or 'target')"
                )

            targets = _normalize_targets(rule["target"])
            for ref_id in ref_values:
                self._assert_link_target(ref_id, targets, record, rule["field"])
                if edge_on == "target":
                    deferred_relationships.append(
                        ParentRelationship(
                            parent_id=ref_id,
                            relation=relation_name,
                            child_id=child_id,
                        )
                    )
                else:
                    child_relationships.append(self._make_relation(relation_name, ref_id))

        return child_relationships, deferred_relationships

    def _read_link_values(self, record: IngestRecord, rule: dict[str, Any]) -> list[str]:
        """Extract bare target ids from a record for one schema links rule.

        Uses rule['field'] and optional rule['at'] to locate the YAML value:
        - no at: read record.data[field]
        - at is an entity-type list: only when record.entity_type is listed
        - at is a container name (e.g. sent_to): read record.data[at][field]

        Returns a list of normalized id strings, or [] when the rule does not
        apply or the field is absent. Raises MapError if the value has the wrong
        shape (string vs list per rule['many']).
        """
        field = rule["field"]
        at = rule.get("at")
        many = rule.get("many", False)

        if isinstance(at, str) and at not in INGEST_ENTITY_TYPES:
            container = record.data.get(at)
            if not isinstance(container, dict):
                return []
            raw_value = container.get(field)
        elif isinstance(at, list):
            if record.entity_type not in at:
                return []
            raw_value = record.data.get(field)
        else:
            raw_value = record.data.get(field)

        if raw_value is None:
            return []

        if many:
            if not isinstance(raw_value, list):
                raise MapError(
                    f"{record.entity_type} at {record.path}.{field}: expected a list of ids"
                )
            return [_normalize_id(ref_id) for ref_id in raw_value if _normalize_id(ref_id)]

        if not isinstance(raw_value, str):
            raise MapError(
                f"{record.entity_type} at {record.path}.{field}: expected an id string"
            )
        normalized = _normalize_id(raw_value)
        return [normalized] if normalized else []

    def _resolve_parent_id(self, record: IngestRecord, parent_id_from: str) -> str:
        parent_id = self.resolve_context.get_parent_id_for_record(
            record.context, parent_id_from
        )
        if parent_id:
            return parent_id

        path_key = parent_id_from.replace("_id", "_path")
        parent_path = record.context.get(path_key)
        if parent_path:
            parent_record = self.pack_state.record_at_path(parent_path)
            if parent_record and parent_record.record_id:
                return _normalize_id(parent_record.record_id)

        raise MapError(
            f"{record.entity_type} at {record.path}: unresolved parent for {parent_id_from}"
        )

    def _assert_link_target(
        self,
        ref_id: str,
        targets: list[str],
        record: IngestRecord,
        field: str,
    ) -> None:
        """Verify a link reference id exists in the pack for an allowed target type.

        Checks pack_state.indexes for ref_id under any of targets (e.g. act, meeting).
        Returns None when the id is found. Raises MapError if ref_id is not indexed
        under any allowed target type.
        """
        for target_type in targets:
            if ref_id in self.indexes.get(target_type, {}):
                return

        target_label = "|".join(targets)
        raise MapError(
            f"{record.entity_type} at {record.path}.{field}: unknown {target_label} id '{ref_id}'"
        )

    def _make_relation(self, relation_name: str, related_entity_id: str) -> AddRelation:
        relation_id = str(uuid.uuid4())
        return AddRelation(
            key=relation_id,
            value=AddRelationValue(
                id=relation_id,
                relatedEntityId=related_entity_id,
                startTime=self.active_at,
                name=relation_name,
            ),
        )
