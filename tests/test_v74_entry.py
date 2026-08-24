from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from pkm_workflow import cli, runtime_policy
from pkm_workflow.runtime_policy import (
    ActivationBlocked,
    ActivationReader,
    ExecutionResult,
    Mode,
    RuntimeEnvironment,
    Workflow,
)

HASHES = tuple(f"sha256:{digit * 64}" for digit in "123456789abcdef")
ACTIVATION_ID = "11111111-1111-4111-8111-111111111111"
CHALLENGE_ID = "22222222-2222-4222-8222-222222222222"
TEST_USER_CONTEXT: dict[str, object] = {
    "schema_version": "7.3-test",
    "contract_id": "test-context",
    "projects": ["PKM"],
    "technical_environment": ["Windows", "Python"],
    "priorities": [{"rank": 1, "topics": ["Agent"]}],
    "style": {
        "language": "zh-CN",
        "orientation": "engineering-decision-first",
        "evidence_first": True,
        "precision_over_recall": True,
        "abstention_allowed": True,
    },
    "prohibited_inferences": ["career_stage"],
    "personalization_rule": "cite an allowed field",
    "vault_inference_allowed": False,
}


def _canonical(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _semantic(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _create(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()


def _activation_fixture(
    root: Path,
    *,
    workflows: tuple[str, ...] = ("ai",),
    tamper: str | None = None,
    activation_mutator: Callable[[dict[str, object]], None] | None = None,
    activation_encoder: Callable[[dict[str, object]], bytes] = _canonical,
) -> Path:
    user_context_bytes = json.dumps(
        TEST_USER_CONTEXT,
        ensure_ascii=False,
        indent=2,
    ).encode() + b"\n"
    _create(
        root / "user-context.json",
        b'{"tampered":true}\n' if tamper == "user-context" else user_context_bytes,
    )
    source_policy_hashes: dict[str, str] = {}
    prompt_hashes: dict[str, str] = {}
    schema_hashes: dict[str, str] = {}
    shadow_evidence_hashes: dict[str, list[str]] = {}
    models: dict[str, dict[str, str]] = {}
    for workflow in workflows:
        source_policy_bytes = _canonical(
            {"schema": "pkm.workflow.source-policy.v1", "workflow": workflow}
        )
        source_policy_hash = _digest(source_policy_bytes)
        source_policy_hashes[workflow] = source_policy_hash
        _create(
            root
            / "source-policies"
            / workflow
            / f"sha256-{source_policy_hash.removeprefix('sha256:')}.json",
            b"tampered\n" if tamper == "source-policy" else source_policy_bytes,
        )
        prompt_bytes = f"sealed prompt for {workflow}\n".encode()
        prompt_hash = _digest(prompt_bytes)
        prompt_hashes[workflow] = prompt_hash
        _create(
            root / "prompts" / workflow / f"sha256-{prompt_hash.removeprefix('sha256:')}.txt",
            b"tampered\n" if tamper == "prompt" else prompt_bytes,
        )
        schema_bytes = _canonical(
            {"schema": "pkm.workflow.model-output-schema.v1", "workflow": workflow}
        )
        schema_hash = _digest(schema_bytes)
        schema_hashes[workflow] = schema_hash
        _create(
            root / "schemas" / workflow / f"sha256-{schema_hash.removeprefix('sha256:')}.json",
            b"tampered\n" if tamper == "schema" else schema_bytes,
        )
        shadow_bytes = _canonical(
            {
                "schema": "pkm.workflow.shadow-evidence.v1",
                "workflow": workflow,
                "gate": "QUALIFIED",
            }
        )
        shadow_hash = _digest(shadow_bytes)
        shadow_evidence_hashes[workflow] = [shadow_hash]
        _create(
            root
            / "shadow-evidence"
            / workflow
            / f"sha256-{shadow_hash.removeprefix('sha256:')}.json",
            b"tampered\n" if tamper == "shadow" else shadow_bytes,
        )
        models[workflow] = {
            "generator": "gpt-5.6-terra",
            "reviewer": "gpt-5.6-sol",
        }
    task_bundle_bytes = _canonical(
        {"schema": "pkm.workflow.task-bundle.v1", "workflows": list(workflows)}
    )
    task_bundle_hash = _digest(task_bundle_bytes)
    _create(
        root
        / "task-bundles"
        / f"sha256-{task_bundle_hash.removeprefix('sha256:')}"
        / "manifest.json",
        b"tampered\n" if tamper == "task" else task_bundle_bytes,
    )
    red_team_hashes = []
    for round_number in (1, 2):
        report_bytes = _canonical(
            {
                "schema": "pkm.workflow.red-team-report.v1",
                "round": round_number,
                "p0": 0,
                "p1": 0,
            }
        )
        report_hash = _digest(report_bytes)
        red_team_hashes.append(report_hash)
        _create(
            root / "red-team-reports" / f"sha256-{report_hash.removeprefix('sha256:')}.json",
            b"tampered\n" if tamper == "red-team" and round_number == 1 else report_bytes,
        )
    config_bytes = _canonical(
        {
            "schema": "pkm.workflow.config.v1",
            "workflows": {
                workflow: {
                    "source_policy_hash": source_policy_hashes[workflow],
                    "prompt_hash": prompt_hashes[workflow],
                    "schema_hash": schema_hashes[workflow],
                    "generator_model": models[workflow]["generator"],
                    "reviewer_model": models[workflow]["reviewer"],
                }
                for workflow in workflows
            },
            "task_bundle_hash": task_bundle_hash,
            "user_context_raw_hash": _digest(user_context_bytes),
            "user_context_canonical_hash": _digest(_semantic(TEST_USER_CONTEXT)),
        }
    )
    config_hash = _digest(config_bytes)
    _create(
        root / "config" / f"sha256-{config_hash.removeprefix('sha256:')}.json",
        b"tampered\n" if tamper == "config" else config_bytes,
    )
    release_files = {
        "entry.py": _digest(b"sealed entry\n"),
        "python.exe": _digest(b"sealed python\n"),
        "workflow.whl": _digest(b"sealed wheel\n"),
    }
    release_bytes = _canonical(
        {"schema": "pkm.workflow.release-manifest.v1", "files": release_files}
    )
    release_hash = _digest(release_bytes)
    release_root = root / "releases" / f"sha256-{release_hash.removeprefix('sha256:')}"
    _create(
        release_root / "manifest.json",
        b"tampered\n" if tamper == "release" else release_bytes,
    )
    _create(release_root / "entry.py", b"sealed entry\n")
    _create(release_root / "python.exe", b"sealed python\n")
    _create(release_root / "workflow.whl", b"sealed wheel\n")
    payload: dict[str, object] = {
        "schema": "pkm.workflow.activation-generation.v1",
        "generation": 1,
        "activation_id": ACTIVATION_ID,
        "prev_activation_hash": None,
        "approved_workflows": list(workflows),
        "release_manifest_hash": release_hash,
        "config_hash": config_hash,
        "source_policy_hashes": source_policy_hashes,
        "prompt_hashes": prompt_hashes,
        "schema_hashes": schema_hashes,
        "models": models,
        "task_bundle_hash": task_bundle_hash,
        "user_context_raw_hash": _digest(user_context_bytes),
        "user_context_canonical_hash": _digest(_semantic(TEST_USER_CONTEXT)),
        "shadow_evidence_hashes": shadow_evidence_hashes,
        "red_team_report_hashes": sorted(red_team_hashes),
        "approved_at": "2026-08-24T16:00:00+08:00",
        "challenge_id": CHALLENGE_ID,
    }
    if activation_mutator is not None:
        activation_mutator(payload)
    activation = root / "activation" / f"{1:020d}-{ACTIVATION_ID}.json"
    activation_bytes = activation_encoder(payload)
    _create(activation, activation_bytes)
    _create_approval(root, payload, activation_bytes, tamper=tamper)
    return activation


def _create_approval(
    root: Path,
    activation: dict[str, object],
    activation_bytes: bytes,
    *,
    tamper: str | None = None,
) -> None:
    challenge_id = str(activation["challenge_id"])
    challenge_bytes = _canonical(
        {
            "schema": "pkm.workflow.activation-challenge.v1",
            "challenge_id": challenge_id,
            "activation_hash": _digest(activation_bytes),
            "approved_workflows": activation["approved_workflows"],
            "expires_at": "2026-08-24T17:00:00+08:00",
        }
    )
    _create(
        root / "activation-challenges" / f"{challenge_id}.json",
        b"tampered\n" if tamper == "challenge" else challenge_bytes,
    )
    consume_bytes = _canonical(
        {
            "schema": "pkm.workflow.activation-consume.v1",
            "challenge_id": challenge_id,
            "challenge_hash": _digest(challenge_bytes),
            "activation_hash": _digest(activation_bytes),
            "consumed_at": activation["approved_at"],
        }
    )
    _create(
        root / "activation-consumed" / f"{challenge_id}.json",
        b"tampered\n" if tamper == "consume" else consume_bytes,
    )


def _reader(root: Path) -> ActivationReader:
    user_context_path = root / "user-context.json"
    expected_bytes = json.dumps(
        TEST_USER_CONTEXT,
        ensure_ascii=False,
        indent=2,
    ).encode() + b"\n"
    return ActivationReader(
        root,
        user_context_path,
        _digest(expected_bytes),
        _digest(_semantic(TEST_USER_CONTEXT)),
    )


def _environment(root: Path) -> RuntimeEnvironment:
    expected_bytes = json.dumps(
        TEST_USER_CONTEXT,
        ensure_ascii=False,
        indent=2,
    ).encode() + b"\n"
    return RuntimeEnvironment(
        root,
        root / "user-context.json",
        _digest(expected_bytes),
        _digest(_semantic(TEST_USER_CONTEXT)),
    )


def _replace(field: str, value: object) -> Callable[[dict[str, object]], None]:
    def mutate(payload: dict[str, object]) -> None:
        payload[field] = value

    return mutate


def test_importing_v74_cli_does_not_reactivate_legacy_write_or_api_modules() -> None:
    for name in ("main", "llm_digest", "pkm_bridge", "fetcher", "writer", "ui_server"):
        sys.modules.pop(name, None)
    importlib.reload(sys.modules["pkm_workflow.cli"])
    assert not {
        "main",
        "llm_digest",
        "pkm_bridge",
        "fetcher",
        "writer",
        "ui_server",
    }.intersection(sys.modules)


def test_flat_cli_rejects_legacy_commands_and_every_free_path(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (
        ["shadow", "--workflow", "ai"],
        ["--workflow", "ai", "--mode", "shadow", "--vault", "D:/other"],
        ["--workflow", "ai", "--mode", "shadow", "--output", "note.md"],
        ["--workflow", "ai", "--mode", "shadow", "--model", "free"],
    ):
        with pytest.raises(SystemExit) as raised:
            cli.main(argv)
        assert raised.value.code == 2
    assert "D:/other" not in capsys.readouterr().out


def test_wrapper_and_packaged_cli_use_the_same_engine_and_contract(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    script = repository / "scripts" / "run_workflow.py"
    argv = ["--workflow", "ai", "--mode", "shadow"]
    direct_environment = dict(os.environ)
    direct_environment["PYTHONPATH"] = str(repository / "src")
    direct = subprocess.run(
        [sys.executable, "-m", "pkm_workflow.cli", *argv],
        cwd=tmp_path,
        env=direct_environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    wrapped = subprocess.run(
        [sys.executable, str(script), *argv],
        cwd=tmp_path,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert direct.returncode == wrapped.returncode == 3
    assert json.loads(direct.stdout) == json.loads(wrapped.stdout)
    assert json.loads(direct.stdout)["reason"] == "SHADOW_ENGINE_NOT_IMPLEMENTED"


def test_all_modes_call_one_engine_and_cli_preserves_domain_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: list[tuple[Workflow, Mode, bool]] = []

    def fake_execute(request: object, environment: object) -> ExecutionResult:
        assert isinstance(environment, RuntimeEnvironment)
        observed.append((request.workflow, request.mode, request.due_only))  # type: ignore[attr-defined]
        return ExecutionResult(5, {"status": "BLOCKED", "reason": "QUOTA_DELAYED"})

    monkeypatch.setattr(cli, "execute_workflow", fake_execute)
    for mode in Mode:
        assert cli.main(["--workflow", "weekly", "--mode", mode.value]) == 5
        assert json.loads(capsys.readouterr().out)["reason"] == "QUOTA_DELAYED"
    assert observed == [(Workflow.WEEKLY, mode, False) for mode in Mode]


def test_direct_and_wrapper_boundaries_redact_unclassified_internal_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_request: object, _environment: object) -> ExecutionResult:
        raise RuntimeError("private path and secret must not escape")

    monkeypatch.setattr(cli, "execute_workflow", fail)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_workflow.py"
    spec = importlib.util.spec_from_file_location("pkm_run_workflow_error", script)
    assert spec is not None and spec.loader is not None
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    for invoke in (cli.main, wrapper.main):
        assert invoke(["--workflow", "ai", "--mode", "shadow"]) == 10
        payload = json.loads(capsys.readouterr().out)
        assert payload["reason"] == "INTERNAL_ERROR"
        assert payload["vault_write"] is False
        assert "private path" not in json.dumps(payload)


def test_nonproduction_modes_remain_read_only_and_truthfully_blocked(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for mode in ("doctor", "dry-run", "shadow"):
        assert cli.main(["--workflow", "ielts", "--mode", mode]) == 3
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == "7.4"
        assert payload["vault_write"] is False
        assert payload["production_activation"] is False
        assert "NATIVE_BROKER_NOT_QUALIFIED" not in payload.get("blockers", [])


def test_production_without_activation_fails_before_business_engine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli.RuntimeEnvironment,
        "production",
        classmethod(lambda cls: _environment(tmp_path)),
    )
    assert cli.main(["--workflow", "ai", "--mode", "production"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["reason"] == "ACTIVATION_MISSING"
    assert payload["vault_write"] is False


def test_activation_reader_accepts_only_exact_structural_scope_and_hashes(tmp_path: Path) -> None:
    _activation_fixture(tmp_path)
    selected = _reader(tmp_path).select(Workflow.AI)
    assert selected.generation == 1
    assert selected.approved_workflows == (Workflow.AI,)
    assert selected.release_manifest_path.is_file()
    assert selected.config_path.is_file()

    with pytest.raises(ActivationBlocked, match="ACTIVATION_SCOPE_MISMATCH"):
        _reader(tmp_path).select(Workflow.WEEKLY)


def test_activation_reader_selects_a_contiguous_hash_chained_higher_generation(
    tmp_path: Path,
) -> None:
    first = _activation_fixture(tmp_path)
    payload = json.loads(first.read_text(encoding="utf-8"))
    second_id = "55555555-5555-4555-8555-555555555555"
    second_challenge_id = "66666666-6666-4666-8666-666666666666"
    payload["generation"] = 2
    payload["activation_id"] = second_id
    payload["challenge_id"] = second_challenge_id
    payload["prev_activation_hash"] = _digest(first.read_bytes())
    second_bytes = _canonical(payload)
    _create(
        tmp_path / "activation" / f"{2:020d}-{second_id}.json",
        second_bytes,
    )
    _create_approval(tmp_path, payload, second_bytes)
    selected = _reader(tmp_path).select(Workflow.AI)
    assert selected.generation == 2
    assert selected.activation_id == second_id


@pytest.mark.parametrize(
    "mutator",
    (
        _replace("schema", "pkm.workflow.activation-generation.v2"),
        _replace("generation", True),
        _replace("activation_id", "not-a-uuid"),
        _replace("prev_activation_hash", HASHES[7]),
        _replace("approved_workflows", []),
        _replace("approved_workflows", [1]),
        _replace("approved_workflows", ["weekly", "ai"]),
        _replace("approved_workflows", ["other"]),
        _replace("source_policy_hashes", {}),
        _replace("prompt_hashes", {"ai": "bad"}),
        _replace("models", {}),
        _replace("models", {"ai": {"generator": "free", "reviewer": "free"}}),
        _replace("task_bundle_hash", "bad"),
        _replace("shadow_evidence_hashes", []),
        _replace("red_team_report_hashes", [HASHES[5]]),
        _replace("approved_at", 1),
        _replace("approved_at", "2026-08-24T16:00:00"),
        _replace("challenge_id", "bad"),
    ),
)
def test_activation_reader_rejects_every_malformed_control_field(
    tmp_path: Path,
    mutator: Callable[[dict[str, object]], None],
) -> None:
    _activation_fixture(tmp_path, activation_mutator=mutator)
    with pytest.raises(ActivationBlocked):
        _reader(tmp_path).select(Workflow.AI)


@pytest.mark.parametrize(
    "encoder",
    (
        lambda _payload: b"\xff",
        lambda _payload: b"{",
        lambda payload: json.dumps(payload, indent=2).encode(),
        lambda _payload: b"[]\n",
        lambda _payload: b'{"schema":NaN}\n',
        lambda _payload: b'{"schema":"a","schema":"b"}\n',
    ),
)
def test_activation_reader_rejects_noncanonical_or_ambiguous_json(
    tmp_path: Path,
    encoder: Callable[[dict[str, object]], bytes],
) -> None:
    _activation_fixture(tmp_path, activation_encoder=encoder)
    with pytest.raises(ActivationBlocked, match="ACTIVATION_SCHEMA_INVALID"):
        _reader(tmp_path).select(Workflow.AI)


def test_activation_reader_rejects_empty_invalid_and_gapped_directories(tmp_path: Path) -> None:
    (tmp_path / "activation").mkdir(parents=True)
    with pytest.raises(ActivationBlocked, match="ACTIVATION_MISSING"):
        _reader(tmp_path).select(Workflow.AI)

    invalid_root = tmp_path / "invalid"
    _create(invalid_root / "activation" / "unexpected.txt", b"blocked\n")
    with pytest.raises(ActivationBlocked, match="ACTIVATION_SCHEMA_INVALID"):
        _reader(invalid_root).select(Workflow.AI)

    gapped_root = tmp_path / "gapped"
    _create(
        gapped_root / "activation" / f"{2:020d}-{ACTIVATION_ID}.json",
        b"{}\n",
    )
    with pytest.raises(ActivationBlocked, match="ACTIVATION_CHAIN_INVALID"):
        _reader(gapped_root).select(Workflow.AI)


def test_activation_reader_rejects_reparse_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _activation_fixture(tmp_path)
    monkeypatch.setattr(runtime_policy, "_is_reparse", lambda path: path == tmp_path)
    with pytest.raises(ActivationBlocked, match="ACTIVATION_ROOT_INVALID"):
        _reader(tmp_path).select(Workflow.AI)


def test_activation_reader_rejects_a_real_parent_junction_escape(tmp_path: Path) -> None:
    root = tmp_path / "durable"
    outside = tmp_path / "outside-releases"
    root.mkdir()
    outside.mkdir()
    junction = root / "releases"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")
    _activation_fixture(root)
    with pytest.raises(ActivationBlocked, match="ACTIVATION_RELEASE_HASH_MISMATCH"):
        _reader(root).select(Workflow.AI)


@pytest.mark.parametrize(
    ("tamper", "reason"),
    (
        ("release", "ACTIVATION_RELEASE_HASH_MISMATCH"),
        ("config", "ACTIVATION_CONFIG_HASH_MISMATCH"),
        ("task", "ACTIVATION_TASK_BUNDLE_MISMATCH"),
        ("source-policy", "ACTIVATION_SOURCE_POLICY_MISMATCH"),
        ("prompt", "ACTIVATION_PROMPT_MISMATCH"),
        ("schema", "ACTIVATION_MODEL_SCHEMA_MISMATCH"),
        ("shadow", "ACTIVATION_SHADOW_EVIDENCE_MISMATCH"),
        ("red-team", "ACTIVATION_RED_TEAM_EVIDENCE_MISMATCH"),
        ("user-context", "ACTIVATION_USER_CONTEXT_MISMATCH"),
        ("challenge", "ACTIVATION_APPROVAL_MISMATCH"),
        ("consume", "ACTIVATION_APPROVAL_MISMATCH"),
    ),
)
def test_activation_reader_rejects_every_bound_artifact_mismatch(
    tmp_path: Path,
    tamper: str,
    reason: str,
) -> None:
    _activation_fixture(tmp_path, tamper=tamper)
    with pytest.raises(ActivationBlocked, match=reason):
        _reader(tmp_path).select(Workflow.AI)


def test_execution_result_and_due_only_policy_reject_unknown_control_values() -> None:
    with pytest.raises(ValueError, match="unknown domain exit"):
        ExecutionResult(7, {})
    result = runtime_policy.execute_workflow(
        runtime_policy.RunRequest(Workflow.AI, Mode.DOCTOR, due_only=True),
        RuntimeEnvironment(Path("missing"), Path("missing-user-context")),
    )
    assert result.exit_code == 2
    assert result.payload["reason"] == "DUE_ONLY_MODE_INVALID"


def test_handle_bound_reader_rejects_missing_nonregular_empty_and_outside_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority"
    authority.mkdir()
    with pytest.raises(ActivationBlocked, match="MISSING"):
        runtime_policy._read_regular_file(
            authority / "missing.json",
            authority_root=authority,
            missing_code="MISSING",
            invalid_code="INVALID",
        )
    directory = authority / "directory"
    directory.mkdir()
    with pytest.raises(ActivationBlocked, match="INVALID"):
        runtime_policy._read_regular_file(
            directory,
            authority_root=authority,
            missing_code="MISSING",
            invalid_code="INVALID",
        )
    empty = authority / "empty.json"
    _create(empty, b"")
    with pytest.raises(ActivationBlocked, match="INVALID"):
        runtime_policy._read_regular_file(
            empty,
            authority_root=authority,
            missing_code="MISSING",
            invalid_code="INVALID",
        )
    outside = tmp_path / "outside.json"
    _create(outside, b"outside\n")
    with pytest.raises(ActivationBlocked, match="INVALID"):
        runtime_policy._read_regular_file(
            outside,
            authority_root=authority,
            missing_code="MISSING",
            invalid_code="INVALID",
        )
    monkeypatch.setattr(
        runtime_policy,
        "_final_path_from_fd",
        lambda _fd: (_ for _ in ()).throw(OSError("handle query failed")),
    )
    regular = authority / "regular.json"
    _create(regular, b"regular\n")
    with pytest.raises(ActivationBlocked, match="INVALID"):
        runtime_policy._read_regular_file(
            regular,
            authority_root=authority,
            missing_code="MISSING",
            invalid_code="INVALID",
        )


def test_low_level_identity_and_identifier_validators_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ActivationBlocked):
        runtime_policy._require_uuid(1)
    with pytest.raises(ActivationBlocked):
        runtime_policy._require_uuid("AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA")
    monkeypatch.setattr(runtime_policy.os.path, "commonpath", lambda _paths: (_ for _ in ()).throw(ValueError()))
    assert runtime_policy._is_within(Path("D:/a"), Path("D:/")) is False


def test_frozen_user_context_shape_and_approval_time_are_fail_closed() -> None:
    for mutation in (
        {**TEST_USER_CONTEXT, "contract_id": ""},
        {**TEST_USER_CONTEXT, "projects": []},
        {**TEST_USER_CONTEXT, "priorities": [{"rank": 0, "topics": []}]},
        {**TEST_USER_CONTEXT, "style": {}},
    ):
        with pytest.raises(ActivationBlocked, match="USER_CONTEXT"):
            runtime_policy._validate_user_context(mutation)
    for value in (1, "not-a-time", "2026-08-24T16:00:00"):
        with pytest.raises(ActivationBlocked, match="TIME"):
            runtime_policy._parse_aware_time(value, "TIME")


def test_activation_reader_rejects_unknown_fields_and_never_falls_back(tmp_path: Path) -> None:
    first = _activation_fixture(tmp_path)
    payload = json.loads(first.read_text(encoding="utf-8"))
    payload["unknown"] = True
    second_id = "33333333-3333-4333-8333-333333333333"
    payload["generation"] = 2
    payload["activation_id"] = second_id
    payload["prev_activation_hash"] = _digest(first.read_bytes())
    _create(
        tmp_path / "activation" / f"{2:020d}-{second_id}.json",
        _canonical(payload),
    )
    with pytest.raises(ActivationBlocked, match="ACTIVATION_SCHEMA_INVALID"):
        _reader(tmp_path).select(Workflow.AI)


def test_activation_reader_rejects_forks_and_bound_artifact_mismatch(tmp_path: Path) -> None:
    first = _activation_fixture(tmp_path)
    payload = json.loads(first.read_text(encoding="utf-8"))
    fork_id = "44444444-4444-4444-8444-444444444444"
    payload["activation_id"] = fork_id
    _create(
        tmp_path / "activation" / f"{1:020d}-{fork_id}.json",
        _canonical(payload),
    )
    with pytest.raises(ActivationBlocked, match="ACTIVATION_FORK"):
        _reader(tmp_path).select(Workflow.AI)

    other = tmp_path / "other"
    _activation_fixture(other, tamper="release")
    with pytest.raises(ActivationBlocked, match="ACTIVATION_RELEASE_HASH_MISMATCH"):
        _reader(other).select(Workflow.AI)


def test_structural_activation_does_not_claim_evidence_or_production_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _activation_fixture(tmp_path)
    monkeypatch.setattr(
        cli.RuntimeEnvironment,
        "production",
        classmethod(lambda cls: _environment(tmp_path)),
    )
    assert cli.main(["--workflow", "ai", "--mode", "production"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["reason"] == "ACTIVATION_EVIDENCE_SEMANTICS_NOT_IMPLEMENTED"
    assert payload["activation_generation"] == 1
    assert payload["activation_structure_validated"] is True
    assert payload["activation_validated"] is False
    assert payload["production_activation"] is False
    assert payload["vault_write"] is False


def test_wrapper_is_a_thin_adapter_without_its_own_parser() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_workflow.py"
    spec = importlib.util.spec_from_file_location("pkm_run_workflow", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert not hasattr(module, "_parser")
