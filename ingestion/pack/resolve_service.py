from __future__ import annotations

from typing import Any

from ingestion.models.schema import Entity, Kind, Relation
from ingestion.pack.errors import ResolveError
from ingestion.pack.models import IngestRecord, PackState, ResolveContext
from ingestion.pack.schema_loader import PackSchema
from ingestion.services.read_service import ReadService
from ingestion.utils.util_functions import Util


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
    return Util.normalize_name(entity.name) == expected_name


def _collect_entity_ids_by_exact_name(
    candidates: list[Entity],
    expected_name: str
) -> list[str]:
    """Keep entities whose decoded name exactly matches (search API is partial)."""
    matches: list[str] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        if not candidate.id or candidate.id in seen_ids:
            continue
        if _entity_name_matches(candidate, expected_name):
            seen_ids.add(candidate.id)
            matches.append(candidate.id)
    return matches


def _resolve_without_active_at(entity_cfg: dict[str, Any]) -> bool:
    return bool(entity_cfg.get("resolve_without_active_at", False))


class ResolveService:
    """Resolve pack records whose schema default_ingest is resolve via ReadService."""

    def __init__(self, read_service: ReadService):
        self.read_service = read_service

    async def resolve_pack(self, pack_state: PackState) -> ResolveContext:
        context = pack_state.resolve_context
        pack_schema = pack_state.pack_schema
        for record in pack_state.records:
            if record.ingest_mode != "resolve":
                continue
            if record.entity_type not in pack_schema.resolve_types:
                continue
            await self.resolve_record(record, pack_schema, context)
        return context

    async def resolve_record(
        self,
        record: IngestRecord,
        pack_schema: PackSchema,
        context: ResolveContext,
    ) -> str:
        entity_type = record.entity_type
        expected_name = Util.normalize_name(record.name)
        if not expected_name:
            raise ResolveError(
                f"{entity_type} at {record.path} requires 'name' for ingest: resolve"
            )

        entity_cfg = pack_schema.entity_config(entity_type)
        allowed_kinds = _allowed_kinds_from_config(entity_cfg.get("kind", {}))
        parent_relationships = entity_cfg.get("parent_relationships") or []

        if _resolve_without_active_at(entity_cfg) or not parent_relationships:
            entity_id = await self._resolve_root_entity(
                entity_label=entity_type,
                expected_name=expected_name,
                allowed_kinds=allowed_kinds,
                path=record.path,
            )
        else:
            tree_parent_type = record.context.get("_tree_parent_type")
            if not tree_parent_type:
                raise ResolveError(
                    f"{entity_type} at {record.path} is missing context '_tree_parent_type'"
                )

            relationship = pack_schema.parent_relationship_for_tree_parent(
                entity_type, str(tree_parent_type)
            )
            parent_id_from = str(relationship.get("parent_id_from", "")).strip()
            if not parent_id_from:
                raise ResolveError(
                    f"{entity_type} at {record.path} has no parent_id_from in schema"
                )

            parent_id = context.get_parent_id_for_record(record.context, parent_id_from)
            if not parent_id:
                path_key = parent_id_from.replace("_id", "_path")
                parent_path = record.context.get(path_key)
                raise ResolveError(
                    f"{entity_type} at {record.path} requires resolved parent at {parent_path}"
                )

            relation_name = str(relationship.get("relation", "")).strip()
            if not relation_name:
                raise ResolveError(
                    f"{entity_type} at {record.path} has no relation in schema"
                )

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

    async def _search_entity_ids_by_exact_name(
        self,
        expected_name: str,
        allowed_kinds: list[Kind],
    ) -> list[str]:
        candidates: list[Entity] = []
        for kind in allowed_kinds:
            candidates.extend(
                await self.read_service.get_entities(
                    Entity(name=expected_name, kind=kind)
                )
            )
        return _collect_entity_ids_by_exact_name(candidates, expected_name)

    async def _resolve_root_entity(
        self,
        *,
        entity_label: str,
        expected_name: str,
        allowed_kinds: list[Kind],
        path: str,
    ) -> str:
        """Resolve a root entity by name and kind only (no active-at filtering)."""
        matches = await self._search_entity_ids_by_exact_name(
            expected_name, allowed_kinds
        )

        return self._require_unique_match(
            matches,
            entity_label=entity_label,
            name=expected_name,
            path=path,
        )

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

        related_ids = {
            related_id
            for relation in relations
            if (related_id := str(relation.relatedEntityId or "").strip())
        }

        name_match_ids = await self._search_entity_ids_by_exact_name(
            expected_name, allowed_kinds
        )
        matches = [
            entity_id for entity_id in name_match_ids if entity_id in related_ids
        ]

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
