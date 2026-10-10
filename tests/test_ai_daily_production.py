from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import pkm_workflow.ai_daily_production as production_module
from pkm_workflow.ai_daily_production import ProductionResult, run_ai_daily_production
from pkm_workflow.daily_content import CoverageLevel, ShadowResult


def _reviewed_shadow(tmp_path: Path, run_id: str) -> tuple[ShadowResult, Path, Path]:
    run_dir = tmp_path / "scratch" / "runs" / run_id
    run_dir.mkdir(parents=True)
    markdown = run_dir / "AI-Daily-2026-08-30-shadow.md"
    markdown.write_text(
        "---\ntype: ai-daily-shadow\nproduction: false\n---\n\n# AI Daily\n",
        encoding="utf-8",
    )
    report = run_dir / "run-report.json"
    report.write_text("{}", encoding="utf-8")
    return (
        ShadowResult(
            0,
            "PUBLISHED",
            date(2026, 8, 30),
            CoverageLevel.A,
            5,
            5,
            2,
            markdown,
            report,
        ),
        markdown,
        report,
    )


def _publish(
    tmp_path: Path, run_id: str = "a" * 32
) -> tuple[ProductionResult, ShadowResult, Path, Path]:
    vault = tmp_path / "30-Daily"
    vault.mkdir(exist_ok=True)
    shadow, markdown, report = _reviewed_shadow(tmp_path, run_id)
    result = run_ai_daily_production(
        date(2026, 8, 30),
        vault_daily_dir=vault,
        coordination_path=tmp_path / "production.lock",
        shadow_runner=lambda _date: shadow,
    )
    return result, shadow, markdown, report


def test_production_promotes_reviewed_shadow_with_create_new(tmp_path: Path) -> None:
    result, _, _, _ = _publish(tmp_path)

    assert result.status == "PUBLISHED", (result.error_code, result.report_path)
    assert result.vault_path is not None
    assert result.report_path is not None
    content = result.vault_path.read_text(encoding="utf-8")
    assert "type: ai-daily\n" in content
    assert "production: true" in content
    receipt = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert receipt["schema"] == "pkm.ai-daily-production.receipt.v1"
    assert receipt["status"] == "PUBLISHED"
    assert receipt["vault_path"] == str(result.vault_path)
    backing = (
        tmp_path
        / "durable"
        / "backing"
        / "ai-daily"
        / "2026-08-30"
        / f"{'a' * 32}.md"
    )
    assert backing.samefile(result.vault_path)


def test_interrupted_backing_write_reuses_identical_durable_report(tmp_path, monkeypatch):
    original = production_module._write_new
    interrupted = False
    def fail_once(path, content):
        nonlocal interrupted
        if path.suffix == ".md" and "backing" in path.parts and not interrupted:
            interrupted = True
            raise OSError("injected interruption")
        return original(path, content)
    monkeypatch.setattr(production_module, "_write_new", fail_once)
    first, shadow, _, _ = _publish(tmp_path, "c" * 32)
    assert first.status == "PRODUCTION_FAILED"
    durable = tmp_path / "durable/reports/ai-daily/2026-08-30" / f"{'c' * 32}.json"
    prior = durable.read_bytes()
    retried = run_ai_daily_production(
        date(2026, 8, 30), vault_daily_dir=tmp_path / "30-Daily",
        coordination_path=tmp_path / "production.lock", shadow_runner=lambda _: shadow,
    )
    assert retried.status == "PUBLISHED"
    assert durable.read_bytes() == prior
    assert retried.vault_write_performed is True


def test_recovery_does_not_adopt_a_mismatched_durable_artifact(tmp_path):
    shadow, _, _ = _reviewed_shadow(tmp_path, "d" * 32)
    vault = tmp_path / "30-Daily"
    vault.mkdir()
    durable = tmp_path / "durable/reports/ai-daily/2026-08-30" / f"{'d' * 32}.json"
    durable.parent.mkdir(parents=True)
    durable.write_bytes(b"different user content")
    result = run_ai_daily_production(
        date(2026, 8, 30), vault_daily_dir=vault,
        coordination_path=tmp_path / "production.lock", shadow_runner=lambda _: shadow,
    )
    assert result.status == "CONFLICT"
    assert result.vault_write_performed is False
    assert durable.read_bytes() == b"different user content"
    assert not list(vault.glob("*.md"))


def test_existing_unreceipted_daily_is_a_conflict_without_token_usage(
    tmp_path: Path,
) -> None:
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

    assert (result.exit_code, result.status, result.error_code) == (
        3,
        "CONFLICT",
        "TARGET_EXISTS_WITHOUT_RECEIPT",
    )
    assert result.request_count == 0
    assert called is False
    assert destination.read_text(encoding="utf-8") == "existing user note"


def test_existing_receipt_survives_scratch_cleanup_and_skips_model(
    tmp_path: Path,
) -> None:
    first, _, markdown, shadow_report = _publish(tmp_path, "b" * 32)
    assert first.status == "PUBLISHED"
    markdown.unlink()
    shadow_report.unlink()
    called = False

    def forbidden(_date: date | None) -> ShadowResult:
        nonlocal called
        called = True
        raise AssertionError("shadow must not run")

    second = run_ai_daily_production(
        date(2026, 8, 30),
        vault_daily_dir=tmp_path / "30-Daily",
        coordination_path=tmp_path / "production.lock",
        shadow_runner=forbidden,
    )

    assert second.status == "ALREADY_EXISTS"
    assert second.request_count == 0
    assert second.report_path == first.report_path
    assert called is False


@pytest.mark.parametrize(
    ("remove_target", "expected_status", "expected_vault_write"),
    [(True, "PUBLISHED", True), (False, "ALREADY_EXISTS", False)],
)
def test_prepared_receipt_recovers_without_another_model_call(
    tmp_path: Path,
    remove_target: bool,
    expected_status: str,
    expected_vault_write: bool,
) -> None:
    first, _, _, _ = _publish(tmp_path, "c" * 32)
    assert first.report_path is not None
    assert first.vault_path is not None
    published_receipt = first.report_path
    published_receipt.unlink()
    if remove_target:
        first.vault_path.unlink()
    called = False

    def forbidden(_date: date | None) -> ShadowResult:
        nonlocal called
        called = True
        raise AssertionError("shadow must not run during reconcile")

    recovered = run_ai_daily_production(
        date(2026, 8, 30),
        vault_daily_dir=tmp_path / "30-Daily",
        coordination_path=tmp_path / "production.lock",
        shadow_runner=forbidden,
    )

    assert recovered.status == expected_status
    assert recovered.request_count == 0
    assert recovered.payload()["vault_write"] is expected_vault_write
    assert recovered.report_path == published_receipt
    assert published_receipt.is_file()
    assert called is False


def test_receipted_daily_with_user_edit_is_a_conflict(tmp_path: Path) -> None:
    first, _, _, _ = _publish(tmp_path, "d" * 32)
    assert first.vault_path is not None
    first.vault_path.write_text("user edit", encoding="utf-8")
    called = False

    def forbidden(_date: date | None) -> ShadowResult:
        nonlocal called
        called = True
        raise AssertionError("shadow must not run for drift")

    result = run_ai_daily_production(
        date(2026, 8, 30),
        vault_daily_dir=tmp_path / "30-Daily",
        coordination_path=tmp_path / "production.lock",
        shadow_runner=forbidden,
    )

    assert result.status == "CONFLICT"
    assert result.error_code == "PUBLISH_RECEIPT_INVALID"
    assert result.request_count == 0
    assert called is False


def test_terminal_receipt_failure_reports_link_and_reconciles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_link = production_module.os.link
    link_calls = 0

    def fail_terminal_receipt(source, destination, *args, **kwargs):
        nonlocal link_calls
        link_calls += 1
        if link_calls == 3:
            raise ValueError("injected terminal receipt verification failure")
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(production_module.os, "link", fail_terminal_receipt)
    first, _, _, _ = _publish(tmp_path, "e" * 32)

    assert first.status == "PUBLISH_RECONCILE_REQUIRED"
    assert first.error_code == "PUBLISH_TERMINAL_PENDING"
    assert first.request_count == 2
    assert first.payload()["vault_write"] is True
    assert (tmp_path / "30-Daily" / "AI-Daily-2026-08-30.md").is_file()
    monkeypatch.setattr(production_module.os, "link", real_link)
    called = False

    def forbidden(_date: date | None) -> ShadowResult:
        nonlocal called
        called = True
        raise AssertionError("shadow must not run during terminal reconcile")

    recovered = run_ai_daily_production(
        date(2026, 8, 30),
        vault_daily_dir=tmp_path / "30-Daily",
        coordination_path=tmp_path / "production.lock",
        shadow_runner=forbidden,
    )

    assert recovered.status == "ALREADY_EXISTS"
    assert recovered.request_count == 0
    assert recovered.report_path is not None
    assert recovered.report_path.name.endswith(".published.json")
    assert called is False


def test_prepared_receipt_failure_preserves_model_usage(tmp_path: Path, monkeypatch) -> None:
    real_link = production_module.os.link

    def fail_prepared_receipt(source, destination, *args, **kwargs):
        raise ValueError("injected prepared receipt failure")

    monkeypatch.setattr(production_module.os, "link", fail_prepared_receipt)
    result, _, _, _ = _publish(tmp_path, "f" * 32)

    assert result.status == "CONFLICT"
    assert result.request_count == 2
    assert result.shadow_report_path is not None
    assert result.shadow_report_path.is_file()
    assert result.payload()["vault_write"] is False
    assert not (tmp_path / "30-Daily" / "AI-Daily-2026-08-30.md").exists()
    monkeypatch.setattr(production_module.os, "link", real_link)


def test_link_created_before_verification_failure_reports_vault_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    real_link = production_module.os.link
    link_calls = 0

    def corrupt_after_target_link(source, destination, *args, **kwargs):
        nonlocal link_calls
        link_calls += 1
        result = real_link(source, destination, *args, **kwargs)
        if link_calls == 2:
            Path(destination).write_text("corrupt after link", encoding="utf-8")
        return result

    monkeypatch.setattr(production_module.os, "link", corrupt_after_target_link)
    result, _, _, _ = _publish(tmp_path, "1" * 32)

    assert result.status == "CONFLICT"
    assert result.request_count == 2
    assert result.payload()["vault_write"] is True
    assert result.payload()["production_activation"] is False
    assert (tmp_path / "30-Daily" / "AI-Daily-2026-08-30.md").is_file()


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
