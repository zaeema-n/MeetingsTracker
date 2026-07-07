from __future__ import annotations

from pathlib import Path

from ingestion.models.schema import Entity, EntityCreate
from ingestion.orchestrator.models import MetadataIngestResult
from ingestion.pack import load_metadata_files
from ingestion.services.ingestion_service import IngestionService
from ingestion.services.read_service import ReadService
from ingestion.utils.logger import logger


class MetadataIngestRunner:
    """Apply metadata sidecar files to existing OpenGIN entities."""

    def __init__(
        self,
        read_service: ReadService | None = None,
        ingestion_service: IngestionService | None = None,
    ):
        self.read_service = read_service or ReadService()
        self.ingestion_service = ingestion_service or IngestionService()

    async def run(
        self,
        pack_dir: Path,
        *,
        dry_run: bool = False,
    ) -> MetadataIngestResult:
        documents = load_metadata_files(pack_dir)
        result = MetadataIngestResult(dry_run=dry_run)

        logger.info(
            "Starting metadata ingest for %s (dry_run=%s)",
            pack_dir,
            dry_run,
        )

        for document in documents:
            entity_id = document.entity_key.strip()
            candidates = await self.read_service.get_entities(Entity(id=entity_id))
            exists = any(str(candidate.id).strip() == entity_id for candidate in candidates)
            if not exists:
                result.skipped_not_found += 1
                logger.warning(
                    "[SKIP] metadata %s from %s (entity not found)",
                    entity_id,
                    document.source_path.name,
                )
                continue

            if dry_run:
                result.dry_run_would_update_metadata += 1
                logger.info(
                    "[DRY-RUN] Would update metadata %s from %s (%s keys)",
                    entity_id,
                    document.source_path.name,
                    len(document.metadata),
                )
                continue

            await self.ingestion_service.update_entity(
                entity_id,
                EntityCreate(id=entity_id, metadata=document.metadata),
            )
            result.metadata_updates += 1
            logger.success(
                "[UPDATE] metadata %s from %s (%s keys)",
                entity_id,
                document.source_path.name,
                len(document.metadata),
            )

        self._log_summary(result)
        return result

    def _log_summary(self, result: MetadataIngestResult) -> None:
        logger.success("Metadata ingest complete (dry_run=%s)", result.dry_run)
        if result.dry_run:
            logger.success(
                "  dry_run_would_update_metadata: %s",
                result.dry_run_would_update_metadata,
            )
        else:
            logger.success("  metadata_updates: %s", result.metadata_updates)
        logger.success("  skipped_not_found: %s", result.skipped_not_found)
