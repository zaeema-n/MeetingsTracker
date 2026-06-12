from __future__ import annotations

from pathlib import Path

from ingestion.mappers import EntityMapper
from ingestion.mappers.models import MappedEntity
from ingestion.models.schema import Entity, Kind
from ingestion.orchestrator.errors import IngestStrictError
from ingestion.orchestrator.models import IngestResult
from ingestion.pack import ResolveService, load_pack
from ingestion.pack.errors import PackLoadError, ResolveError
from ingestion.pack.models import IngestRecord, PackState
from ingestion.services.ingestion_service import IngestionService
from ingestion.services.read_service import ReadService
from ingestion.utils.logger import logger


def _kind_label(kind: Kind) -> str:
    return f"{kind.major}/{kind.minor}"


class IngestRunner:
    """Orchestrate resolve + create ingest for a ministry pack."""

    def __init__(
        self,
        read_service: ReadService | None = None,
        ingestion_service: IngestionService | None = None,
    ):
        self.read_service = read_service or ReadService()
        self.ingestion_service = ingestion_service or IngestionService()
        self.resolve_service = ResolveService(self.read_service)

    async def run(
        self,
        pack_dir: Path,
        *,
        active_at: str,
        schema_path: Path | None = None,
        dry_run: bool = False,
        strict: bool = False,
    ) -> IngestResult:
        active_at = str(active_at).strip()
        if not active_at:
            raise PackLoadError("active_at is required")

        pack_state = load_pack(pack_dir, active_at=active_at, schema_path=schema_path)
        result = IngestResult(
            active_at=active_at,
            dry_run=dry_run,
            strict=strict,
        )

        logger.info(
            "Starting ingest for %s (active_at=%s, dry_run=%s, strict=%s)",
            pack_state.pack_dir,
            active_at,
            dry_run,
            strict,
        )

        await self.resolve_service.resolve_pack(pack_state)
        mapper = EntityMapper(pack_state)

        for record in pack_state.iter_records():
            if record.ingest_mode == "resolve":
                entity_id = pack_state.resolve_context.get_resolved_id(record.path)
                if not entity_id:
                    raise ResolveError(
                        f"{record.entity_type} at {record.path} was not resolved"
                    )
                result.resolved += 1
                logger.info(
                    "[RESOLVE] %s %s -> %s",
                    record.entity_type,
                    record.path,
                    entity_id,
                )
                continue

            await self._ingest_create_record(
                record=record,
                pack_state=pack_state,
                mapper=mapper,
                result=result,
                dry_run=dry_run,
                strict=strict,
            )

        self._log_summary(result)
        return result

    async def _ingest_create_record(
        self,
        *,
        record: IngestRecord,
        pack_state: PackState,
        mapper: EntityMapper,
        result: IngestResult,
        dry_run: bool,
        strict: bool,
    ) -> None:
        mapped = mapper.map_record(record)
        entity = mapped.entity
        kind_label = _kind_label(entity.kind)
        exists = await self._entity_exists(entity.id, entity.kind)

        if exists:
            if strict:
                raise IngestStrictError(
                    f"{kind_label} {entity.id} at {record.path} already exists in OpenGIN "
                    f"(active_at={pack_state.active_at})"
                )
            result.skipped_existing += 1
            logger.info(
                "[SKIP] %s %s at %s (already in DB)",
                kind_label,
                entity.id,
                record.path,
            )
            return

        if dry_run:
            result.dry_run_would_create += 1
            logger.info(
                "[DRY-RUN] Would create %s %s at %s",
                kind_label,
                entity.id,
                record.path,
            )
            self._log_dry_run_parent_updates(mapped, result)
            return

        await self.ingestion_service.create_entity(entity)
        result.created += 1
        logger.success(
            "[CREATE] %s %s at %s",
            kind_label,
            entity.id,
            record.path,
        )

        await self._apply_parent_relationships(mapper, mapped, result)

    async def _entity_exists(self, entity_id: str, kind: Kind) -> bool:
        candidates = await self.read_service.get_entities(
            Entity(id=entity_id, kind=kind)
        )
        normalized_id = str(entity_id).strip()
        return any(str(candidate.id).strip() == normalized_id for candidate in candidates)

    async def _apply_parent_relationships(
        self,
        mapper: EntityMapper,
        mapped: MappedEntity,
        result: IngestResult,
    ) -> None:
        for parent_relationship in mapped.parent_relationships:
            parent_update = mapper.parent_entity_update(parent_relationship)
            await self.ingestion_service.update_entity(
                parent_relationship.parent_id,
                parent_update,
            )
            result.parent_updates += 1
            logger.info(
                "[UPDATE] parent %s %s -> %s",
                parent_relationship.parent_id,
                parent_relationship.relation,
                parent_relationship.child_id,
            )

    def _log_dry_run_parent_updates(
        self,
        mapped: MappedEntity,
        result: IngestResult,
    ) -> None:
        for parent_relationship in mapped.parent_relationships:
            result.dry_run_would_update_parent += 1
            logger.info(
                "[DRY-RUN] Would update parent %s %s -> %s",
                parent_relationship.parent_id,
                parent_relationship.relation,
                parent_relationship.child_id,
            )

    def _log_summary(self, result: IngestResult) -> None:
        logger.success(
            "Ingest complete (active_at=%s, dry_run=%s, strict=%s)",
            result.active_at,
            result.dry_run,
            result.strict,
        )
        logger.success("  resolved: %s", result.resolved)
        logger.success("  created: %s", result.created)
        logger.success("  skipped_existing: %s", result.skipped_existing)
        if result.dry_run:
            logger.success("  dry_run_would_create: %s", result.dry_run_would_create)
            logger.success(
                "  dry_run_would_update_parent: %s",
                result.dry_run_would_update_parent,
            )
        else:
            logger.success("  parent_updates: %s", result.parent_updates)
