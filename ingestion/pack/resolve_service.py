from __future__ import annotations

from typing import Any

from ingestion.models.schema import Entity, Kind, Relation
from ingestion.pack.errors import ResolveError
from ingestion.pack.models import IngestRecord, PackState, ResolveContext
from ingestion.pack.schema_loader import get_entity_config
from ingestion.services.read_service import ReadService

RESOLVE_ENTITY_TYPES = ("government", "president", "ministry", "department")

PARENT_PATH_KEY = {
    "president": "_parent_government_path",
    "ministry": "_parent_president_path",
    "department": "_parent_ministry_path",
}

PARENT_RELATION = {
    "president": "AS_PRESIDENT",
    "ministry": "AS_MINISTER",
    "department": "AS_DEPARTMENT",
}


def _normalize_name(value: str | None) -> str:
    return str(value or "").strip()


def _allowed_kinds_from_config(kind_cfg: dict[str, Any]) -> list[Kind]:
    major = str(kind_cfg.get("major", "")).strip()
    minors = kind_cfg.get("minors")
    if isinstance(minors, list) and minors:
        return [
            Kind(major=major, minor=str(minor).strip())
            for minor in minors
            if str(minor).strip()
        ]

    minor = str(kind_cfg.get("minor", "")).strip()
    if major or minor:
        return [Kind(major=major, minor=minor)]
    return [Kind()]


def _kind_matches(entity: Entity, expected: Kind) -> bool:
    return entity.kind.major == expected.major and entity.kind.minor == expected.minor


def _kind_matches_allowed(entity: Entity, allowed_kinds: list[Kind]) -> bool:
    return any(_kind_matches(entity, kind) for kind in allowed_kinds)


def _entity_name_matches(entity: Entity, expected_name: str) -> bool:
    return _normalize_name(entity.name) == expected_name


def _resolve_without_active_at(entity_cfg: dict[str, Any]) -> bool:
    return bool(entity_cfg.get("resolve_without_active_at", False))


class ResolveService:
    """Resolve government → president → ministry → department records via ReadService."""

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
        allowed_kinds = _allowed_kinds_from_config(entity_cfg.get("kind", {}))

        if entity_type == "government" or _resolve_without_active_at(entity_cfg):
            entity_id = await self._resolve_root_entity(
                entity_label=entity_type,
                expected_name=expected_name,
                allowed_kinds=allowed_kinds,
                path=record.path,
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
                allowed_kinds=allowed_kinds,
                active_at=context.active_at,
                path=record.path,
            )

        context.register_resolution(record.path, entity_type, entity_id)
        return entity_id

    async def _resolve_root_entity(
        self,
        *,
        entity_label: str,
        expected_name: str,
        allowed_kinds: list[Kind],
        path: str,
    ) -> str:
        """Resolve a root entity by name and kind only (no active-at filtering)."""
        matches: list[str] = []
        seen_ids: set[str] = set()

        for kind in allowed_kinds:
            candidates = await self.read_service.get_entities(
                Entity(name=expected_name, kind=kind)
            )
            for candidate in candidates:
                if not candidate.id or candidate.id in seen_ids:
                    continue
                if not _kind_matches_allowed(candidate, allowed_kinds):
                    continue
                if _entity_name_matches(candidate, expected_name):
                    seen_ids.add(candidate.id)
                    matches.append(candidate.id)

        return self._require_unique_match(
            matches,
            entity_label=entity_label,
            name=expected_name,
            path=path,
        )

    async def _fetch_entity_by_id(
        self,
        entity_id: str,
        allowed_kinds: list[Kind],
    ) -> Entity | None:
        """Load an entity by id; search ignores kind, so filter locally."""
        normalized_id = str(entity_id).strip()
        candidates = await self.read_service.get_entities(Entity(id=normalized_id))

        for candidate in candidates:
            if str(candidate.id).strip() != normalized_id:
                continue
            if _kind_matches_allowed(candidate, allowed_kinds):
                return candidate

        return None

    async def _resolve_child_by_relation(
        self,
        entity_type: str,
        parent_id: str,
        relation_name: str,
        expected_name: str,
        allowed_kinds: list[Kind],
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

            related = await self._fetch_entity_by_id(related_id, allowed_kinds)
            if not related:
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
        active_at: str | None = None,
        parent_id: str | None = None,
        relation_name: str | None = None,
    ) -> str:
        if len(matches) == 1:
            return matches[0]

        scope = f" at {path}"
        if parent_id and relation_name:
            scope = f" under {parent_id} via {relation_name}{scope}"
        if active_at:
            scope = f"{scope} on {active_at}"

        if not matches:
            raise ResolveError(
                f"No {entity_label} named '{name}' found{scope}"
            )

        raise ResolveError(
            f"Ambiguous {entity_label} named '{name}'{scope}: matched {len(matches)} entities ({', '.join(matches)})"
        )
