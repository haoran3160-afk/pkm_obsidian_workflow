"""Behavior tests for the no-delete verification harness.

These tests deliberately keep every artifact they create.  They use a caller
provided, unique ``PKM_TEST_ROOT`` instead of pytest's temporary-directory
fixtures so the project's no-delete invariant also applies to the test suite.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "scripts" / "run_task_verification.ps1"
MANIFEST = REPO_ROOT / "verification_commands.json"


def _new_verification_root(case: str) -> Path:
    parent_value = os.environ.get("PKM_TEST_ROOT")
    if not parent_value:
        pytest.skip("PKM_TEST_ROOT is required by the no-delete test contract")

    parent = Path(parent_value).resolve(strict=True)
    root = parent / f"{case}-{uuid.uuid4().hex}"
    root.mkdir()
    return root


def _run_harness(*arguments: str) -> subprocess.CompletedProcess[str]:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(HARNESS),
        *arguments,
    ]
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_harness_with_argument_array(
    *,
    task_id: str,
    root: Path,
    manifest: Path,
    command_id: str,
    executable: Path,
    arguments: tuple[str, ...],
    test_only: bool = True,
) -> subprocess.CompletedProcess[str]:
    argument_expression = ",".join(_powershell_literal(value) for value in arguments)
    invocation = [
            "&",
            _powershell_literal(str(HARNESS)),
            "-TaskId",
            _powershell_literal(task_id),
            "-VerificationRoot",
            _powershell_literal(str(root)),
            "-ManifestPath",
            _powershell_literal(str(manifest)),
            "-CommandId",
            _powershell_literal(command_id),
            "-FilePath",
            _powershell_literal(str(executable)),
            "-ArgumentList",
            f"@({argument_expression})",
    ]
    if test_only:
        invocation.append("-TestOnly")
    expression = " ".join(invocation)
    encoded = base64.b64encode(expression.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _write_command_manifest(
    *,
    root: Path,
    manifest_path: Path,
    python: Path,
    script: str,
    task_id: str = "E00.5",
    expected_exit_codes: tuple[int, ...] | None = None,
    expected_blocked_terminal: dict[str, object] | None = None,
    expected_success_terminal: dict[str, object] | None = None,
    snapshot_roots: tuple[Path, ...] | None = None,
) -> None:
    command: dict[str, object] = {
        "internal": False,
        "task_ids": [task_id],
        "executable_path": str(python),
        "executable_sha256": hashlib.sha256(python.read_bytes()).hexdigest(),
        "working_directory": str(REPO_ROOT),
        "argv_schema": [
            {"literal": "-c"},
            {"literal": script},
            {"verification_path": True},
        ],
        "environment": {"PYTHONDONTWRITEBYTECODE": "1"},
        "snapshot_roots": [
            str(path) for path in (snapshot_roots or (manifest_path,))
        ],
        "allowed_mutations": ["verification-create-only"],
    }
    if expected_exit_codes is not None:
        command["expected_exit_codes"] = list(expected_exit_codes)
    if expected_blocked_terminal is not None:
        command["expected_blocked_terminal"] = expected_blocked_terminal
    if expected_success_terminal is not None:
        command["expected_success_terminal"] = expected_success_terminal
    manifest = {
        "schema_version": 1,
        "generation": "verification-output-root-test",
        "authorization": "test-only",
        "harness_mutation_policy": [
            "verification-create-only",
            "verification-root-acl-tighten",
        ],
        "spec_sha256": "0" * 64,
        "release_hash": None,
        "allowed_verification_roots": [str(root)],
        "protected_roots": [str(root)],
        "commands": {"emit-create-new-output": command},
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _new_runtime_scratch_root(case: str) -> Path:
    parent = Path(r"D:\personal\obsidian_workflow-runtime\scratch").resolve(
        strict=True
    )
    root = parent / f"harness-{case}-{uuid.uuid4().hex}"
    root.mkdir()
    return root


def _add_scratch_toolchain_contract(
    manifest_path: Path,
    *,
    mutable_roots: tuple[Path | str, ...],
    cargo_target_dir: Path | str,
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    command = manifest["commands"]["emit-create-new-output"]
    command["scratch_toolchain_only"] = True
    command["mutable_scratch_roots"] = [str(path) for path in mutable_roots]
    command["environment"]["CARGO_TARGET_DIR"] = str(cargo_target_dir)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_self_test_creates_immutable_evidence_without_removing_prior_paths() -> None:
    root = _new_verification_root("self-test")
    prior = root / "prior.txt"
    prior.write_text("must remain", encoding="utf-8")

    result = _run_harness(
        "-TaskId",
        "E00.5",
        "-VerificationRoot",
        str(root),
        "-ManifestPath",
        str(MANIFEST),
        "-SelfTest",
    )

    assert result.returncode == 0, result.stderr
    assert prior.read_text(encoding="utf-8") == "must remain"

    evidence_paths = sorted(root.glob("evidence-*.json"))
    assert len(evidence_paths) == 1
    evidence = json.loads(evidence_paths[0].read_text(encoding="utf-8"))
    assert evidence["task_id"] == "E00.5"
    assert evidence["command_id"] == "self-test"
    assert evidence["status"] == "HARNESS_SELF_TEST_ONLY"
    assert evidence["trust_mode"] == "CANONICAL_MANIFEST"
    assert evidence["production_qualification_eligible"] is False
    assert evidence["manifest_sha256"] == hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    assert evidence["delete_count"] == 0
    assert evidence["rename_count"] == 0
    assert evidence["create_count"] == 0
    assert evidence["change_count"] == 0
    assert evidence["created_path_count"] == 0
    assert evidence["modified_path_count"] == 0
    assert evidence["truncate_count"] == 0
    assert evidence["monitor_gap_count"] == 0
    assert evidence["security_change_count"] == 0
    assert evidence["security_boundary_change_count"] == 0
    assert evidence["security_boundary_change_paths"] == []
    assert evidence["modified_security_paths"] == []
    assert evidence["harness_mutation_policy"] == [
        "verification-create-only",
        "verification-root-acl-tighten",
    ]
    assert evidence["verification_root_acl"]["mutation"] == (
        "verification-root-acl-tighten"
    )


def test_unknown_command_is_rejected_before_process_start() -> None:
    root = _new_verification_root("unknown-command")
    canary = root / "canary-must-not-exist.txt"

    result = _run_harness(
        "-TaskId",
        "E00.5",
        "-VerificationRoot",
        str(root),
        "-ManifestPath",
        str(MANIFEST),
        "-CommandId",
        "not-allowlisted",
        "-FilePath",
        "powershell.exe",
        "-ArgumentList",
        str(canary),
    )

    assert result.returncode != 0
    assert not canary.exists()
    assert "not allowlisted" in (result.stderr + result.stdout).lower()


def test_existing_evidence_target_is_never_overwritten() -> None:
    root = _new_verification_root("create-new")
    fixed_id = "00000000-0000-0000-0000-000000000001"
    existing = root / f"evidence-{fixed_id}.json"
    existing.write_text("original", encoding="utf-8")

    first = _run_harness(
        "-TaskId",
        "E00.5",
        "-VerificationRoot",
        str(root),
        "-ManifestPath",
        str(MANIFEST),
        "-EvidenceId",
        fixed_id,
        "-SelfTest",
    )

    assert first.returncode != 0
    assert existing.read_text(encoding="utf-8") == "original"


def test_harness_source_tracks_created_changed_and_content_hash_guards() -> None:
    source = HARNESS.read_text(encoding="utf-8")

    assert "-EventName Created" in source
    assert "-EventName Changed" in source
    assert "modified_paths" in source
    assert "created_paths" in source
    assert "Get-Sha256Hex" in source
    assert "[System.Security.Cryptography.SHA256]::Create()" in source
    assert "allowed_ancestor_change_events" in source


def test_declared_create_new_output_root_is_preserved_and_not_reported_as_violation() -> None:
    root = _new_verification_root("declared-output")
    manifest_path = root / "manifest.json"
    output_root = root / "command-output"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = (
        "from pathlib import Path; import sys; "
        "p=Path(sys.argv[1]); p.mkdir(); "
        "(p/'probe.txt').write_text('preserved',encoding='utf-8')"
    )
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
    )

    result = _run_harness_with_argument_array(
        task_id="E00.5",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert (output_root / "probe.txt").read_text(encoding="utf-8") == "preserved"
    evidence_paths = sorted(root.glob("evidence-*.json"))
    assert len(evidence_paths) == 1
    evidence = json.loads(evidence_paths[0].read_text(encoding="utf-8"))
    assert evidence["status"] == "HARNESS_TEST_ONLY"
    assert evidence["trust_mode"] == "HARNESS_TEST_ONLY"
    assert evidence["production_qualification_eligible"] is False
    assert "HARNESS_TEST_ONLY_MANIFEST" in evidence["qualification_blockers"]
    assert evidence["created_paths"] == []
    assert evidence["create_events"] == []
    assert str(output_root) in evidence["allowed_output_roots"]


def test_output_outside_declared_create_new_root_blocks_evidence() -> None:
    root = _new_verification_root("output-escape")
    manifest_path = root / "manifest.json"
    output_root = root / "declared-output"
    escaped_path = root / "undeclared-output.txt"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = (
        "from pathlib import Path; import sys; "
        "p=Path(sys.argv[1]); p.mkdir(); "
        "(p/'allowed.txt').write_text('ok',encoding='utf-8'); "
        "(p.parent/'undeclared-output.txt').write_text('blocked',encoding='utf-8')"
    )
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
    )

    result = _run_harness_with_argument_array(
        task_id="E00.5",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode != 0
    assert escaped_path.read_text(encoding="utf-8") == "blocked"
    evidence_paths = sorted(root.glob("evidence-*.json"))
    assert len(evidence_paths) == 1
    evidence = json.loads(evidence_paths[0].read_text(encoding="utf-8"))
    assert evidence["status"] == "BLOCKED"
    assert str(escaped_path).lower() in evidence["created_paths"]


def test_existing_declared_output_root_is_rejected_before_process_start() -> None:
    root = _new_verification_root("existing-output")
    manifest_path = root / "manifest.json"
    output_root = root / "already-exists"
    output_root.mkdir()
    marker = output_root / "marker.txt"
    marker.write_text("untouched", encoding="utf-8")
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = "raise SystemExit('must never execute')"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
    )

    result = _run_harness_with_argument_array(
        task_id="E00.5",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode != 0
    assert marker.read_text(encoding="utf-8") == "untouched"
    assert not list(root.glob("evidence-*.json"))


def test_t13b_task_identifier_is_accepted_by_the_harness_contract() -> None:
    root = _new_verification_root("t13b-task-id")
    manifest_path = root / "manifest.json"
    output_root = root / "qualification-output"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = "from pathlib import Path; import sys; Path(sys.argv[1]).mkdir()"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
        task_id="T1.3b",
    )

    result = _run_harness_with_argument_array(
        task_id="T1.3b",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert output_root.is_dir()


def test_custom_manifest_is_rejected_without_explicit_test_only_before_process_start() -> None:
    root = _new_verification_root("custom-manifest-normal-mode")
    manifest_path = root / "manifest.json"
    output_root = root / "must-not-exist"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = "from pathlib import Path; import sys; Path(sys.argv[1]).mkdir()"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
    )

    result = _run_harness_with_argument_array(
        task_id="E00.5",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
        test_only=False,
    )

    assert result.returncode != 0
    assert not output_root.exists()
    assert "canonical manifest" in (result.stderr + result.stdout).lower()
    assert not list(root.glob("reservation-*.json"))
    assert not list(root.glob("evidence-*.json"))


def test_declared_fail_closed_exit_requires_strict_terminal_contract() -> None:
    root = _new_verification_root("expected-blocked-exit")
    manifest_path = root / "manifest.json"
    output_root = root / "doctor-output"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = "import sys; raise SystemExit(3)"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
        task_id="T1.3b",
        expected_exit_codes=(3,),
    )

    result = _run_harness_with_argument_array(
        task_id="T1.3b",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode != 0
    evidence_path = next(root.glob("evidence-*.json"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["status"] == "BLOCKED"
    assert evidence["exit_code"] == 3
    assert evidence["expected_exit_codes"] == [3]
    assert "EXPECTED_BLOCKED_TERMINAL_CONTRACT_MISSING" in evidence["qualification_blockers"]


def test_strict_fail_closed_terminal_is_test_only_and_never_success() -> None:
    root = _new_verification_root("strict-expected-blocked-exit")
    manifest_path = root / "manifest.json"
    output_root = root / "doctor-output"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    terminal = {
        "terminal": "BLOCKED_PREQUALIFICATION",
        "blockers": ["ELEVATION_REQUIRED"],
        "child_created": False,
        "child_resumed": False,
        "secret_materialized_in_child": False,
        "network_or_auth_used": False,
    }
    stdout = json.dumps(terminal, separators=(",", ":"))
    script = f"import sys; print({stdout!r}); raise SystemExit(3)"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
        task_id="T1.3b",
        expected_exit_codes=(3,),
        expected_blocked_terminal={
            "terminal": "BLOCKED_PREQUALIFICATION",
            "exact_keys": sorted(terminal),
            "nonempty_blockers": True,
            "required_blockers_in_order": ["ELEVATION_REQUIRED"],
            "optional_leading_blockers": [],
            "false_fields": [
                "child_created",
                "child_resumed",
                "secret_materialized_in_child",
                "network_or_auth_used",
            ],
        },
    )

    result = _run_harness_with_argument_array(
        task_id="T1.3b",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    evidence_path = next(root.glob("evidence-*.json"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["status"] == "HARNESS_TEST_ONLY"
    assert evidence["observed_terminal_status"] == "VERIFIED_EXPECTED_BLOCKED"
    assert evidence["terminal_verification"]["valid"] is True
    assert evidence["production_qualification_eligible"] is False


@pytest.mark.parametrize(
    "terminal_mutation",
    (
        {"blockers": []},
        {"blockers": ["ELEVATION_REQUIRED", "ELEVATION_REQUIRED"]},
        {"unexpected": "field"},
        {"child_resumed": True},
    ),
)
def test_malformed_expected_blocked_terminal_is_rejected(
    terminal_mutation: dict[str, object],
) -> None:
    root = _new_verification_root("malformed-expected-blocked")
    manifest_path = root / "manifest.json"
    output_root = root / "doctor-output"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    terminal: dict[str, object] = {
        "terminal": "BLOCKED_PREQUALIFICATION",
        "blockers": ["ELEVATION_REQUIRED"],
        "child_created": False,
        "child_resumed": False,
        "secret_materialized_in_child": False,
        "network_or_auth_used": False,
    }
    terminal.update(terminal_mutation)
    stdout = json.dumps(terminal, separators=(",", ":"))
    script = f"import sys; print({stdout!r}); raise SystemExit(3)"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
        task_id="T1.3b",
        expected_exit_codes=(3,),
        expected_blocked_terminal={
            "terminal": "BLOCKED_PREQUALIFICATION",
            "exact_keys": sorted(
                {
                    "terminal",
                    "blockers",
                    "child_created",
                    "child_resumed",
                    "secret_materialized_in_child",
                    "network_or_auth_used",
                }
            ),
            "nonempty_blockers": True,
            "required_blockers_in_order": ["ELEVATION_REQUIRED"],
            "optional_leading_blockers": [],
            "false_fields": [
                "child_created",
                "child_resumed",
                "secret_materialized_in_child",
                "network_or_auth_used",
            ],
        },
    )

    result = _run_harness_with_argument_array(
        task_id="T1.3b",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode != 0
    evidence = json.loads(next(root.glob("evidence-*.json")).read_text(encoding="utf-8"))
    assert evidence["status"] == "BLOCKED"
    assert evidence["terminal_verification"]["valid"] is False
    assert evidence["production_qualification_eligible"] is False


def test_external_success_is_observer_only_with_explicit_tcb_gaps() -> None:
    root = _new_verification_root("observer-only")
    manifest_path = root / "manifest.json"
    output_root = root / "output"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = "from pathlib import Path; import sys; Path(sys.argv[1]).mkdir()"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
    )

    result = _run_harness_with_argument_array(
        task_id="E00.5",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    evidence = json.loads(next(root.glob("evidence-*.json")).read_text(encoding="utf-8"))
    assert evidence["status"] == "HARNESS_TEST_ONLY"
    assert evidence["observed_terminal_status"] == "VERIFIED_OBSERVER_ONLY"
    assert evidence["verification_scope"] == "POST_HOC_OBSERVER_ONLY"
    assert evidence["qualification_terminal"] == "BLOCKED_TCB_GAP"
    assert evidence["production_qualification_eligible"] is False
    assert evidence["process_authority"] == {
        "restricted_token": False,
        "job_object": False,
        "descendant_drain_confirmed": False,
    }
    assert "HARNESS_JOB_OBJECT_AUTHORITY_ABSENT" in evidence["qualification_blockers"]
    assert "HARNESS_DESCENDANT_DRAIN_UNCONFIRMED" in evidence["qualification_blockers"]


def _synthetic_success_payload(python: Path, root: Path) -> dict[str, object]:
    details = python.stat()
    coverage_root = root / "reported-coverage"
    reservation = coverage_root / ("p-" + "b" * 32)
    basetemp = reservation / "basetemp"
    basetemp.mkdir(parents=True)
    pycache_root = coverage_root / ("c-" + "c" * 32)
    pycache_root.mkdir()
    sealed_runtime_root = coverage_root / ("r-" + "d" * 32)
    coverage_module = sealed_runtime_root / "c.py"
    pytest_module = sealed_runtime_root / "p.py"
    sealed_runtime_root.mkdir()
    coverage_module.touch()
    pytest_module.touch()
    payload: dict[str, object] = {
        "branch": True,
        "data_file": None,
        "coverage_percent": 90.0,
        "coverage_root": str(coverage_root),
        "input_closure_sha256": "",
        "input_records": [
            {
                "file_id": details.st_ino,
                "link_count": details.st_nlink,
                "path": str(python),
                "sha256": "sha256:" + hashlib.sha256(python.read_bytes()).hexdigest(),
                "size": details.st_size,
                "volume": details.st_dev,
            }
        ],
        "isolated_mode": True,
        "module_results": [
            {
                "branch_pct": 90.0,
                "branches": 10,
                "line_pct": 90.0,
                "missing_branches": 1,
                "missing_statements": 1,
                "module": "module.py",
                "passed": True,
                "statements": 10,
            }
        ],
        "modules": ["module.py"],
        "pycache_root": str(pycache_root),
        "pytest_cacheprovider": False,
        "pytest_cov_plugin": False,
        "pytest_exit_code": 0,
        "pytest_basetemp": str(basetemp),
        "pytest_output_overflow": False,
        "pytest_output_bytes": 100,
        "pytest_output_limit_bytes": 1_048_576,
        "pytest_output_sha256": "sha256:" + "2" * 64,
        "pytest_plugin_autoload": False,
        "pytest_reservation": str(reservation),
        "pytest_stderr_sha256": "sha256:" + "3" * 64,
        "pytest_stdout_sha256": "sha256:" + "4" * 64,
        "runtime_identities": {
            "coverage": str(coverage_module),
            "distribution_closure_sha256": "sha256:" + "5" * 64,
            "distribution_file_count": 1,
            "pytest": str(pytest_module),
            "sealed_runtime_root": str(sealed_runtime_root),
        },
        "schema_version": "pkm.t13b.coverage.v1",
        "terminal": "PASSED",
        "tests": ["test.py"],
        "threshold_percent": 90,
        "totals": {
            "branch_pct": 90.0,
            "branches": 10,
            "line_pct": 90.0,
            "missing_branches": 1,
            "missing_statements": 1,
            "statements": 10,
        },
    }
    input_records = payload["input_records"]
    payload["input_closure_sha256"] = "sha256:" + hashlib.sha256(
        json.dumps(
            input_records,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    return payload


def _synthetic_success_contract(
    payload: dict[str, object], python: Path, root: Path
) -> dict[str, object]:
    return {
        "schema_version": "pkm.t13b.coverage.v1",
        "terminal": "PASSED",
        "threshold_percent": 90,
        "output_limit_bytes": 1_048_576,
        "exact_keys": sorted(payload),
        "modules": ["module.py"],
        "tests": ["test.py"],
        "false_fields": [
            "pytest_plugin_autoload",
            "pytest_cacheprovider",
            "pytest_cov_plugin",
        ],
        "required_input_paths": [str(python)],
        "input_closure_sha256": payload["input_closure_sha256"],
        "runtime_identity_keys": [
            "coverage",
            "distribution_closure_sha256",
            "distribution_file_count",
            "pytest",
            "sealed_runtime_root",
        ],
        "runtime_identity_path_keys": ["coverage", "pytest", "sealed_runtime_root"],
        "runtime_identity_root_key": "sealed_runtime_root",
        "runtime_identity_relative_paths": {
            "coverage": "c.py",
            "pytest": "p.py",
        },
        "coverage_root_template": str(root / "reported-coverage"),
        "runtime_root_leaf_regex": "^r-[0-9a-f]{32}$",
        "pytest_reservation_leaf_regex": "^p-[0-9a-f]{32}$",
        "pytest_basetemp_leaf": "basetemp",
        "pycache_root_leaf_regex": "^c-[0-9a-f]{32}$",
        "runtime_closure_digest_key": "distribution_closure_sha256",
        "runtime_closure_count_key": "distribution_file_count",
        "runtime_closure_sha256": "sha256:" + "5" * 64,
        "runtime_closure_file_count": 1,
    }


def test_strict_success_terminal_validates_independent_coverage_arithmetic() -> None:
    root = _new_verification_root("strict-success-terminal")
    manifest_path = root / "manifest.json"
    output_root = root / "unused-output"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    payload = _synthetic_success_payload(python, root)
    stdout = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    script = f"print({stdout!r})"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
        expected_success_terminal=_synthetic_success_contract(payload, python, root),
        snapshot_roots=(manifest_path, python),
    )

    result = _run_harness_with_argument_array(
        task_id="E00.5",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    evidence = json.loads(next(root.glob("evidence-*.json")).read_text(encoding="utf-8"))
    assert evidence["status"] == "HARNESS_TEST_ONLY"
    assert evidence["success_terminal_verification"] == {
        "required": True,
        "valid": True,
        "terminal": "PASSED",
        "reasons": [],
    }


@pytest.mark.parametrize(
    "mutation",
    (
        "passed_false",
        "noncanonical",
        "fake_input_closure",
        "missing_runtime",
        "wrong_runtime_path",
        "wrong_coverage_root",
        "duplicate_nested_key",
    ),
)
def test_invalid_or_noncanonical_success_terminal_is_blocked(mutation: str) -> None:
    root = _new_verification_root("invalid-success-terminal")
    manifest_path = root / "manifest.json"
    output_root = root / "unused-output"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    payload = _synthetic_success_payload(python, root)
    if mutation == "passed_false":
        module_results = payload["module_results"]
        assert isinstance(module_results, list)
        module_result = module_results[0]
        assert isinstance(module_result, dict)
        module_result["passed"] = False
    elif mutation == "fake_input_closure":
        payload["input_closure_sha256"] = "sha256:" + "f" * 64
    elif mutation == "missing_runtime":
        payload.pop("runtime_identities")
    elif mutation == "wrong_runtime_path":
        runtime_identities = payload["runtime_identities"]
        assert isinstance(runtime_identities, dict)
        runtime_identities["coverage"] = str(manifest_path)
    elif mutation == "wrong_coverage_root":
        payload["coverage_root"] = str(root)
    separators = None if mutation == "noncanonical" else (",", ":")
    stdout = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=separators)
    if mutation == "duplicate_nested_key":
        stdout = stdout.replace('"passed":true', '"passed":true,"passed":true', 1)
    script = f"print({stdout!r})"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
        expected_success_terminal=_synthetic_success_contract(payload, python, root),
        snapshot_roots=(manifest_path, python),
    )

    result = _run_harness_with_argument_array(
        task_id="E00.5",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode != 0
    evidence = json.loads(next(root.glob("evidence-*.json")).read_text(encoding="utf-8"))
    assert evidence["status"] == "BLOCKED"
    assert evidence["success_terminal_verification"]["valid"] is False


def test_detached_child_race_is_preserved_as_observer_only_blocker() -> None:
    root = _new_verification_root("detached-child-race")
    manifest_path = root / "manifest.json"
    output_root = root / "late-output"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    child = (
        "from pathlib import Path; import sys,time; time.sleep(0.8); "
        "p=Path(sys.argv[1]); p.mkdir(); (p/'late.txt').write_text('late',encoding='utf-8')"
    )
    script = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable,'-c',{child!r},sys.argv[1]],"
        "creationflags=8,close_fds=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
    )
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
    )

    result = _run_harness_with_argument_array(
        task_id="E00.5",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    deadline = time.monotonic() + 3.0
    late_path = output_root / "late.txt"
    while not late_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert late_path.read_text(encoding="utf-8") == "late"
    evidence = json.loads(next(root.glob("evidence-*.json")).read_text(encoding="utf-8"))
    assert evidence["status"] == "HARNESS_TEST_ONLY"
    assert evidence["production_qualification_eligible"] is False
    assert evidence["process_authority"]["descendant_drain_confirmed"] is False
    assert "HARNESS_DESCENDANT_DRAIN_UNCONFIRMED" in evidence["qualification_blockers"]


def test_manifest_and_executable_pre_post_attestations_are_recorded() -> None:
    root = _new_verification_root("pre-post-attestations")
    manifest_path = root / "manifest.json"
    output_root = root / "output"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = "from pathlib import Path; import sys; Path(sys.argv[1]).mkdir()"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
    )

    result = _run_harness_with_argument_array(
        task_id="E00.5",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    evidence = json.loads(next(root.glob("evidence-*.json")).read_text(encoding="utf-8"))
    assert evidence["manifest_attestation"]["pre_sha256"] == hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    assert evidence["manifest_attestation"]["pre_post_match"] is True
    executable = evidence["executable_attestation"]
    assert executable["pre_sha256"] == hashlib.sha256(python.read_bytes()).hexdigest()
    assert executable["pre_post_match"] is True
    assert executable["same_handle_launch"] is False
    assert "HARNESS_EXECUTABLE_SAME_HANDLE_LAUNCH_ABSENT" in evidence["qualification_blockers"]
    assert len(evidence["before_snapshot_closure_sha256"]) == 64
    assert len(evidence["after_snapshot_closure_sha256"]) == 64
    assert evidence["stdout_attestation"]["persisted_raw"] is False
    assert evidence["stderr_attestation"]["persisted_raw"] is False
    assert "stdout" not in evidence
    assert "stderr" not in evidence


def test_alternate_data_stream_output_path_is_rejected_before_process_start() -> None:
    root = _new_verification_root("ads-output")
    manifest_path = root / "manifest.json"
    output_root = Path(str(root / "output") + ":stream")
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = "raise SystemExit('must never execute')"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
    )

    result = _run_harness_with_argument_array(
        task_id="E00.5",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode != 0
    assert "alternate data stream" in (result.stderr + result.stdout).lower()
    assert not list(root.glob("reservation-*.json"))
    assert not list(root.glob("evidence-*.json"))


def test_scratch_toolchain_contract_is_observer_only_and_never_production() -> None:
    root = _new_verification_root("scratch-toolchain-green")
    manifest_path = root / "manifest.json"
    output_root = root / "declared-output"
    scratch_root = _new_runtime_scratch_root("green")
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = "from pathlib import Path; import sys; Path(sys.argv[1]).mkdir()"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
        task_id="T1.3b",
    )
    _add_scratch_toolchain_contract(
        manifest_path,
        mutable_roots=(scratch_root,),
        cargo_target_dir=scratch_root,
    )

    result = _run_harness_with_argument_array(
        task_id="T1.3b",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    evidence = json.loads(next(root.glob("evidence-*.json")).read_text("utf-8"))
    assert evidence["status"] == "HARNESS_TEST_ONLY"
    assert evidence["scratch_toolchain_only"] is True
    assert evidence["mutable_scratch_roots"] == [str(scratch_root)]
    assert evidence["production_qualification_eligible"] is False
    assert "HARNESS_MUTABLE_SCRATCH_TOOLCHAIN_ONLY" in evidence[
        "qualification_blockers"
    ]


@pytest.mark.parametrize(
    "case",
    ["outside-runtime", "runtime-root", "missing-root", "ads", "target-mismatch"],
)
def test_invalid_scratch_toolchain_roots_are_rejected_before_process_start(
    case: str,
) -> None:
    root = _new_verification_root(f"scratch-toolchain-{case}")
    manifest_path = root / "manifest.json"
    output_root = root / "must-not-exist"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = "from pathlib import Path; import sys; Path(sys.argv[1]).mkdir()"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
        task_id="T1.3b",
    )
    runtime_scratch = Path(r"D:\personal\obsidian_workflow-runtime\scratch").resolve(
        strict=True
    )
    valid = _new_runtime_scratch_root(f"{case}-valid")
    mutable: Path | str = valid
    target: Path | str = valid
    if case == "outside-runtime":
        mutable = root / "outside-runtime"
        Path(mutable).mkdir()
        target = mutable
    elif case == "runtime-root":
        mutable = runtime_scratch
        target = runtime_scratch
    elif case == "missing-root":
        mutable = runtime_scratch / f"missing-{uuid.uuid4().hex}"
        target = mutable
    elif case == "ads":
        mutable = f"{valid}:stream"
        target = mutable
    elif case == "target-mismatch":
        target = _new_runtime_scratch_root("mismatch-target")
    _add_scratch_toolchain_contract(
        manifest_path,
        mutable_roots=(mutable,),
        cargo_target_dir=target,
    )

    result = _run_harness_with_argument_array(
        task_id="T1.3b",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode != 0
    assert not output_root.exists()
    assert not list(root.glob("reservation-*.json"))
    assert not list(root.glob("evidence-*.json"))


def test_scratch_toolchain_reparse_root_is_rejected_before_process_start() -> None:
    root = _new_verification_root("scratch-toolchain-reparse")
    manifest_path = root / "manifest.json"
    output_root = root / "must-not-exist"
    target = _new_runtime_scratch_root("junction-target")
    junction = target.parent / f"harness-junction-{uuid.uuid4().hex}"
    junction_result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(target)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if junction_result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = "from pathlib import Path; import sys; Path(sys.argv[1]).mkdir()"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
        task_id="T1.3b",
    )
    _add_scratch_toolchain_contract(
        manifest_path,
        mutable_roots=(junction,),
        cargo_target_dir=junction,
    )

    result = _run_harness_with_argument_array(
        task_id="T1.3b",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode != 0
    assert "reparse" in (result.stderr + result.stdout).lower()
    assert not output_root.exists()
    assert not list(root.glob("reservation-*.json"))
    assert not list(root.glob("evidence-*.json"))


def test_declared_output_rejects_existing_junction_ancestry_before_process_start() -> None:
    root = _new_verification_root("junction-output")
    manifest_path = root / "manifest.json"
    junction_target = root / "junction-target"
    junction_target.mkdir()
    junction = root / "junction"
    junction_result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(junction_target)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if junction_result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")

    output_root = junction / "escaped-output"
    physical_escape = junction_target / "escaped-output"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = "from pathlib import Path; import sys; Path(sys.argv[1]).mkdir()"
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
        task_id="T1.3b",
    )

    result = _run_harness_with_argument_array(
        task_id="T1.3b",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode != 0
    assert not physical_escape.exists()
    assert not list(root.glob("reservation-*.json"))
    assert not list(root.glob("evidence-*.json"))


def test_verification_root_rejects_raw_junction_even_when_target_stays_in_allowlist() -> None:
    parent = _new_verification_root("junction-verification-root")
    target = parent / "target"
    target.mkdir()
    junction = parent / "junction"
    junction_result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(target)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if junction_result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")
    manifest_path = target / "manifest.json"
    output_root = junction / "must-not-exist"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = "from pathlib import Path; import sys; Path(sys.argv[1]).mkdir()"
    _write_command_manifest(
        root=target,
        manifest_path=manifest_path,
        python=python,
        script=script,
    )

    result = _run_harness_with_argument_array(
        task_id="E00.5",
        root=junction,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    assert result.returncode != 0
    assert "reparse" in (result.stderr + result.stdout).lower()
    assert not (target / "must-not-exist").exists()
    assert not list(target.glob("reservation-*.json"))
    assert not list(target.glob("evidence-*.json"))


def test_process_created_reparse_output_fails_closed_with_evidence() -> None:
    root = _new_verification_root("process-created-reparse")
    manifest_path = root / "manifest.json"
    output_root = root / "declared-output"
    python = Path(os.environ.get("PYTHON", os.sys.executable)).resolve(strict=True)
    script = (
        "from pathlib import Path; import subprocess,sys; "
        "p=Path(sys.argv[1]); target=p.parent/'physical-target'; target.mkdir(); "
        "r=subprocess.run(['cmd.exe','/c','mklink','/J',str(p),str(target)],"
        "capture_output=True); raise SystemExit(r.returncode)"
    )
    _write_command_manifest(
        root=root,
        manifest_path=manifest_path,
        python=python,
        script=script,
    )

    result = _run_harness_with_argument_array(
        task_id="E00.5",
        root=root,
        manifest=manifest_path,
        command_id="emit-create-new-output",
        executable=python,
        arguments=("-c", script, str(output_root)),
    )

    if not output_root.exists():
        pytest.skip("Windows junction creation is unavailable")
    assert result.returncode != 0
    evidence = json.loads(next(root.glob("evidence-*.json")).read_text(encoding="utf-8"))
    assert evidence["status"] == "BLOCKED"
    assert evidence["output_integrity_errors"]
    assert evidence["snapshot_validation_errors"] == [
        "POST_PROCESS_SNAPSHOT_FAILED_CLOSED"
    ]
