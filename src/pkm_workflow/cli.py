"""Side-effect-free v7.3 control CLI.

The Python CLI can inspect and execute shadow planning only.  Production Vault
publication is deliberately unavailable until the native broker qualification
artifact and explicit user activation ledger exist.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from collections.abc import Sequence
from datetime import date

REQUIRED_CODEX_VERSION = "0.147.0"
EXIT_OK = 0
EXIT_SAFETY_BLOCKED = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pkm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--json", action="store_true")

    shadow = subparsers.add_parser("shadow")
    _workflow_arguments(shadow)

    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--workflow", choices=("ai", "ielts", "weekly"))
    reconcile.add_argument("--json", action="store_true")

    run = subparsers.add_parser("run")
    _workflow_arguments(run, workflow_required=False)
    run.add_argument(
        "--cause",
        choices=("task", "schedule", "logon", "unlock", "retry", "manual"),
        required=True,
    )
    return parser


def _workflow_arguments(
    parser: argparse.ArgumentParser,
    *,
    workflow_required: bool = True,
) -> None:
    parser.add_argument(
        "--workflow",
        choices=("ai", "ielts", "weekly"),
        required=workflow_required,
    )
    parser.add_argument("--due-only", action="store_true")
    parser.add_argument("--date", type=_content_date)
    parser.add_argument("--json", action="store_true")


def _content_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from error
    if value != parsed.isoformat():
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD")
    return parsed


def _codex_version() -> str | None:
    executable = shutil.which("codex")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    combined = f"{result.stdout}\n{result.stderr}"
    for token in combined.split():
        if token.count(".") == 2 and all(part.isdigit() for part in token.split(".")):
            return token
    return None


def _doctor_report() -> dict[str, object]:
    installed = _codex_version()
    blockers = []
    if installed != REQUIRED_CODEX_VERSION:
        blockers.append("CODEX_VERSION_NOT_QUALIFIED")
    blockers.extend(
        (
            "NATIVE_BROKER_NOT_QUALIFIED",
            "APPCONTAINER_WFP_NOT_QUALIFIED",
            "TASK_BUNDLE_NOT_INSTALLED",
            "SHADOW_EVALUATION_INCOMPLETE",
            "USER_APPROVAL_REQUIRED",
        )
    )
    return {
        "schema_version": "7.3",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "required_codex_version": REQUIRED_CODEX_VERSION,
        "installed_codex_version": installed,
        "auto_upgrade": False,
        "production_activation": False,
        "blockers": blockers,
    }


def _emit(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "run" and not args.due_only and args.workflow is None:
        parser.error("run requires --workflow unless --due-only is set")
    if args.command == "doctor":
        report = _doctor_report()
        _emit(report, as_json=args.json)
        return EXIT_OK if not report["blockers"] else EXIT_SAFETY_BLOCKED

    if args.command == "shadow":
        report = _doctor_report()
        payload = {
            "schema_version": "7.3",
            "mode": "shadow-planning-only",
            "status": "BLOCKED",
            "reason": "SHADOW_ENGINE_NOT_IMPLEMENTED",
            "workflow": args.workflow,
            "date": args.date.isoformat() if args.date else None,
            "due_only": args.due_only,
            "vault_write": False,
            "production_eligible": False,
            "blockers": report["blockers"],
        }
        _emit(payload, as_json=args.json)
        return EXIT_SAFETY_BLOCKED

    if args.command == "reconcile":
        payload = {
            "schema_version": "7.3",
            "mode": "reconcile-read-only",
            "status": "BLOCKED",
            "reason": "RECONCILER_NOT_IMPLEMENTED",
            "workflow": args.workflow,
            "vault_write": False,
            "production_eligible": False,
            "blockers": _doctor_report()["blockers"],
        }
        _emit(payload, as_json=args.json)
        return EXIT_SAFETY_BLOCKED

    payload = {
        "schema_version": "7.3",
        "status": "BLOCKED",
        "reason": "NATIVE_BROKER_NOT_QUALIFIED",
        "workflow": args.workflow,
        "cause": args.cause,
        "date": args.date.isoformat() if args.date else None,
        "due_only": args.due_only,
        "vault_write": False,
    }
    _emit(payload, as_json=args.json)
    return EXIT_SAFETY_BLOCKED


if __name__ == "__main__":
    sys.exit(main())
