from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pkm_workflow import cli
from pkm_workflow.cli import main

pytestmark = pytest.mark.skip(
    reason=(
        "retired v7.3 subcommand/native-blocker CLI contract; "
        "the v7.4 production closure is tests/test_v74_entry.py"
    )
)


def test_importing_production_cli_does_not_import_legacy_write_or_api_modules() -> None:
    for name in ("main", "llm_digest", "pkm_bridge", "fetcher", "writer"):
        sys.modules.pop(name, None)

    importlib.reload(sys.modules["pkm_workflow.cli"])

    assert not {"main", "llm_digest", "pkm_bridge", "fetcher", "writer"}.intersection(sys.modules)


def test_unimplemented_shadow_is_read_only_but_never_reports_success(capsys) -> None:
    exit_code = main(["shadow", "--workflow", "ai", "--date", "2026-08-09", "--json"])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert output["mode"] == "shadow-planning-only"
    assert output["status"] == "BLOCKED"
    assert output["reason"] == "SHADOW_ENGINE_NOT_IMPLEMENTED"
    assert output["workflow"] == "ai"
    assert output["date"] == "2026-08-09"
    assert output["vault_write"] is False
    assert output["production_eligible"] is False
    assert "USER_APPROVAL_REQUIRED" in output["blockers"]


def test_unimplemented_reconcile_never_closes_a_gap_or_reports_success(capsys) -> None:
    exit_code = main(["reconcile", "--workflow", "ai", "--json"])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert output["mode"] == "reconcile-read-only"
    assert output["status"] == "BLOCKED"
    assert output["reason"] == "RECONCILER_NOT_IMPLEMENTED"
    assert output["vault_write"] is False


def test_python_run_command_is_fail_closed_until_native_qualification(capsys) -> None:
    exit_code = main(
        [
            "run",
            "--workflow",
            "ai",
            "--cause",
            "manual",
            "--json",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert output["status"] == "BLOCKED"
    assert output["reason"] == "NATIVE_BROKER_NOT_QUALIFIED"
    assert output["vault_write"] is False


def test_due_only_task_argv_does_not_require_a_workflow_and_remains_blocked(
    capsys,
) -> None:
    exit_code = main(["run", "--due-only", "--cause", "task", "--json"])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert output["status"] == "BLOCKED"
    assert output["workflow"] is None
    assert output["cause"] == "task"
    assert output["due_only"] is True
    assert output["vault_write"] is False


def test_manual_run_accepts_a_validated_content_date_and_remains_blocked(
    capsys,
) -> None:
    exit_code = main(
        [
            "run",
            "--workflow",
            "weekly",
            "--cause",
            "manual",
            "--date",
            "2026-08-09",
            "--json",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert output["status"] == "BLOCKED"
    assert output["workflow"] == "weekly"
    assert output["cause"] == "manual"
    assert output["date"] == "2026-08-09"


@pytest.mark.parametrize("command", ("shadow", "run"))
@pytest.mark.parametrize("invalid_date", ("2026-02-30", "20260809"))
def test_workflow_commands_reject_invalid_calendar_dates(
    command: str,
    invalid_date: str,
) -> None:
    argv = [command, "--workflow", "ai", "--date", invalid_date]
    if command == "run":
        argv.extend(("--cause", "manual"))

    with pytest.raises(SystemExit) as raised:
        main(argv)

    assert raised.value.code == 2


def test_doctor_reports_required_codex_version_without_auto_upgrade(capsys) -> None:
    exit_code = main(["doctor", "--json"])

    output = json.loads(capsys.readouterr().out)
    assert exit_code in {0, 3}
    assert output["required_codex_version"] == "0.147.0"
    assert output["auto_upgrade"] is False
    assert output["production_activation"] is False


def test_doctor_fails_closed_when_codex_is_not_on_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)

    assert main(["doctor", "--json"]) == 3
    output = json.loads(capsys.readouterr().out)
    assert output["installed_codex_version"] is None
    assert "CODEX_VERSION_NOT_QUALIFIED" in output["blockers"]


def test_doctor_parses_only_a_semantic_codex_version_and_never_self_activates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "C:/sealed/codex.exe")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="codex-cli 0.147.0", stderr=""),
    )

    exit_code = main(["doctor", "--json"])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert output["installed_codex_version"] == "0.147.0"
    assert "CODEX_VERSION_NOT_QUALIFIED" not in output["blockers"]
    assert "USER_APPROVAL_REQUIRED" in output["blockers"]


@pytest.mark.parametrize(
    "result",
    (
        SimpleNamespace(stdout="codex-cli development", stderr=""),
        SimpleNamespace(stdout="codex-cli 0.147.x", stderr=""),
    ),
)
def test_doctor_rejects_unparseable_codex_version_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    result: SimpleNamespace,
) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "C:/sealed/codex.exe")
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: result)

    assert main(["doctor", "--json"]) == 3
    output = json.loads(capsys.readouterr().out)
    assert output["installed_codex_version"] is None
    assert "CODEX_VERSION_NOT_QUALIFIED" in output["blockers"]


@pytest.mark.parametrize("error", (OSError("blocked"), subprocess.TimeoutExpired("codex", 5)))
def test_doctor_fails_closed_when_codex_version_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "C:/sealed/codex.exe")

    def fail_probe(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(cli.subprocess, "run", fail_probe)
    assert main(["doctor", "--json"]) == 3
    output = json.loads(capsys.readouterr().out)
    assert output["installed_codex_version"] is None
    assert "CODEX_VERSION_NOT_QUALIFIED" in output["blockers"]


def test_human_readable_output_retains_blocked_state(capsys) -> None:
    assert main(["run", "--workflow", "weekly", "--cause", "retry"]) == 3

    output = capsys.readouterr().out
    assert "status: BLOCKED" in output
    assert "vault_write: False" in output


def test_checkout_compatibility_script_resolves_the_same_engine_without_pythonpath(
    tmp_path: Path,
) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_workflow.py"
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, str(script), "--doctor", "--json"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 3
    payload = json.loads(completed.stdout)
    assert payload["production_activation"] is False
    assert "NATIVE_BROKER_NOT_QUALIFIED" in payload["blockers"]
