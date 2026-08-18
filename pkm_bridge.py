#!/usr/bin/env python3
"""Thin bridge from a canonical RunRef to the no-delete Commit Service."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from commit_service import ArtifactInput, CommitService
from run_service import RunService
from workflow_contracts import CommitStatus, NoteDraft, RunRef


class BridgeError(RuntimeError):
    exit_code = 3


class BridgeUsageError(BridgeError):
    exit_code = 2


class BridgeAuthorizationError(BridgeError):
    exit_code = 3


class BridgeCommitError(BridgeError):
    exit_code = 3


class BridgePartialError(BridgeCommitError):
    exit_code = 4


class PKMBridge:
    """Consume authority from Run Service; never construct or upgrade it."""

    def __init__(self, *, runs: RunService, commits: CommitService) -> None:
        self.runs = runs
        self.commits = commits

    def commit(
        self,
        *,
        run_ref: RunRef,
        generation_id: str,
        artifacts: Sequence[ArtifactInput],
    ) -> tuple[str, ...]:
        if not isinstance(run_ref, RunRef):
            raise BridgeAuthorizationError("a canonical RunRef from Run Service is required")
        canonical = self.runs.get_run(run_ref.run_id).ref
        if canonical != run_ref:
            raise BridgeAuthorizationError(
                "provided RunRef differs from canonical Run Service lineage"
            )
        try:
            result = self.commits.commit(
                run_id=canonical.run_id,
                generation_id=generation_id,
                artifacts=artifacts,
            )
        except Exception as exc:
            raise BridgeCommitError(str(exc)) from exc
        if result.status is CommitStatus.PARTIAL:
            raise BridgePartialError(f"generation {generation_id} is PARTIAL; no paths returned")
        if result.status is not CommitStatus.COMMITTED:
            raise BridgeCommitError(f"generation {generation_id} ended as {result.status.value}")
        return tuple(file.path for file in result.files)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Submit pre-rendered notes through canonical PKM commit services"
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--target-path")
    parser.add_argument("--content")
    parser.add_argument("--json", dest="json_path")
    parser.add_argument("--logical-collection", default="manual-note")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--review-state", default="approved")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--update-index", action="store_true")
    parser.add_argument("--update-log", nargs="?", const="", default=None)
    return parser


def _draft(
    *,
    run_id: str,
    target_path: str,
    body: str,
    logical_collection: str,
    version: int,
    review_state: str,
) -> ArtifactInput:
    body_hash = hashlib.sha256(b"publication_state: \n" + body.encode("utf-8")).hexdigest()
    return ArtifactInput(
        draft=NoteDraft(
            run_id=run_id,
            logical_collection=logical_collection,
            target_path=target_path,
            version=version,
            body_hash=body_hash,
            review_state=review_state,
        ),
        body=body,
    )


def _load_artifacts(args: argparse.Namespace) -> list[ArtifactInput]:
    if args.json_path:
        payload = json.loads(Path(args.json_path).read_text(encoding="utf-8-sig"))
        rows = payload if isinstance(payload, list) else [payload]
        if not rows or not all(isinstance(row, dict) for row in rows):
            raise BridgeUsageError("batch JSON must contain one or more objects")
        return [
            _draft(
                run_id=args.run_id,
                target_path=str(row["target_path"]),
                body=str(row["body"]),
                logical_collection=str(row.get("logical_collection", "manual-note")),
                version=int(row.get("version", 1)),
                review_state=str(row.get("review_state", "approved")),
            )
            for row in rows
        ]
    if not args.target_path or args.content is None:
        raise BridgeUsageError("--target-path and --content are required without --json")
    return [
        _draft(
            run_id=args.run_id,
            target_path=args.target_path,
            body=args.content,
            logical_collection=args.logical_collection,
            version=args.version,
            review_state=args.review_state,
        )
    ]


def run_cli(
    argv: Sequence[str],
    *,
    runs: RunService,
    bridge: PKMBridge,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else BridgeUsageError.exit_code
    if args.overwrite or args.update_index or args.update_log is not None:
        stderr.write("forbidden: overwrite and existing MOC/log updates are disabled\n")
        return BridgeUsageError.exit_code
    try:
        run_ref = runs.get_run(args.run_id).ref
        paths = bridge.commit(
            run_ref=run_ref,
            generation_id=args.generation_id,
            artifacts=_load_artifacts(args),
        )
    except BridgeError as exc:
        stderr.write(f"{exc}\n")
        return exc.exit_code
    except Exception as exc:
        stderr.write(f"{exc}\n")
        return BridgeCommitError.exit_code
    for path in paths:
        stdout.write(f"{path}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    sys.stderr.write("pkm_bridge requires services wired by the canonical pkm CLI\n")
    return BridgeUsageError.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
