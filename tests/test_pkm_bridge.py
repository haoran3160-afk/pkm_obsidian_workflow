from __future__ import annotations

import io
import os
import uuid
from pathlib import Path

import pytest

import commit_service
import pkm_bridge
from commit_service import ArtifactInput, CommitService
from path_policy import VaultPathPolicy
from run_service import RunService
from run_store import RunStore
from workflow_contracts import AuthorizationContext, ExecutionMode, NoteDraft


def _case_root(name: str) -> Path:
    parent_value = os.environ.get("PKM_TEST_ROOT")
    if not parent_value:
        pytest.skip("PKM_TEST_ROOT is required by the no-delete test contract")
    parent = Path(parent_value).resolve(strict=True)
    root = parent / f"{name}-{uuid.uuid4().hex}"
    root.mkdir()
    return root


def _authorization() -> AuthorizationContext:
    return AuthorizationContext(
        effective_mode=ExecutionMode.OBSERVED,
        mode_epoch="mode-1",
        release_manifest_hash="a" * 64,
        spec_hash="b" * 64,
        config_hash="c" * 64,
        source_baseline_hash="d" * 64,
        gate_receipt_id="receipt-1",
        gate_consumption_id="consumption-1",
        authorization_lineage_id="lineage-1",
    )


def _system(name: str):
    root = _case_root(name)
    runtime = root / "runtime"
    vault = root / "vault"
    (vault / "30-Daily" / "AI-News").mkdir(parents=True)
    (vault / "30-Daily" / "IELTS-Preview").mkdir(parents=True)
    runtime.mkdir()
    runs = RunService(RunStore(runtime))
    run_ref = runs.start_run(
        idempotency_key=f"{name}:run",
        command="curate",
        authorization=_authorization(),
    )
    commits = CommitService(runs, VaultPathPolicy(vault))
    bridge = pkm_bridge.PKMBridge(runs=runs, commits=commits)
    return root, runs, run_ref, commits, bridge


def _artifact(
    run_id: str,
    body: str,
    filename: str = "AI-Daily-2026-07-26.md",
    *,
    target_path: str | None = None,
    logical_collection: str = "ai-daily",
) -> ArtifactInput:
    return ArtifactInput(
        draft=NoteDraft(
            run_id=run_id,
            logical_collection=logical_collection,
            target_path=target_path or f"30-Daily/AI-News/{filename}",
            version=1,
            body_hash=commit_service.stable_body_hash(body),
            review_state="approved",
        ),
        body=body,
    )


def test_bridge_consumes_canonical_run_ref_and_returns_committed_paths_only() -> None:
    root, _, run_ref, _, bridge = _system("bridge-happy")
    artifact = _artifact(run_ref.run_id, "# verified\n")

    paths = bridge.commit(
        run_ref=run_ref,
        generation_id="generation-1",
        artifacts=[artifact],
    )

    assert paths == (artifact.draft.target_path,)
    assert (root / "vault" / artifact.draft.target_path).exists()
    assert not (root / "vault" / "40-MOC" / "log.md").exists()


def test_missing_or_forged_run_ref_is_rejected_before_commit() -> None:
    root, _, run_ref, _, bridge = _system("bridge-lineage")
    artifact = _artifact(run_ref.run_id, "# body\n")
    forged = run_ref.model_copy(
        update={
            "authorization": run_ref.authorization.model_copy(
                update={"authorization_lineage_id": "forged-lineage"}
            )
        }
    )

    with pytest.raises(pkm_bridge.BridgeAuthorizationError, match="RunRef"):
        bridge.commit(  # type: ignore[arg-type]
            run_ref=None,
            generation_id="generation-missing",
            artifacts=[artifact],
        )
    with pytest.raises(pkm_bridge.BridgeAuthorizationError, match="canonical"):
        bridge.commit(
            run_ref=forged,
            generation_id="generation-forged",
            artifacts=[artifact],
        )

    assert tuple((root / "vault").rglob("*.md")) == ()


def test_disk_failure_and_path_escape_are_non_success_without_returned_path(
    monkeypatch,
) -> None:
    root, _, run_ref, commits, bridge = _system("bridge-failures")
    artifact = _artifact(run_ref.run_id, "# body\n")
    monkeypatch.setattr(
        commits.broker,
        "create_new_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk offline")),
    )

    with pytest.raises(pkm_bridge.BridgeCommitError, match="disk offline"):
        bridge.commit(
            run_ref=run_ref,
            generation_id="generation-disk",
            artifacts=[artifact],
        )
    assert not (root / "vault" / artifact.draft.target_path).exists()

    escaped = ArtifactInput(
        draft=artifact.draft.model_copy(update={"target_path": "40-MOC/log.md"}),
        body=artifact.body,
    )
    with pytest.raises(pkm_bridge.BridgeCommitError, match="allow"):
        bridge.commit(
            run_ref=run_ref,
            generation_id="generation-escape",
            artifacts=[escaped],
        )
    assert not (root / "vault" / "40-MOC" / "log.md").exists()


def test_repeated_generation_returns_same_path_without_duplicate_write() -> None:
    root, _, run_ref, _, bridge = _system("bridge-replay")
    artifact = _artifact(run_ref.run_id, "# body\n")

    first = bridge.commit(
        run_ref=run_ref,
        generation_id="generation-1",
        artifacts=[artifact],
    )
    second = bridge.commit(
        run_ref=run_ref,
        generation_id="generation-1",
        artifacts=[artifact],
    )

    assert first == second == (artifact.draft.target_path,)
    assert len(tuple((root / "vault").rglob("*.md"))) == 1


def test_batch_partial_records_result_and_raises_non_success(monkeypatch) -> None:
    root, runs, run_ref, commits, bridge = _system("bridge-partial")
    first = _artifact(run_ref.run_id, "# first\n")
    second = _artifact(
        run_ref.run_id,
        "# second\n",
        target_path=("30-Daily/IELTS-Preview/IELTS-Preview-2026-07-26.md"),
        logical_collection="ielts-preview",
    )
    real_create = commits.broker.create_new_file
    calls = {"count": 0}

    def _fail_second(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("second target failed")
        return real_create(*args, **kwargs)

    monkeypatch.setattr(commits.broker, "create_new_file", _fail_second)

    with pytest.raises(pkm_bridge.BridgePartialError, match="PARTIAL"):
        bridge.commit(
            run_ref=run_ref,
            generation_id="generation-1",
            artifacts=[first, second],
        )

    assert (root / "vault" / first.draft.target_path).exists()
    assert not (root / "vault" / second.draft.target_path).exists()
    assert runs.get_run(run_ref.run_id).status.value == "PARTIAL"
    marker = root / "runtime" / "partial" / "generation-1.partial.json"
    assert marker.exists()


def test_cli_overwrite_is_exit_2_and_does_not_call_services() -> None:
    class ForbiddenRuns:
        def get_run(self, _run_id: str):
            raise AssertionError("service must not be called")

    class ForbiddenBridge:
        def commit(self, **_kwargs):
            raise AssertionError("bridge must not be called")

    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = pkm_bridge.run_cli(
        [
            "--run-id",
            "run-1",
            "--generation-id",
            "generation-1",
            "--target-path",
            "30-Daily/AI-News/note.md",
            "--content",
            "body",
            "--overwrite",
        ],
        runs=ForbiddenRuns(),  # type: ignore[arg-type]
        bridge=ForbiddenBridge(),  # type: ignore[arg-type]
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "forbidden" in stderr.getvalue().lower()


@pytest.mark.parametrize("flag", ["--update-index", "--update-log"])
def test_cli_forbids_existing_moc_or_log_updates(flag: str) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    value = ["legacy log"] if flag == "--update-log" else []

    exit_code = pkm_bridge.run_cli(
        [
            "--run-id",
            "run-1",
            "--generation-id",
            "generation-1",
            "--target-path",
            "30-Daily/AI-News/note.md",
            "--content",
            "body",
            flag,
            *value,
        ],
        runs=object(),  # type: ignore[arg-type]
        bridge=object(),  # type: ignore[arg-type]
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
