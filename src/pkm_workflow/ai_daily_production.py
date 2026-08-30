"""Minimal, idempotent promotion of one reviewed AI Daily into the real Vault."""

from __future__ import annotations

import msvcrt
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .ai_daily_shadow import ShadowResult, run_ai_daily_shadow

VAULT_DAILY_DIR = Path(r"D:\personal\ObsidianVault\30-Daily")
PRODUCTION_LOCK_PATH = Path(
    r"D:\personal\obsidian_workflow-runtime\ai-daily-production.lock"
)


@dataclass(frozen=True)
class ProductionResult:
    exit_code: int
    status: str
    content_date: date
    vault_path: Path | None
    report_path: Path | None
    request_count: int
    error_code: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "content_date": self.content_date.isoformat(),
            "vault_path": str(self.vault_path) if self.vault_path else None,
            "report_path": str(self.report_path) if self.report_path else None,
            "request_count": self.request_count,
            "error_code": self.error_code,
            "vault_write": self.status == "PUBLISHED",
            "production_activation": self.status in {"PUBLISHED", "ALREADY_EXISTS"},
        }


def run_ai_daily_production(
    content_date: date | None = None,
    *,
    vault_daily_dir: Path = VAULT_DAILY_DIR,
    coordination_path: Path = PRODUCTION_LOCK_PATH,
    shadow_runner: Callable[[date | None], ShadowResult] = run_ai_daily_shadow,
) -> ProductionResult:
    selected_date = content_date or date.today()
    try:
        daily_dir = vault_daily_dir.resolve(strict=True)
        lock_path = coordination_path.resolve(strict=False)
        lock_path.parent.resolve(strict=True)
    except OSError:
        return ProductionResult(
            10, "PRODUCTION_FAILED", selected_date, None, None, 0, "VAULT_PATH_INVALID"
        )
    destination = daily_dir / f"AI-Daily-{selected_date.isoformat()}.md"
    lock_handle = None
    try:
        lock_handle = lock_path.open("a+b")
        if lock_path.stat().st_size == 0:
            lock_handle.write(b"0")
            lock_handle.flush()
        lock_handle.seek(0)
        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        if lock_handle is not None:
            lock_handle.close()
        return ProductionResult(
            4, "PRODUCTION_BUSY", selected_date, None, None, 0, "PRODUCTION_BUSY"
        )

    assert lock_handle is not None
    shadow: ShadowResult | None = None
    try:
        if destination.exists():
            return ProductionResult(0, "ALREADY_EXISTS", selected_date, destination, None, 0)

        shadow = shadow_runner(selected_date)
        if shadow.exit_code != 0 or shadow.markdown_path is None:
            return ProductionResult(
                shadow.exit_code,
                shadow.status,
                selected_date,
                None,
                shadow.report_path,
                shadow.request_count,
                shadow.error_code,
            )
        markdown = shadow.markdown_path.read_text(encoding="utf-8")
        production_markdown = markdown.replace(
            "type: ai-daily-shadow", "type: ai-daily", 1
        ).replace("production: false", "production: true", 1)
        backing = shadow.markdown_path.with_name(
            f"AI-Daily-{selected_date.isoformat()}-production.md"
        )
        with backing.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(production_markdown)
            handle.flush()
        try:
            destination.hardlink_to(backing)
        except FileExistsError:
            return ProductionResult(0, "ALREADY_EXISTS", selected_date, destination, None, 0)
        return ProductionResult(
            0,
            "PUBLISHED",
            selected_date,
            destination,
            shadow.report_path,
            shadow.request_count,
        )
    except OSError:
        return ProductionResult(
            10,
            "PRODUCTION_FAILED",
            selected_date,
            None,
            shadow.report_path if shadow else None,
            shadow.request_count if shadow else 0,
            "PRODUCTION_WRITE_FAILED",
        )
    finally:
        lock_handle.seek(0)
        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        lock_handle.close()
