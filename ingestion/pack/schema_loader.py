from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ingestion.pack.errors import PackSchemaError

DEFAULT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schema" / "pack_schema.yaml"
)


@dataclass(frozen=True)
class PackSchema:
    """Compiled view of pack_schema.yaml for schema-driven ingestion."""

    raw: dict[str, Any]  # full parsed YAML; used by files, links, etc.
    path: Path  # schema file path (for error messages)
    ingest_order: tuple[str, ...]  # processing order for all entity types
    entity_types: frozenset[str]  # all known entity type names
    resolve_types: frozenset[str]  # types looked up in OpenGIN (default_ingest: resolve)
    create_types: frozenset[str]  # types created from pack ids (default_ingest: create)
    nested_children: dict[str, tuple[str, ...]]  # parent type → YAML keys that may nest under it
    _entities: dict[str, dict[str, Any]]  # per-type config from entities: block
    _allowed_tree_parents: dict[str, tuple[str, ...]]  # child → valid parent types in schema
    _parent_relationships: dict[str, tuple[dict[str, Any], ...]]  # child → parent_relationships entries
    _root_entities_by_file: dict[str, tuple[str, ...]]  # file key → top-level entity types in that file

    @property
    def files(self) -> dict[str, str]:
        """Map pack file keys to filenames (e.g. ``organisations`` → ``organisations.yaml``)."""
        return self.raw["pack"]["files"]

    @property
    def links(self) -> list[dict[str, Any]]:
        """Cross-file link rules from the schema (``mandated_by``, ``meetings``, etc.)."""
        links = self.raw.get("links", [])
        if not isinstance(links, list):
            raise PackSchemaError(f"Pack schema links must be a list: {self.path}")
        return links

    def entity_config(self, entity_type: str) -> dict[str, Any]:
        """Return the raw ``entities.<type>`` block (kind, file, id_field, …)."""
        if entity_type not in self._entities:
            raise PackSchemaError(f"Unknown entity type in schema: {entity_type}")
        return self._entities[entity_type]

    def yaml_key_for(self, entity_type: str) -> str:
        """Return the YAML nested-list key for an entity type (same as the type name)."""
        if entity_type not in self.entity_types:
            raise PackSchemaError(f"Unknown entity type in schema: {entity_type}")
        return entity_type

    def allowed_tree_parents(self, entity_type: str) -> tuple[str, ...]:
        """Return parent types that may own ``entity_type`` in the YAML tree."""
        if entity_type not in self.entity_types:
            raise PackSchemaError(f"Unknown entity type in schema: {entity_type}")
        return self._allowed_tree_parents[entity_type]

    def root_entities_for_file(self, file_key: str) -> tuple[str, ...]:
        """Return top-level entity types for a pack file (types with no tree parents)."""
        return self._root_entities_by_file.get(file_key, ())

    def parent_relationship_for_tree_parent(
        self, entity_type: str, tree_parent_type: str
    ) -> dict[str, Any]:
        """Return the single ``parent_relationships`` entry matching actual YAML nesting."""
        if entity_type not in self.entity_types:
            raise PackSchemaError(f"Unknown entity type in schema: {entity_type}")

        matches = [
            relationship
            for relationship in self._parent_relationships.get(entity_type, ())
            if relationship.get("parent_type") == tree_parent_type
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise PackSchemaError(
                f"No parent_relationship for {entity_type} with tree parent "
                f"{tree_parent_type}"
            )
        raise PackSchemaError(
            f"Ambiguous parent_relationship for {entity_type} and {tree_parent_type}"
        )

    @staticmethod
    def parent_path_context_key(parent_type: str) -> str:
        """Context key for a parent's pack path (e.g. ``department`` → ``_parent_department_path``)."""
        return f"_parent_{parent_type}_path"

    def requires_active_at(self) -> bool:
        """Whether ingest requires an ``--active-at`` date."""
        return bool(self.raw.get("pack", {}).get("requires_active_at", False))


def load_pack_schema(schema_path: Path | None = None) -> PackSchema:
    """Load, validate, and compile pack_schema.yaml into a :class:`PackSchema`."""
    path = schema_path or DEFAULT_SCHEMA_PATH
    if not path.is_file():
        raise PackSchemaError(f"Pack schema not found: {path}")

    with path.open(encoding="utf-8") as file:
        schema = yaml.safe_load(file)

    if not isinstance(schema, dict):
        raise PackSchemaError(f"Pack schema must be a mapping: {path}")

    if "entities" not in schema or "pack" not in schema:
        raise PackSchemaError(f"Pack schema missing 'pack' or 'entities': {path}")

    files = schema.get("pack", {}).get("files")
    if not isinstance(files, dict) or not files:
        raise PackSchemaError(f"Pack schema missing pack.files: {path}")

    entities = schema.get("entities", {})
    if not isinstance(entities, dict) or not entities:
        raise PackSchemaError(f"Pack schema missing entities: {path}")

    ingest_order = _validate_ingest_order(schema, path)
    entity_types = frozenset(entities)
    _validate_parent_relationships(entities, entity_types, path)
    _validate_links(schema.get("links", []), entity_types, path)

    allowed_tree_parents = _build_allowed_tree_parents(entities, entity_types)
    parent_relationships = _build_parent_relationships(entities, entity_types)
    nested_children = _build_nested_children(entities, entity_types)
    root_entities_by_file = _build_root_entities_by_file(entities, allowed_tree_parents)
    resolve_types, create_types = _build_ingest_type_sets(entities, entity_types, path)

    return PackSchema(
        raw=schema,
        path=path,
        ingest_order=ingest_order,
        entity_types=entity_types,
        resolve_types=resolve_types,
        create_types=create_types,
        nested_children=nested_children,
        _entities=dict(entities),
        _allowed_tree_parents=allowed_tree_parents,
        _parent_relationships=parent_relationships,
        _root_entities_by_file=root_entities_by_file,
    )


def load_schema(schema_path: Path | None = None) -> dict[str, Any]:
    """Load raw pack schema YAML (validated). Prefer load_pack_schema() for ingestion."""
    return load_pack_schema(schema_path).raw


def get_entity_config(schema: dict[str, Any], entity_type: str) -> dict[str, Any]:
    """Look up ``entities.<type>`` from a raw schema dict (legacy helper)."""
    entities = schema.get("entities", {})
    if entity_type not in entities:
        raise PackSchemaError(f"Unknown entity type in schema: {entity_type}")
    return entities[entity_type]


def schema_requires_active_at(schema: dict[str, Any]) -> bool:
    """Return whether a raw schema dict requires ``--active-at``."""
    return bool(schema.get("pack", {}).get("requires_active_at", False))


def _validate_ingest_order(schema: dict[str, Any], path: Path) -> tuple[str, ...]:
    """Ensure ingest_order lists every entity exactly once; return the ordered tuple."""
    entities = schema.get("entities", {})
    if not isinstance(entities, dict) or not entities:
        raise PackSchemaError(f"Pack schema missing entities: {path}")

    ingest_order = schema.get("ingest_order")
    if not isinstance(ingest_order, list) or not ingest_order:
        raise PackSchemaError(f"Pack schema missing ingest_order: {path}")

    entity_types = set(entities)
    seen: set[str] = set()
    duplicates: list[str] = []
    unknown: list[str] = []
    ordered: list[str] = []

    for entry in ingest_order:
        if not isinstance(entry, str):
            raise PackSchemaError(
                f"Pack schema ingest_order entries must be strings: {path}"
            )
        if entry in seen:
            duplicates.append(entry)
        seen.add(entry)
        ordered.append(entry)
        if entry not in entity_types:
            unknown.append(entry)

    if unknown:
        raise PackSchemaError(
            f"Pack schema ingest_order references unknown entity types "
            f"{sorted(unknown)}: {path}"
        )
    if duplicates:
        raise PackSchemaError(
            f"Pack schema ingest_order lists duplicate entity types "
            f"{sorted(set(duplicates))}: {path}"
        )

    missing = sorted(entity_types - seen)
    if missing:
        raise PackSchemaError(
            f"Pack schema ingest_order missing entity types {missing}: {path}"
        )

    return tuple(ordered)


def _validate_parent_relationships(
    entities: dict[str, dict[str, Any]],
    entity_types: frozenset[str],
    path: Path,
) -> None:
    """Ensure parent_relationships reference valid parent types and never self-reference."""
    for entity_type, entity_cfg in entities.items():
        relationships = entity_cfg.get("parent_relationships", [])
        if relationships is None:
            continue
        if not isinstance(relationships, list):
            raise PackSchemaError(
                f"Pack schema parent_relationships for {entity_type} must be a list: {path}"
            )

        for relationship in relationships:
            if not isinstance(relationship, dict):
                raise PackSchemaError(
                    f"Pack schema parent_relationships entry for {entity_type} "
                    f"must be a mapping: {path}"
                )
            parent_type = relationship.get("parent_type")
            if not isinstance(parent_type, str):
                raise PackSchemaError(
                    f"Pack schema parent_relationships for {entity_type} missing "
                    f"parent_type: {path}"
                )
            if parent_type not in entity_types:
                raise PackSchemaError(
                    f"Pack schema parent_relationships for {entity_type} references "
                    f"unknown parent_type {parent_type!r}: {path}"
                )
            if parent_type == entity_type:
                raise PackSchemaError(
                    f"Pack schema parent_relationships for {entity_type} cannot "
                    f"list itself as parent_type: {path}"
                )


def _validate_links(
    links: Any, entity_types: frozenset[str], path: Path
) -> None:
    """Ensure each link rule's target references a known entity type."""
    if links is None:
        return
    if not isinstance(links, list):
        raise PackSchemaError(f"Pack schema links must be a list: {path}")

    for index, link in enumerate(links):
        if not isinstance(link, dict):
            raise PackSchemaError(f"Pack schema links[{index}] must be a mapping: {path}")

        target = link.get("target")
        if target is None:
            raise PackSchemaError(f"Pack schema links[{index}] missing target: {path}")

        for target_type in _normalize_entity_type_list(target, f"links[{index}].target"):
            if target_type not in entity_types:
                raise PackSchemaError(
                    f"Pack schema links[{index}] references unknown target "
                    f"{target_type!r}: {path}"
                )


def _normalize_entity_type_list(value: Any, label: str) -> list[str]:
    """Coerce a schema value to a list of entity type strings; raise on bad shape."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        if not value or not all(isinstance(item, str) for item in value):
            raise PackSchemaError(f"Pack schema {label} must be a string or string list")
        return value
    raise PackSchemaError(f"Pack schema {label} must be a string or string list")


def _build_allowed_tree_parents(
    entities: dict[str, dict[str, Any]],
    entity_types: frozenset[str],
) -> dict[str, tuple[str, ...]]:
    """Map each entity type to the parent types allowed in parent_relationships."""
    allowed: dict[str, tuple[str, ...]] = {}
    for entity_type in entity_types:
        relationships = entities[entity_type].get("parent_relationships", []) or []
        parents = tuple(
            relationship["parent_type"]
            for relationship in relationships
            if isinstance(relationship, dict) and isinstance(relationship.get("parent_type"), str)
        )
        allowed[entity_type] = parents
    return allowed


def _build_parent_relationships(
    entities: dict[str, dict[str, Any]],
    entity_types: frozenset[str],
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Map each entity type to its parent_relationships entries from the schema."""
    return {
        entity_type: tuple(entities[entity_type].get("parent_relationships", []) or [])
        for entity_type in entity_types
    }


def _build_nested_children(
    entities: dict[str, dict[str, Any]],
    entity_types: frozenset[str],
) -> dict[str, tuple[str, ...]]:
    """Invert parent_relationships: parent type → child types that may nest under it."""
    children: dict[str, set[str]] = {entity_type: set() for entity_type in entity_types}

    for entity_type in entity_types:
        for relationship in entities[entity_type].get("parent_relationships", []) or []:
            if not isinstance(relationship, dict):
                continue
            parent_type = relationship.get("parent_type")
            if isinstance(parent_type, str) and parent_type in children:
                children[parent_type].add(entity_type)

    return {entity_type: tuple(sorted(children[entity_type])) for entity_type in entity_types}


def _build_root_entities_by_file(
    entities: dict[str, dict[str, Any]],
    allowed_tree_parents: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    """Map each pack file key to entity types that start the YAML tree in that file."""
    by_file: dict[str, list[str]] = {}

    for entity_type, entity_cfg in entities.items():
        if allowed_tree_parents[entity_type]:
            continue
        file_key = entity_cfg.get("file")
        if not isinstance(file_key, str):
            raise PackSchemaError(
                f"Pack schema entity {entity_type} missing file key"
            )
        by_file.setdefault(file_key, []).append(entity_type)

    return {file_key: tuple(entity_list) for file_key, entity_list in by_file.items()}


def _build_ingest_type_sets(
    entities: dict[str, dict[str, Any]],
    entity_types: frozenset[str],
    path: Path,
) -> tuple[frozenset[str], frozenset[str]]:
    """Split entity types by default_ingest into resolve and create sets."""
    resolve_types: set[str] = set()
    create_types: set[str] = set()

    for entity_type in entity_types:
        default_ingest = entities[entity_type].get("default_ingest", "create")
        if default_ingest == "resolve":
            resolve_types.add(entity_type)
        elif default_ingest == "create":
            create_types.add(entity_type)
        else:
            raise PackSchemaError(
                f"Pack schema default_ingest for {entity_type} must be "
                f"'resolve' or 'create', got {default_ingest!r}: {path}"
            )

    return frozenset(resolve_types), frozenset(create_types)
