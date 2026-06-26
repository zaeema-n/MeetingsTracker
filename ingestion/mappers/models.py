from __future__ import annotations

from dataclasses import dataclass

from ingestion.models.schema import EntityCreate


@dataclass(frozen=True)
class ParentRelationship:
    """Outgoing edge to apply on the parent entity after the child is created."""

    parent_id: str
    relation: str
    child_id: str


@dataclass
class MappedEntity:
    """Create-path record mapped to a child EntityCreate plus deferred parent-side edges."""

    entity: EntityCreate
    parent_relationships: list[ParentRelationship]
