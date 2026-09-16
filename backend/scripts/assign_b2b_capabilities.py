from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from collections.abc import Sequence
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.schemas.human_review_operations import ApprovedAccountAssignmentsV1
from app.services.human_review_capabilities import (
    CapabilityAssignmentConflict,
    apply_capability_assignments,
    plan_capability_assignments,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or apply explicitly approved B2B capability assignments.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approved-manifest-sha256")
    parser.add_argument("--operator-identifier")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _load_manifest(path: Path) -> ApprovedAccountAssignmentsV1:
    value = json.loads(path.read_text(encoding="utf-8"))
    return ApprovedAccountAssignmentsV1.model_validate(value)


def _write_output(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.dry_run and (
        args.approved_manifest_sha256 is not None
        or args.operator_identifier is not None
    ):
        parser.error(
            "--approved-manifest-sha256 and --operator-identifier are apply-only"
        )
    if args.apply and not args.approved_manifest_sha256:
        parser.error("--approved-manifest-sha256 is required with --apply")
    if args.apply and not args.operator_identifier:
        parser.error("--operator-identifier is required with --apply")

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        parser.error("DATABASE_URL environment variable is required")
    if make_url(database_url).get_backend_name() != "postgresql":
        parser.error("DATABASE_URL must use PostgreSQL")

    try:
        manifest = _load_manifest(args.manifest)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        parser.error(f"invalid manifest: {exc}")

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        if args.dry_run:
            with Session(engine) as db, db.begin():
                db.execute(text("SET TRANSACTION READ ONLY"))
                output = plan_capability_assignments(db, manifest)
        else:
            with Session(engine) as db, db.begin():
                output = apply_capability_assignments(
                    db,
                    manifest,
                    args.approved_manifest_sha256,
                    args.operator_identifier,
                )
    except CapabilityAssignmentConflict as exc:
        parser.error(str(exc))
    finally:
        engine.dispose()

    _write_output(args.output, output.model_dump(mode="json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
