"""Sealed v7.4 control CLI with one shared execution seam."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .runtime_policy import (
    ExecutionResult,
    Mode,
    RunRequest,
    RuntimeEnvironment,
    Workflow,
    execute_workflow,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pkm")
    parser.add_argument("--workflow", choices=tuple(item.value for item in Workflow), required=True)
    parser.add_argument("--mode", choices=tuple(item.value for item in Mode), required=True)
    parser.add_argument("--due-only", action="store_true")
    return parser


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    request = RunRequest(
        workflow=Workflow(args.workflow),
        mode=Mode(args.mode),
        due_only=args.due_only,
    )
    try:
        result = execute_workflow(request, RuntimeEnvironment.production())
    except Exception:
        result = ExecutionResult(
            10,
            {
                "schema_version": "7.4",
                "engine": "v7.4-sealed-entry",
                "workflow": request.workflow.value,
                "mode": request.mode.value,
                "due_only": request.due_only,
                "status": "BLOCKED",
                "reason": "INTERNAL_ERROR",
                "vault_write": False,
                "production_activation": False,
                "activation_structure_validated": False,
                "activation_validated": False,
            },
        )
    _emit(result.payload)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
