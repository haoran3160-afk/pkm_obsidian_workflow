#!/usr/bin/env python3
"""Compatibility adapter for v7.3 doctor and dry-run commands only."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

REPOSITORY_SRC = Path(__file__).resolve().parents[1] / "src"
if not (REPOSITORY_SRC / "pkm_workflow" / "cli.py").is_file():
    raise SystemExit("v7.3 source engine is missing from this checkout")
sys.path.insert(0, str(REPOSITORY_SRC))

# The checkout-local engine must be selected before importing the compatibility
# seam; this intentionally follows the deterministic sys.path bootstrap above.
from pkm_workflow.cli import main as workflow_main  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/run_workflow.py")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--doctor", action="store_true")
    operation.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workflow", choices=("ai", "ielts", "weekly"), default="ai")
    parser.add_argument("--due-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    forwarded = ["doctor"] if args.doctor else ["shadow", "--workflow", args.workflow]
    if args.due_only and not args.doctor:
        forwarded.append("--due-only")
    if args.json:
        forwarded.append("--json")
    return workflow_main(forwarded)


if __name__ == "__main__":
    sys.exit(main())
