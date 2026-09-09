"""The personal AI Daily CLI: prepare, independent review, finalize."""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

from .ai_daily_luna import run_luna_stage


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pkm")
    parser.add_argument("--workflow", choices=("ai",), required=True)
    parser.add_argument("--mode", choices=("shadow", "production"), required=True)
    parser.add_argument("--stage", choices=("prepare", "review", "finalize"))
    parser.add_argument("--run-id")
    parser.add_argument("--edition", choices=("daily", "weekly"))
    parser.add_argument("--confirm-vault-write", action="store_true")
    args = parser.parse_args(argv)
    payload: dict[str, Any]
    if args.confirm_vault_write and args.mode != "production":
        parser.error("--confirm-vault-write requires production")
    if args.stage is None:
        payload = {"status": "LUNA_AUTOMATION_REQUIRED", "exit_code": 4, "vault_write": False}
    else:
        try:
            payload = run_luna_stage(
                args.stage, mode=args.mode, run_id=args.run_id,
                confirm_vault_write=args.confirm_vault_write,
                edition=args.edition,
            )
        except Exception:
            payload = {
                "status": "INTERNAL_ERROR", "error_code": "INTERNAL_ERROR", "exit_code": 10,
                "run_id": args.run_id,
                "vault_write": None if args.stage == "finalize" and args.confirm_vault_write else False,
            }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return int(payload["exit_code"])
