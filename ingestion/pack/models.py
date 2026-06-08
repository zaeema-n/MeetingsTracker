from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal

IngestMode = Literal["resolve", "create"]

INGEST_ORDER: list[str] = [
    "president",
    "ministry",
    "department",
    "act",
    "meeting",
    "meeting_instance",
    "board",
    "council",
    "rti_document",
]


@dataclass
class ResolveContext:
    """Shared resolution state for a single ingest run."""

    active_at: str
    president_id: str | None = None
    ministry_id: str | None = None
    department_id: str | None = None

    def set_resolved_id(self, entity_type: str, entity_id: str) -> None:
        if entity_type == "president":
            self.president_id = entity_id
        elif entity_type == "ministry":
            self.ministry_id = entity_id
        elif entity_type == "department":
            self.department_id = entity_id

    def get_parent_id(self, context_key: str) -> str | None:
        mapping = {
            "_parent_president_id": self.president_id,
            "_parent_ministry_id": self.ministry_id,
            "_parent_department_id": self.department_id,
        }
        return mapping.get(context_key)


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
    schema: dict[str, Any]
    active_at: str
    raw_files: dict[str, Any]
    records: list[IngestRecord] = field(default_factory=list)
    indexes: dict[str, dict[str, dict]] = field(default_factory=dict)
    resolve_context: ResolveContext = field(default_factory=lambda: ResolveContext(active_at=""))

    def iter_records(self) -> Iterator[IngestRecord]:
        order = {entity_type: index for index, entity_type in enumerate(INGEST_ORDER)}
        yield from sorted(self.records, key=lambda record: order.get(record.entity_type, 999))

    def records_by_type(self, entity_type: str) -> list[IngestRecord]:
        return [record for record in self.records if record.entity_type == entity_type]
