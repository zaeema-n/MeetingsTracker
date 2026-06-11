#!/usr/bin/env python3
"""Ingest a ministry data pack into OpenGIN."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ingestion.mappers.errors import MapError
from ingestion.orchestrator import IngestRunner, IngestStrictError
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
        required=True,
        metavar="DATE",
        help="ISO date for resolve lookups and create timestamps (required on every run)",
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
    return parser


async def run_ingest(args: argparse.Namespace) -> int:
    await http_client.start()
    try:
        runner = IngestRunner()
        await runner.run(
            args.pack_dir,
            active_at=args.active_at,
            schema_path=args.schema,
            dry_run=args.dry_run,
            strict=args.strict,
        )
        return 0
    finally:
        await http_client.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
