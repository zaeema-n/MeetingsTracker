#!/usr/bin/env python3
"""Ingest a ministry data pack into OpenGIN."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ingestion.mappers.errors import MapError
from ingestion.orchestrator import IngestRunner, IngestStrictError, MetadataIngestRunner, PhaseRunResult
from ingestion.pack.errors import PackLoadError, PackSchemaError, ResolveError
from ingestion.pack.schema_loader import DEFAULT_SCHEMA_PATH
from ingestion.utils.http_client import http_client
from ingestion.utils.logger import logger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest a ministry YAML pack into OpenGIN.",
    )
    parser.add_argument(
        "pack_dir",
        type=Path,
        help="Path to data/<Ministry name>/ containing acts, organisations, meetings, rtis",
    )
    parser.add_argument(
        "--active-at",
        metavar="DATE",
        help="ISO date for resolve lookups and create timestamps (required unless --metadata-only)",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Pack schema YAML (default: {DEFAULT_SCHEMA_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log resolve results and would-create actions without API writes",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail if any create-path entity already exists in OpenGIN",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Skip graph ingest and only process *_metadata.json sidecar files",
    )
    parser.add_argument(
        "--graph-only",
        action="store_true",
        help="Run graph ingest only and skip the metadata sidecar phase",
    )
    return parser


async def run_ingest(args: argparse.Namespace) -> int:
    await http_client.start()
    try:
        phase_results = PhaseRunResult()
        if not args.metadata_only:
            runner = IngestRunner()
            phase_results.graph = await runner.run(
                args.pack_dir,
                active_at=args.active_at,
                schema_path=args.schema,
                dry_run=args.dry_run,
                strict=args.strict,
            )

        if not args.graph_only:
            metadata_runner = MetadataIngestRunner()
            phase_results.metadata = await metadata_runner.run(
                args.pack_dir,
                dry_run=args.dry_run,
            )

        _log_phase_results(phase_results)
        return 0
    finally:
        await http_client.close()


def _log_phase_results(results: PhaseRunResult) -> None:
    if results.graph is not None:
        logger.success(
            "Graph ingest totals (active_at=%s, dry_run=%s, strict=%s)",
            results.graph.active_at,
            results.graph.dry_run,
            results.graph.strict,
        )
        logger.success("  resolved: %s", results.graph.resolved)
        logger.success("  created: %s", results.graph.created)
        logger.success("  skipped_existing: %s", results.graph.skipped_existing)
        if results.graph.dry_run:
            logger.success(
                "  dry_run_would_create: %s",
                results.graph.dry_run_would_create,
            )
            logger.success(
                "  dry_run_would_update_parent: %s",
                results.graph.dry_run_would_update_parent,
            )
        else:
            logger.success("  parent_updates: %s", results.graph.parent_updates)

    if results.metadata is not None:
        logger.success(
            "Metadata ingest totals (dry_run=%s)",
            results.metadata.dry_run,
        )
        if results.metadata.dry_run:
            logger.success(
                "  dry_run_would_update_metadata: %s",
                results.metadata.dry_run_would_update_metadata,
            )
        else:
            logger.success(
                "  metadata_updates: %s",
                results.metadata.metadata_updates,
            )
        logger.success("  skipped_not_found: %s", results.metadata.skipped_not_found)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.metadata_only and args.strict:
        parser.error("--strict cannot be used with --metadata-only")

    if args.metadata_only and args.graph_only:
        parser.error("--metadata-only cannot be combined with --graph-only")

    if not args.metadata_only and not args.active_at:
        parser.error("--active-at is required unless --metadata-only is set")

    try:
        return asyncio.run(run_ingest(args))
    except (
        PackLoadError,
        PackSchemaError,
        ResolveError,
        MapError,
        IngestStrictError,
    ) as exc:
        logger.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.error("Interrupted")
        return 130
    except Exception as exc:
        logger.error("Ingest failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
