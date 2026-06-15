from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal

from ingestion.pack.schema_loader import PackSchema

IngestMode = Literal["resolve", "create"]


@dataclass
class ResolveContext:
    """Shared resolution state for a single ingest run."""

    active_at: str
    resolved_by_path: dict[str, str] = field(default_factory=dict)

    def register_resolution(self, path: str, entity_type: str, entity_id: str) -> None:
        """Record an OpenGIN entity id for a resolved pack path."""
        self.resolved_by_path[path] = entity_id

    def get_resolved_id(self, path: str) -> str | None:
        return self.resolved_by_path.get(path)

    def get_parent_id_for_record(
        self, record_context: dict[str, Any], context_key: str
    ) -> str | None:
        """Look up a parent id via the ancestor path stored in record context."""
        path_key = context_key.replace("_id", "_path")
        parent_path = record_context.get(path_key)
        if parent_path:
            return self.resolved_by_path.get(parent_path)
        return None


@dataclass
class IngestRecord:
    """One entity instance from the pack ready for resolve or create."""

    entity_type: str
    ingest_mode: IngestMode
    data: dict[str, Any]
    path: str
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def record_id(self) -> str | None:
        return self.data.get("id")

    @property
    def name(self) -> str | None:
        return self.data.get("name")


@dataclass
class PackState:
    pack_dir: Path
    pack_schema: PackSchema
    active_at: str
    raw_files: dict[str, Any]
    records: list[IngestRecord] = field(default_factory=list)
    indexes: dict[str, dict[str, dict]] = field(default_factory=dict)
    resolve_context: ResolveContext = field(default_factory=lambda: ResolveContext(active_at=""))

    @property
    def schema(self) -> dict[str, Any]:
        """Raw schema dict for callers not yet migrated to :class:`PackSchema`."""
        return self.pack_schema.raw

    def iter_records(self) -> Iterator[IngestRecord]:
        order = {
            entity_type: index
            for index, entity_type in enumerate(self.pack_schema.ingest_order)
        }
        yield from sorted(self.records, key=lambda record: order.get(record.entity_type, 999))

    def records_by_type(self, entity_type: str) -> list[IngestRecord]:
        return [record for record in self.records if record.entity_type == entity_type]

    def record_at_path(self, path: str) -> IngestRecord | None:
        for record in self.records:
            if record.path == path:
                return record
        return None
