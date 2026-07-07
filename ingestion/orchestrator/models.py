from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GraphIngestResult:
    active_at: str
    dry_run: bool
    strict: bool
    resolved: int = 0
    created: int = 0
    skipped_existing: int = 0
    dry_run_would_create: int = 0
    parent_updates: int = 0
    dry_run_would_update_parent: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class MetadataIngestResult:
    dry_run: bool
    metadata_updates: int = 0
    dry_run_would_update_metadata: int = 0
    skipped_not_found: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class PhaseRunResult:
    graph: GraphIngestResult | None = None
    metadata: MetadataIngestResult | None = None
