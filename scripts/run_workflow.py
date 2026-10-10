#!/usr/bin/env python3
"""Checkout-local entry for the personal Luna AI Daily workflow."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_SRC = REPOSITORY_ROOT / "src"
if not (REPOSITORY_SRC / "pkm_workflow" / "cli.py").is_file():
    raise SystemExit("AI Daily engine is missing from this checkout")
sys.path.insert(0, str(REPOSITORY_SRC))
sys.path.insert(1, str(REPOSITORY_ROOT))

# The checkout-local engine must be selected before importing the compatibility
# seam; this intentionally follows the deterministic sys.path bootstrap above.
from pkm_workflow.cli import main as workflow_main  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    return workflow_main(list(argv) if argv is not None else None)


if __name__ == "__main__":
    sys.exit(main())
