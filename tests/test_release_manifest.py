from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from release_manifest import ReleaseBlocked, ReleaseBuilder


def _case_root(name: str) -> Path:
    parent_value = os.environ.get("PKM_TEST_ROOT")
    if not parent_value:
        pytest.skip("PKM_TEST_ROOT is required by the no-delete test contract")
    parent = Path(parent_value).resolve(strict=True)
    del name
    root = parent / f"r-{uuid.uuid4().hex[:6]}"
    root.mkdir()
    return root


def _write_new(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def _fixture(name: str):
    root = _case_root(name)
    source = root / "source"
    prepare = root / "prepare"
    releases = root / "releases"
    source.mkdir()
    prepare.mkdir()
    releases.mkdir()
    files = {
        "main": _write_new(source / "main.py", b"print('snapshot')\n"),
        "harness": _write_new(
            source / "scripts" / "run_task_verification.ps1",
            b"Set-StrictMode -Version 2.0\n",
        ),
        "command": _write_new(
            source / "verification_commands.json",
            json.dumps(
                {
                    "commands": {
                        "release-verify": {
                            "working_directory": str(source),
                            "executable_path": str(source / "main.py"),
                        }
                    }
                }
            ).encode("utf-8"),
        ),
        "spec": _write_new(source / "docs" / "spec.md", b"# spec\n"),
        "config": _write_new(root / "config-v3.json", b'{"schema_version":3}\n'),
        "baseline": _write_new(root / "source-baseline.json", b'{"sources":[]}\n'),
        "python": _write_new(root / "python.exe", b"python-runtime"),
        "node": _write_new(root / "node.exe", b"node-runtime"),
        "lock": _write_new(source / "package-lock.json", b'{"lockfileVersion":3}\n'),
    }
    _write_new(source / "ui" / ".vite" / "deps.json", b"existing-cache")
    return root, source, prepare, releases, files


def _prepare(
    builder: ReleaseBuilder,
    *,
    source: Path,
    prepare: Path,
    files: dict[str, Path],
):
    return builder.prepare(
        attempt_id=uuid.uuid4().hex,
        source_root=source,
        payload_paths=[
            files["main"],
            files["harness"],
            files["command"],
            files["lock"],
        ],
        spec_path=files["spec"],
        config_path=files["config"],
        source_baseline_path=files["baseline"],
        command_template_path=files["command"],
        interpreter_path=files["python"],
        node_path=files["node"],
        lockfile_paths=[files["lock"]],
        ui_static_root=None,
        target_dir=prepare,
    )


def test_prepare_and_create_make_relative_snapshot_and_exclude_vite_cache() -> None:
    root, source, prepare, releases, files = _fixture("release-happy")
    sealed: list[Path] = []
    builder = ReleaseBuilder(seal_callback=lambda path: sealed.append(path) or True)
    prepared = _prepare(
        builder,
        source=source,
        prepare=prepare,
        files=files,
    )

    result = builder.create(
        input_descriptor_path=prepared.input_descriptor_path,
        target_dir=releases,
    )
    verified = builder.verify(result.manifest_path)

    assert verified.release_hash == result.release_hash
    assert sealed == [result.release_root]
    assert result.entrypoint_path.read_bytes() == files["main"].read_bytes()
    assert result.harness_path.read_bytes() == files["harness"].read_bytes()
    assert not tuple(result.snapshot_root.rglob(".vite"))
    command_text = result.command_manifest_path.read_text(encoding="utf-8")
    assert str(source) not in command_text
    assert str(result.snapshot_root) not in command_text
    assert (result.release_root / "SEALED.json").exists()
    assert root.joinpath("source", "ui", ".vite", "deps.json").read_bytes() == (b"existing-cache")


@pytest.mark.parametrize(
    "drift_key",
    ["main", "config", "spec", "python", "node", "lock"],
)
def test_any_bound_input_drift_blocks_before_snapshot_creation(
    drift_key: str,
) -> None:
    _, source, prepare, releases, files = _fixture(f"release-drift-{drift_key}")
    prepared = _prepare(
        ReleaseBuilder(seal_callback=lambda _path: True),
        source=source,
        prepare=prepare,
        files=files,
    )
    drift_path = files[drift_key].resolve()
    builder = ReleaseBuilder(
        seal_callback=lambda _path: True,
        input_reader=lambda path: (
            b"simulated-drift" if path.resolve() == drift_path else path.read_bytes()
        ),
    )

    with pytest.raises(ReleaseBlocked, match="drift"):
        builder.create(
            input_descriptor_path=prepared.input_descriptor_path,
            target_dir=releases,
        )

    assert tuple(releases.rglob("snapshot")) == ()


def test_copy_time_race_and_unfreezable_runtime_closure_are_blocked() -> None:
    _, source, prepare, releases, files = _fixture("release-races")
    prepared = _prepare(
        ReleaseBuilder(seal_callback=lambda _path: True),
        source=source,
        prepare=prepare,
        files=files,
    )
    calls: dict[Path, int] = {}

    def racing_reader(path: Path) -> bytes:
        resolved = path.resolve()
        calls[resolved] = calls.get(resolved, 0) + 1
        if resolved == files["main"].resolve() and calls[resolved] > 1:
            return b"copy-time-race"
        return path.read_bytes()

    with pytest.raises(ReleaseBlocked, match="copy-time race"):
        ReleaseBuilder(
            seal_callback=lambda _path: True,
            input_reader=racing_reader,
        ).create(
            input_descriptor_path=prepared.input_descriptor_path,
            target_dir=releases,
        )

    def cannot_freeze(_paths):
        raise OSError("sharing violation")

    with pytest.raises(ReleaseBlocked, match="freeze runtime closure"):
        ReleaseBuilder(
            seal_callback=lambda _path: True,
            freeze_callback=cannot_freeze,
        ).create(
            input_descriptor_path=prepared.input_descriptor_path,
            target_dir=releases,
        )


def test_working_tree_changes_are_not_used_by_sealed_snapshot_verify() -> None:
    _, source, prepare, releases, files = _fixture("release-independent")
    builder = ReleaseBuilder(seal_callback=lambda _path: True)
    prepared = _prepare(
        builder,
        source=source,
        prepare=prepare,
        files=files,
    )
    result = builder.create(
        input_descriptor_path=prepared.input_descriptor_path,
        target_dir=releases,
    )
    builder.input_reader = lambda _path: b"unrelated-working-tree-change"

    verified = builder.verify(result.manifest_path)

    assert verified.release_hash == result.release_hash
