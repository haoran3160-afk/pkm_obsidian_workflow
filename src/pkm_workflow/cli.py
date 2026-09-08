"""Sealed v7.4 control CLI with one shared execution seam."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .ai_daily_luna import run_luna_stage
from .ai_daily_shadow import LunaAutomationRequired
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
    parser.add_argument("--confirm-vault-write", action="store_true")
    parser.add_argument("--stage", choices=("prepare", "review", "finalize"))
    parser.add_argument("--run-id")
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
    if args.confirm_vault_write and (
        request.workflow is not Workflow.AI or request.mode is not Mode.PRODUCTION
    ):
        parser.error("--confirm-vault-write requires --workflow ai --mode production")
    if args.stage:
        if request.workflow is not Workflow.AI or request.mode not in {Mode.PRODUCTION, Mode.SHADOW}:
            parser.error("--stage requires AI shadow or production")
        try:
            payload = run_luna_stage(
                args.stage, mode=args.mode, run_id=args.run_id,
                confirm_vault_write=args.confirm_vault_write,
            )
        except Exception:
            payload = {
                "status": "INTERNAL_ERROR", "error_code": "INTERNAL_ERROR", "exit_code": 10,
                "run_id": args.run_id,
                "vault_write": None if args.stage == "finalize" and args.confirm_vault_write else False,
            }
        _emit(payload)
        return int(payload["exit_code"])
    if args.run_id:
        parser.error("--run-id requires --stage")
    if args.confirm_vault_write:
        _emit({
            "status": "LUNA_AUTOMATION_REQUIRED", "error_code": "LUNA_AUTOMATION_REQUIRED",
            "exit_code": 4, "vault_write": False, "api_request_count": 0,
            "next_step": "Use --stage prepare, then Luna generation and independent review.",
        })
        return 4
    try:
        result = execute_workflow(request, RuntimeEnvironment.production())
    except LunaAutomationRequired:
        _emit({
            "status": "LUNA_AUTOMATION_REQUIRED", "exit_code": 4,
            "vault_write": False, "api_request_count": 0,
            "next_step": "Use --stage prepare, then Luna generation and independent review.",
        })
        return 4
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
    if request.workflow is Workflow.AI and request.mode is Mode.PRODUCTION:
        result.payload["next_step"] = "Use --stage prepare for the current Luna workflow."
    _emit(result.payload)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
