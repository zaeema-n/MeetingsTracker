from __future__ import annotations

from typing import Any

from ingestion.models.schema import Entity, Kind, Relation
from ingestion.pack.errors import ResolveError
from ingestion.pack.models import IngestRecord, PackState, ResolveContext
from ingestion.pack.schema_loader import get_entity_config
from ingestion.services.read_service import ReadService

RESOLVE_ENTITY_TYPES = ("president", "ministry", "department")

PARENT_PATH_KEY = {
    "ministry": "_parent_president_path",
    "department": "_parent_ministry_path",
}

PARENT_RELATION = {
    "ministry": "AS_MINISTER",
    "department": "AS_DEPARTMENT",
}


def _normalize_name(value: str | None) -> str:
    return str(value or "").strip()


def _kind_from_config(kind_cfg: dict[str, Any]) -> Kind:
    return Kind(major=kind_cfg.get("major", ""), minor=kind_cfg.get("minor", ""))


def _kind_matches(entity: Entity, expected: Kind) -> bool:
    return entity.kind.major == expected.major and entity.kind.minor == expected.minor


def _entity_name_matches(entity: Entity, expected_name: str) -> bool:
    return _normalize_name(entity.name) == expected_name


class ResolveService:
    """Resolve president → ministry → department records via ReadService."""

    def __init__(self, read_service: ReadService):
        self.read_service = read_service

    async def resolve_pack(self, pack_state: PackState) -> ResolveContext:
        context = pack_state.resolve_context
        for record in pack_state.records:
            if record.ingest_mode != "resolve":
                continue
            if record.entity_type not in RESOLVE_ENTITY_TYPES:
                continue
            await self.resolve_record(record, pack_state.schema, context)
        return context

    async def resolve_record(
        self,
        record: IngestRecord,
        schema: dict[str, Any],
        context: ResolveContext,
    ) -> str:
        entity_type = record.entity_type
        expected_name = _normalize_name(record.name)
        if not expected_name:
            raise ResolveError(
                f"{entity_type} at {record.path} requires 'name' for ingest: resolve"
            )

        entity_cfg = get_entity_config(schema, entity_type)
        expected_kind = _kind_from_config(entity_cfg.get("kind", {}))

        if entity_type == "president":
            entity_id = await self._resolve_president(
                expected_name, expected_kind, context.active_at, record.path
            )
        else:
            parent_path_key = PARENT_PATH_KEY[entity_type]
            parent_path = record.context.get(parent_path_key)
            if not parent_path:
                raise ResolveError(
                    f"{entity_type} at {record.path} is missing parent path '{parent_path_key}'"
                )

            parent_id = context.get_resolved_id(parent_path)
            if not parent_id:
                raise ResolveError(
                    f"{entity_type} at {record.path} requires resolved parent at {parent_path}"
                )

            relation_name = PARENT_RELATION[entity_type]
            entity_id = await self._resolve_child_by_relation(
                entity_type=entity_type,
                parent_id=parent_id,
                relation_name=relation_name,
                expected_name=expected_name,
                expected_kind=expected_kind,
                active_at=context.active_at,
                path=record.path,
            )

        context.register_resolution(record.path, entity_type, entity_id)
        return entity_id

    async def _resolve_president(
        self,
        expected_name: str,
        expected_kind: Kind,
        active_at: str,
        path: str,
    ) -> str:
        candidates = await self.read_service.get_entities(
            Entity(name=expected_name, kind=expected_kind)
        )

        matches: list[str] = []
        for candidate in candidates:
            if not candidate.id:
                continue
            if not _kind_matches(candidate, expected_kind):
                continue
            if _entity_name_matches(candidate, expected_name):
                matches.append(candidate.id)

        return self._require_unique_match(
            matches,
            entity_label="president",
            name=expected_name,
            path=path,
            active_at=active_at,
        )

    async def _resolve_child_by_relation(
        self,
        entity_type: str,
        parent_id: str,
        relation_name: str,
        expected_name: str,
        expected_kind: Kind,
        active_at: str,
        path: str,
    ) -> str:
        relations = await self.read_service.fetch_relations(
            parent_id,
            Relation(
                name=relation_name,
                activeAt=active_at,
            ),
        )

        matches: list[str] = []
        seen_ids: set[str] = set()
        for relation in relations:
            related_id = str(relation.relatedEntityId or "").strip()
            if not related_id or related_id in seen_ids:
                continue
            seen_ids.add(related_id)

            related_entities = await self.read_service.get_entities(
                Entity(id=related_id, kind=expected_kind)
            )
            if not related_entities:
                continue

            related = related_entities[0]
            if not _kind_matches(related, expected_kind):
                continue
            if _entity_name_matches(related, expected_name):
                matches.append(related_id)

        return self._require_unique_match(
            matches,
            entity_label=entity_type,
            name=expected_name,
            path=path,
            active_at=active_at,
            parent_id=parent_id,
            relation_name=relation_name,
        )

    def _require_unique_match(
        self,
        matches: list[str],
        *,
        entity_label: str,
        name: str,
        path: str,
        active_at: str,
        parent_id: str | None = None,
        relation_name: str | None = None,
    ) -> str:
        if len(matches) == 1:
            return matches[0]

        scope = f" at {path} on {active_at}"
        if parent_id and relation_name:
            scope = f" under {parent_id} via {relation_name}{scope}"

        if not matches:
            raise ResolveError(
                f"No {entity_label} named '{name}' found{scope}"
            )

        raise ResolveError(
            f"Ambiguous {entity_label} named '{name}'{scope}: matched {len(matches)} entities ({', '.join(matches)})"
        )
