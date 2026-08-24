"""Behavior tests for the fixed, in-memory T1.3b coverage gate."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.run_t13b_coverage as coverage_runner
from scripts.run_t13b_coverage import (
    COVERAGE_ROOT_ENV,
    OUTPUT_LIMIT_BYTES,
    CoverageRootError,
    _BoundedUtf8Sink,
    _create_new_basetemp,
    passes_module_coverage,
    resolve_coverage_root,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPOSITORY_ROOT / "scripts" / "run_t13b_coverage.py"
RUNTIME_SCRATCH = Path("D:/personal/obsidian_workflow-runtime/scratch")
VERIFICATION_ARTIFACTS = Path("D:/personal/obsidian_workflow/verification-artifacts")
RUNNER_TEST_ROOT_ENV = "PKM_T13B_RUNNER_TEST_ROOT"

EXPECTED_MODULES = [
    "src/pkm_workflow/curation.py",
    "src/pkm_workflow/codex_job.py",
    "src/pkm_workflow/codex_proxy.py",
    "src/pkm_workflow/codex_runner.py",
    "src/pkm_workflow/model_contract.py",
    "src/pkm_workflow/review_contract.py",
]
EXPECTED_TESTS = [
    "tests/test_v8_curation.py",
    "tests/test_v8_codex_job.py",
    "tests/test_v8_codex_proxy.py",
    "tests/test_v8_codex_runner.py",
    "tests/test_v73_codex_runner.py",
    "tests/test_v8_model_contract.py",
    "tests/test_v8_review_contract.py",
]


def _create_new_workdir(label: str) -> Path:
    configured_parent = os.environ.get(RUNNER_TEST_ROOT_ENV, str(RUNTIME_SCRATCH))
    parent = resolve_coverage_root({COVERAGE_ROOT_ENV: configured_parent})
    root = parent / f"t-{label[:12]}-{uuid.uuid4().hex[:8]}"
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    return root


def _run(*arguments: str, cwd: Path, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-S", str(RUNNER), *arguments],
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )


def test_runner_rejects_every_caller_supplied_override() -> None:
    workdir = _create_new_workdir("coverage-args")

    result = _run("--source", "D:/personal", cwd=workdir)

    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
    assert not list(workdir.iterdir())


@pytest.mark.parametrize(
    "untrusted_root",
    (
        str(REPOSITORY_ROOT),
        "D:/personal/ObsidianVault",
        "relative/python-artifacts",
        "//server/share/python-artifacts",
        "//?/D:/personal/obsidian_workflow/verification-artifacts/python-artifacts",
    ),
)
def test_runner_rejects_repo_vault_relative_unc_and_device_coverage_roots(
    untrusted_root: str,
) -> None:
    workdir = _create_new_workdir("coverage-root-reject")
    environment = os.environ.copy()
    environment[COVERAGE_ROOT_ENV] = untrusted_root

    result = _run(cwd=workdir, environment=environment)

    assert result.returncode == 3
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["terminal"] == "COVERAGE_ROOT_REJECTED"
    assert payload["error_type"] == "CoverageRootError"
    assert not list(workdir.iterdir())


def test_coverage_root_defaults_to_runtime_and_rejects_any_reparse_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert resolve_coverage_root({}) == RUNTIME_SCRATCH.resolve(strict=True)
    real_lstat = coverage_runner.os.lstat
    flagged = VERIFICATION_ARTIFACTS.resolve(strict=True)

    def lstat_with_reparse(path: os.PathLike[str] | str):
        result = real_lstat(path)
        if Path(path) == flagged:
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_file_attributes=getattr(result, "st_file_attributes", 0) | 0x400,
            )
        return result

    monkeypatch.setattr(coverage_runner.os, "lstat", lstat_with_reparse)
    with pytest.raises(CoverageRootError, match="reparse"):
        resolve_coverage_root({COVERAGE_ROOT_ENV: str(flagged)})


def test_fixed_inputs_are_reparse_checked_before_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _create_new_workdir("fixed-input-reparse")
    fixed_input = repository / "fixed.py"
    with fixed_input.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("VALUE = 1\n")
    monkeypatch.setattr(coverage_runner, "REPOSITORY_ROOT", repository)

    def reject_fixed_input(path: Path) -> None:
        assert path == fixed_input
        raise CoverageRootError("lexical reparse input rejected")

    monkeypatch.setattr(
        coverage_runner, "_reject_reparse_components", reject_fixed_input
    )

    with pytest.raises(CoverageRootError, match="lexical reparse"):
        coverage_runner._fixed_paths(("fixed.py",))


def test_repository_inputs_are_reparse_checked_before_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _create_new_workdir("closure-reparse")
    source_directory = repository / "src" / "pkm_workflow"
    tests_directory = repository / "tests"
    scripts_directory = repository / "scripts"
    source_directory.mkdir(parents=True, exist_ok=False)
    tests_directory.mkdir(parents=False, exist_ok=False)
    scripts_directory.mkdir(parents=False, exist_ok=False)
    source_input = source_directory / "sample.py"
    test_input = tests_directory / "sample.json"
    pyproject = repository / "pyproject.toml"
    runner = scripts_directory / "run_t13b_coverage.py"
    for path, body in (
        (source_input, "VALUE = 1\n"),
        (test_input, "{}\n"),
        (pyproject, "[project]\nname = 'fixture'\n"),
        (runner, "# fixture\n"),
    ):
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(body)

    monkeypatch.setattr(coverage_runner, "REPOSITORY_ROOT", repository)
    monkeypatch.setattr(coverage_runner, "SOURCE_ROOT", repository / "src")
    monkeypatch.setattr(coverage_runner, "PYPROJECT", pyproject)
    monkeypatch.setattr(coverage_runner, "__file__", str(runner))
    original_resolve = Path.resolve
    source_was_resolved = False
    source_was_checked = False

    def tracking_resolve(path: Path, strict: bool = False) -> Path:
        nonlocal source_was_resolved
        if path == source_input:
            source_was_resolved = True
        return original_resolve(path, strict=strict)

    def reject_after_resolution(path: Path) -> None:
        nonlocal source_was_checked
        if path == source_input:
            assert not source_was_resolved, (
                "lexical repository input was resolved before reparse validation"
            )
            source_was_checked = True

    monkeypatch.setattr(Path, "resolve", tracking_resolve)
    monkeypatch.setattr(
        coverage_runner, "_reject_reparse_components", reject_after_resolution
    )

    paths = coverage_runner._repository_input_paths()

    assert source_was_checked is True
    assert source_input in paths


def test_repository_inputs_reject_non_python_reparse_directories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _create_new_workdir("source-dir-reparse")
    source_directory = repository / "src" / "pkm_workflow"
    tests_directory = repository / "tests"
    scripts_directory = repository / "scripts"
    nested_source_directory = source_directory / "nested"
    nested_source_directory.mkdir(parents=True, exist_ok=False)
    tests_directory.mkdir(parents=False, exist_ok=False)
    scripts_directory.mkdir(parents=False, exist_ok=False)
    source_input = source_directory / "sample.py"
    pyproject = repository / "pyproject.toml"
    runner = scripts_directory / "run_t13b_coverage.py"
    for path, body in (
        (source_input, "VALUE = 1\n"),
        (pyproject, "[project]\nname = 'fixture'\n"),
        (runner, "# fixture\n"),
    ):
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(body)

    monkeypatch.setattr(coverage_runner, "REPOSITORY_ROOT", repository)
    monkeypatch.setattr(coverage_runner, "SOURCE_ROOT", repository / "src")
    monkeypatch.setattr(coverage_runner, "PYPROJECT", pyproject)
    monkeypatch.setattr(coverage_runner, "__file__", str(runner))

    def reject_nested_source(path: Path) -> None:
        if path == nested_source_directory:
            raise CoverageRootError("source directory reparse rejected")

    monkeypatch.setattr(
        coverage_runner, "_reject_reparse_components", reject_nested_source
    )

    with pytest.raises(CoverageRootError, match="source directory reparse"):
        coverage_runner._repository_input_paths()


def test_each_module_must_independently_meet_both_fixed_thresholds() -> None:
    assert passes_module_coverage(
        statements=10,
        missing_statements=1,
        branches=10,
        missing_branches=1,
    )
    assert not passes_module_coverage(
        statements=100,
        missing_statements=11,
        branches=100,
        missing_branches=0,
    )


def test_basetemp_is_reserved_create_new_under_a_verified_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed_root = _create_new_workdir("runner-reservation")
    monkeypatch.setenv(COVERAGE_ROOT_ENV, str(allowed_root))

    reservation, basetemp = _create_new_basetemp()

    assert reservation.is_dir()
    assert reservation.parent == allowed_root.resolve(strict=True)
    assert basetemp == reservation / "basetemp"
    assert not basetemp.exists()


def test_output_sink_hashes_without_retaining_and_marks_overflow() -> None:
    sink = _BoundedUtf8Sink(limit_bytes=4)

    assert sink.write("ab") == 2
    assert sink.write("cde") == 3

    assert sink.byte_count == 5
    assert sink.overflow is True
    assert sink.sha256 == hashlib.sha256(b"abcde").hexdigest()
    assert not passes_module_coverage(
        statements=100,
        missing_statements=0,
        branches=100,
        missing_branches=11,
    )


def test_runner_executes_fixed_real_tests_with_memory_only_branch_coverage() -> None:
    workdir = _create_new_workdir("coverage-live")
    ambient_data = workdir / "ambient.coverage"
    with ambient_data.open("xb") as stream:
        stream.write(b"must-not-change")
    hostile_startup = workdir / "hostile-coveragerc"
    with hostile_startup.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("[run]\nparallel = true\n")
    environment = os.environ.copy()
    environment["COVERAGE_FILE"] = str(ambient_data)
    environment["COVERAGE_PROCESS_START"] = str(hostile_startup)
    environment["PYTEST_ADDOPTS"] = "--cov=D:/personal --cov-report=term"
    environment["PYTEST_PLUGINS"] = "pytest_cov.plugin"
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    allowed_root = resolve_coverage_root(
        {COVERAGE_ROOT_ENV: os.environ.get(RUNNER_TEST_ROOT_ENV, str(RUNTIME_SCRATCH))}
    )
    environment[COVERAGE_ROOT_ENV] = str(allowed_root)

    result = _run(cwd=REPOSITORY_ROOT, environment=environment)

    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "pkm.t13b.coverage.v1"
    assert payload["threshold_percent"] == 90
    assert "branch" in payload, payload
    assert payload["branch"] is True
    assert payload["data_file"] is None
    assert payload["pytest_exit_code"] == 0, payload["pytest_failures"]
    assert payload["pytest_failures"] == []
    assert payload["modules"] == EXPECTED_MODULES
    assert payload["tests"] == EXPECTED_TESTS
    assert payload["pytest_plugin_autoload"] is False
    assert payload["pytest_cacheprovider"] is False
    assert payload["pytest_cov_plugin"] is False
    assert payload["pytest_tmp_path_mode"] == "CREATE_NEW_NO_CLEANUP"
    assert payload["bytecode_writes"] is False
    assert payload["isolated_mode"] is True
    assert Path(payload["coverage_root"]) == allowed_root.resolve(strict=True)
    assert Path(payload["pycache_root"]).parent == allowed_root.resolve(strict=True)
    assert not list(Path(payload["pycache_root"]).iterdir())
    assert Path(payload["pytest_reservation"]).parent == allowed_root.resolve(strict=True)
    assert Path(payload["pytest_basetemp"]).parent == Path(payload["pytest_reservation"])
    assert payload["pytest_basetemp_created"] is False
    assert payload["pytest_output_limit_bytes"] == OUTPUT_LIMIT_BYTES
    assert payload["pytest_output_overflow"] is False
    assert payload["input_closure_sha256"].startswith("sha256:")
    assert len(payload["input_closure_sha256"]) == 71
    assert len(payload["input_records"]) > len(EXPECTED_MODULES) + len(EXPECTED_TESTS)
    input_paths = {record["path"] for record in payload["input_records"]}
    assert str(RUNNER.resolve(strict=True)) in input_paths
    assert str((REPOSITORY_ROOT / "pyproject.toml").resolve(strict=True)) in input_paths
    assert set(payload["runtime_identities"]) == {
        "coverage",
        "pytest",
        "distribution_closure_sha256",
        "distribution_file_count",
        "sealed_runtime_root",
    }
    assert payload["runtime_identities"]["distribution_closure_sha256"] == (
        "sha256:f02dd6b952b95691b969598341ac8ea75514ff9cced8d4e1f8fe2dfa5e688d53"
    )
    assert payload["runtime_identities"]["distribution_file_count"] == 566
    sealed_runtime_root = Path(payload["runtime_identities"]["sealed_runtime_root"])
    assert sealed_runtime_root.parent == allowed_root.resolve(strict=True)
    assert sealed_runtime_root.name.startswith("rt-")
    assert len(sealed_runtime_root.name) == len("rt-") + 16
    assert Path(payload["runtime_identities"]["coverage"]) == (
        sealed_runtime_root / "Lib" / "site-packages" / "coverage" / "__init__.py"
    )
    assert Path(payload["runtime_identities"]["pytest"]) == (
        sealed_runtime_root / "Lib" / "site-packages" / "pytest" / "__init__.py"
    )
    assert [result["module"] for result in payload["module_results"]] == EXPECTED_MODULES
    every_module_passed = True
    for module_result in payload["module_results"]:
        assert set(module_result) == {
            "module",
            "statements",
            "missing_statements",
            "branches",
            "missing_branches",
            "line_pct",
            "branch_pct",
            "passed",
        }
        assert module_result["statements"] > 0
        assert module_result["missing_statements"] >= 0
        assert module_result["branches"] > 0
        assert module_result["missing_branches"] >= 0
        expected_line_pct = round(
            (module_result["statements"] - module_result["missing_statements"])
            * 100
            / module_result["statements"],
            2,
        )
        expected_branch_pct = round(
            (module_result["branches"] - module_result["missing_branches"])
            * 100
            / module_result["branches"],
            2,
        )
        expected_pass = (
            (module_result["statements"] - module_result["missing_statements"]) * 100
            >= 90 * module_result["statements"]
            and (module_result["branches"] - module_result["missing_branches"]) * 100
            >= 90 * module_result["branches"]
        )
        assert module_result["line_pct"] == expected_line_pct
        assert module_result["branch_pct"] == expected_branch_pct
        assert module_result["passed"] is expected_pass
        every_module_passed = every_module_passed and expected_pass
    assert result.returncode == (0 if every_module_passed else 1)
    assert payload["terminal"] == (
        "PASSED" if every_module_passed else "COVERAGE_BELOW_THRESHOLD"
    )
    assert payload["totals"]["statements"] > 0
    assert payload["totals"]["branches"] > 0
    assert payload["totals"]["missing_statements"] >= 0
    assert payload["totals"]["missing_branches"] >= 0
    assert payload["pytest_output_bytes"] > 0
    assert payload["pytest_output_sha256"].startswith("sha256:")
    assert len(payload["pytest_output_sha256"]) == 71
    assert payload["pytest_stdout_sha256"].startswith("sha256:")
    assert payload["pytest_stderr_sha256"].startswith("sha256:")
    assert ambient_data.read_bytes() == b"must-not-change"
    assert sorted(path.name for path in workdir.iterdir()) == sorted(
        (ambient_data.name, hostile_startup.name)
    )
    pytest_basetemp = Path(payload["pytest_basetemp"])
    assert not pytest_basetemp.exists()
    assert pytest_basetemp.parent == Path(payload["pytest_reservation"])
    assert not list(pytest_basetemp.rglob(".coverage*"))
