from __future__ import annotations

from datetime import date
from pathlib import Path

from pkm_workflow.ai_daily_production import run_ai_daily_production
from pkm_workflow.ai_daily_shadow import CoverageLevel, ShadowResult


def test_production_promotes_reviewed_shadow_with_create_new(tmp_path: Path) -> None:
    vault = tmp_path / "30-Daily"
    vault.mkdir()
    shadow_path = tmp_path / "AI-Daily-2026-08-30-shadow.md"
    shadow_path.write_text(
        "---\ntype: ai-daily-shadow\nproduction: false\n---\n\n# AI Daily\n",
        encoding="utf-8",
    )
    report_path = tmp_path / "run-report.json"
    report_path.write_text("{}", encoding="utf-8")

    result = run_ai_daily_production(
        date(2026, 8, 30),
        vault_daily_dir=vault,
        coordination_path=tmp_path / "production.lock",
        shadow_runner=lambda _date: ShadowResult(
            0,
            "PUBLISHED",
            date(2026, 8, 30),
            CoverageLevel.A,
            5,
            5,
            2,
            shadow_path,
            report_path,
        ),
    )

    assert result.status == "PUBLISHED"
    assert result.vault_path is not None
    content = result.vault_path.read_text(encoding="utf-8")
    assert "type: ai-daily\n" in content
    assert "production: true" in content


def test_existing_daily_note_skips_shadow_and_token_usage(tmp_path: Path) -> None:
    vault = tmp_path / "30-Daily"
    vault.mkdir()
    destination = vault / "AI-Daily-2026-08-30.md"
    destination.write_text("existing user note", encoding="utf-8")
    called = False

    def forbidden(_date: date | None) -> ShadowResult:
        nonlocal called
        called = True
        raise AssertionError("shadow must not run")

    result = run_ai_daily_production(
        date(2026, 8, 30),
        vault_daily_dir=vault,
        coordination_path=tmp_path / "production.lock",
        shadow_runner=forbidden,
    )

    assert result.status == "ALREADY_EXISTS"
    assert result.request_count == 0
    assert called is False
    assert destination.read_text(encoding="utf-8") == "existing user note"


def test_missing_vault_returns_structured_failure_without_shadow(tmp_path: Path) -> None:
    called = False

    def forbidden(_date: date | None) -> ShadowResult:
        nonlocal called
        called = True
        raise AssertionError("shadow must not run")

    result = run_ai_daily_production(
        date(2026, 8, 30),
        vault_daily_dir=tmp_path / "missing",
        coordination_path=tmp_path / "production.lock",
        shadow_runner=forbidden,
    )

    assert result.exit_code == 10
    assert result.status == "PRODUCTION_FAILED"
    assert result.error_code == "VAULT_PATH_INVALID"
    assert result.request_count == 0
    assert called is False
