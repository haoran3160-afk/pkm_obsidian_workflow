"""Content-addressed release preparation, creation, and verification."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from workflow_contracts import (
    ReleaseInputDescriptor,
    ReleaseInputFile,
    ReleaseManifest,
    ReleaseSnapshotFile,
)


class ReleaseBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedRelease:
    input_descriptor_path: Path
    command_manifest_path: Path
    descriptor_hash: str


@dataclass(frozen=True)
class CreatedRelease:
    release_hash: str
    release_root: Path
    snapshot_root: Path
    manifest_path: Path
    entrypoint_path: Path
    harness_path: Path
    command_manifest_path: Path


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_file(path: Path) -> bytes:
    return path.read_bytes()


def _write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _write_json_new(path: Path, payload: object) -> None:
    raw = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    _write_new(path, raw)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@contextmanager
def _open_frozen_inputs(paths: Sequence[Path]) -> Iterator[None]:
    """Keep every input open and detect identity drift during materialization."""

    with ExitStack() as stack:
        identities: dict[Path, tuple[int, int, int]] = {}
        for path in paths:
            stream = stack.enter_context(path.open("rb"))
            stat = os.fstat(stream.fileno())
            identities[path] = (
                int(stat.st_dev),
                int(stat.st_ino),
                int(stat.st_size),
            )
        yield
        for path, expected in identities.items():
            stat = path.stat()
            current = (
                int(stat.st_dev),
                int(stat.st_ino),
                int(stat.st_size),
            )
            if current != expected:
                raise ReleaseBlocked(f"copy-time race changed frozen input: {path}")


class ReleaseBuilder:
    def __init__(
        self,
        *,
        seal_callback: Callable[[Path], bool] | None = None,
        input_reader: Callable[[Path], bytes] = _read_file,
        freeze_callback: (
            Callable[[Sequence[Path]], AbstractContextManager[object] | None] | None
        ) = None,
    ) -> None:
        self.seal_callback = seal_callback
        self.input_reader = input_reader
        self.freeze_callback = freeze_callback

    def prepare(
        self,
        *,
        attempt_id: str,
        source_root: Path,
        payload_paths: Sequence[Path],
        spec_path: Path,
        config_path: Path,
        source_baseline_path: Path,
        command_template_path: Path,
        interpreter_path: Path,
        node_path: Path,
        lockfile_paths: Sequence[Path],
        ui_static_root: Path | None,
        target_dir: Path,
    ) -> PreparedRelease:
        source = Path(source_root).resolve(strict=True)
        target = Path(target_dir).resolve(strict=True)
        if not source.is_dir() or not target.is_dir():
            raise ValueError("source_root and target_dir must be directories")
        payload = [Path(path).resolve(strict=True) for path in payload_paths]
        if any(not _inside(path, source) for path in payload):
            raise ReleaseBlocked("payload path is outside source root")
        if any(".vite" in path.parts for path in payload):
            raise ReleaseBlocked("ui/.vite cache cannot enter a release")
        if len(payload) != len(set(payload)):
            raise ReleaseBlocked("release payload contains duplicate paths")

        role_paths: list[tuple[str, Path, str | None]] = []
        for path in payload:
            role_paths.append(
                (
                    "source",
                    path,
                    path.relative_to(source).as_posix(),
                )
            )
        bound = (
            ("spec", Path(spec_path).resolve(strict=True), "bound/spec.md"),
            ("config", Path(config_path).resolve(strict=True), "bound/config.json"),
            (
                "source_baseline",
                Path(source_baseline_path).resolve(strict=True),
                "bound/source-baseline.json",
            ),
            ("interpreter", Path(interpreter_path).resolve(strict=True), None),
            ("node", Path(node_path).resolve(strict=True), None),
        )
        role_paths.extend(bound)
        for index, path in enumerate(lockfile_paths):
            role_paths.append(
                (
                    f"lockfile:{index}",
                    Path(path).resolve(strict=True),
                    None,
                )
            )
        if ui_static_root is not None:
            ui_root = Path(ui_static_root).resolve(strict=True)
            if not ui_root.is_dir():
                raise ReleaseBlocked("UI static root is not a directory")
            for path in sorted(ui_root.rglob("*")):
                if path.is_file():
                    role_paths.append(
                        (
                            "ui",
                            path.resolve(strict=True),
                            (Path("ui-static") / path.relative_to(ui_root)).as_posix(),
                        )
                    )

        inputs = [self._input_record(*row) for row in role_paths]
        normalized_command = self._relative_command_manifest(
            Path(command_template_path).resolve(strict=True),
            source,
        )
        command_path = target / f"command-manifest-{attempt_id}.json"
        _write_new(command_path, normalized_command)
        descriptor = ReleaseInputDescriptor(
            attempt_id=attempt_id,
            source_root=str(source),
            ui_enabled=ui_static_root is not None,
            inputs=inputs,
            relative_command_manifest_path=str(command_path),
            relative_command_manifest_hash=_hash_bytes(normalized_command),
            created_at=datetime.now(timezone.utc),
        )
        descriptor_path = target / f"input-descriptor-{attempt_id}.json"
        descriptor_bytes = (descriptor.model_dump_json(indent=2) + "\n").encode("utf-8")
        _write_new(descriptor_path, descriptor_bytes)
        return PreparedRelease(
            input_descriptor_path=descriptor_path,
            command_manifest_path=command_path,
            descriptor_hash=_hash_bytes(descriptor_bytes),
        )

    def _input_record(
        self,
        role: str,
        path: Path,
        snapshot_path: str | None,
    ) -> ReleaseInputFile:
        if not path.is_file():
            raise ReleaseBlocked(f"release input is not a file: {path}")
        raw = self.input_reader(path)
        stat = path.stat()
        return ReleaseInputFile(
            role=role,
            absolute_path=str(path),
            snapshot_path=snapshot_path,
            size=len(raw),
            sha256=_hash_bytes(raw),
            device_id=int(stat.st_dev),
            file_id=int(stat.st_ino),
        )

    def _relative_command_manifest(
        self,
        path: Path,
        source_root: Path,
    ) -> bytes:
        try:
            payload = json.loads(self.input_reader(path))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReleaseBlocked("command template is not valid JSON") from exc
        source_text = str(source_root)

        def convert(value: object) -> object:
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            if isinstance(value, list):
                return [convert(item) for item in value]
            if not isinstance(value, str):
                return value
            normalized = value.replace("/", "\\")
            root_normalized = source_text.replace("/", "\\")
            if normalized.casefold() == root_normalized.casefold():
                return "."
            prefix = root_normalized.rstrip("\\") + "\\"
            if normalized.casefold().startswith(prefix.casefold()):
                return normalized[len(prefix) :].replace("\\", "/")
            return value

        return (
            json.dumps(
                convert(payload),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")

    def create(
        self,
        *,
        input_descriptor_path: Path,
        target_dir: Path,
    ) -> CreatedRelease:
        descriptor_path = Path(input_descriptor_path).resolve(strict=True)
        release_dir = Path(target_dir).resolve(strict=True)
        try:
            descriptor = ReleaseInputDescriptor.model_validate_json(
                descriptor_path.read_text(encoding="utf-8-sig")
            )
        except Exception as exc:
            raise ReleaseBlocked("invalid release input descriptor") from exc
        command_path = Path(descriptor.relative_command_manifest_path).resolve(strict=True)
        command_bytes = self.input_reader(command_path)
        if _hash_bytes(command_bytes) != descriptor.relative_command_manifest_hash:
            raise ReleaseBlocked("relative command manifest drift")
        paths = [Path(record.absolute_path).resolve(strict=True) for record in descriptor.inputs]
        freeze_context = self._freeze(paths)
        try:
            with freeze_context:
                first_reads = self._verify_inputs(descriptor)
                payload_hash = self._payload_hash(
                    descriptor,
                    command_bytes,
                    first_reads,
                )
                release_root = release_dir / payload_hash / descriptor.attempt_id
                if release_root.exists():
                    return self._read_existing_release(
                        release_root,
                        payload_hash,
                        descriptor.attempt_id,
                    )
                snapshot_root = release_root / "snapshot"
                snapshot_root.mkdir(parents=True)
                copied: list[ReleaseSnapshotFile] = []
                for record in descriptor.inputs:
                    if record.snapshot_path is None:
                        continue
                    source = Path(record.absolute_path)
                    second = self.input_reader(source)
                    if second != first_reads[source]:
                        raise ReleaseBlocked(f"copy-time race detected for {source}")
                    target = snapshot_root / record.snapshot_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _write_new(target, second)
                    copied.append(
                        ReleaseSnapshotFile(
                            relative_path=record.snapshot_path,
                            size=len(second),
                            sha256=_hash_bytes(second),
                        )
                    )
                command_target = snapshot_root / "cmd.json"
                _write_new(command_target, command_bytes)
                copied.append(
                    ReleaseSnapshotFile(
                        relative_path=command_target.relative_to(snapshot_root).as_posix(),
                        size=len(command_bytes),
                        sha256=_hash_bytes(command_bytes),
                    )
                )
                manifest = self._build_manifest(
                    descriptor=descriptor,
                    release_hash=payload_hash,
                    snapshot_root=snapshot_root,
                    command_target=command_target,
                    copied=copied,
                )
                manifest_path = release_root / "release-manifest.json"
                _write_new(
                    manifest_path,
                    (manifest.model_dump_json(indent=2) + "\n").encode("utf-8"),
                )
                _write_json_new(
                    release_root / "SEALED.json",
                    {
                        "schema_version": 1,
                        "release_hash": payload_hash,
                        "attempt_id": descriptor.attempt_id,
                        "seal_requested_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                if self.seal_callback is None or not self.seal_callback(release_root):
                    raise ReleaseBlocked("release ACL sealing could not be established")
        except ReleaseBlocked:
            raise
        except OSError as exc:
            raise ReleaseBlocked(f"could not freeze runtime closure: {exc}") from exc
        return self._result(manifest_path, manifest)

    def _freeze(
        self,
        paths: Sequence[Path],
    ) -> AbstractContextManager[object]:
        if self.freeze_callback is None:
            return _open_frozen_inputs(paths)
        try:
            result = self.freeze_callback(paths)
        except OSError as exc:
            raise ReleaseBlocked(f"could not freeze runtime closure: {exc}") from exc
        if result is None:
            return _open_frozen_inputs(paths)
        return result

    def _verify_inputs(
        self,
        descriptor: ReleaseInputDescriptor,
    ) -> dict[Path, bytes]:
        reads: dict[Path, bytes] = {}
        for record in descriptor.inputs:
            path = Path(record.absolute_path).resolve(strict=True)
            raw = self.input_reader(path)
            stat = path.stat()
            if (
                len(raw) != record.size
                or _hash_bytes(raw) != record.sha256
                or int(stat.st_dev) != record.device_id
                or int(stat.st_ino) != record.file_id
            ):
                raise ReleaseBlocked(f"release input drift detected: {path}")
            reads[path] = raw
        return reads

    @staticmethod
    def _payload_hash(
        descriptor: ReleaseInputDescriptor,
        command_bytes: bytes,
        reads: dict[Path, bytes],
    ) -> str:
        payload = {
            "attempt_independent_inputs": [
                {
                    "role": record.role,
                    "snapshot_path": record.snapshot_path,
                    "sha256": record.sha256,
                    "size": record.size,
                }
                for record in descriptor.inputs
            ],
            "relative_command_manifest_hash": _hash_bytes(command_bytes),
            "ui_enabled": descriptor.ui_enabled,
            "readback_hashes": sorted(_hash_bytes(raw) for raw in reads.values()),
        }
        return _hash_bytes(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    @staticmethod
    def _build_manifest(
        *,
        descriptor: ReleaseInputDescriptor,
        release_hash: str,
        snapshot_root: Path,
        command_target: Path,
        copied: list[ReleaseSnapshotFile],
    ) -> ReleaseManifest:
        by_role = {record.role: record for record in descriptor.inputs}
        lockfiles = {
            record.absolute_path: record.sha256
            for record in descriptor.inputs
            if record.role.startswith("lockfile:")
        }
        entrypoint = snapshot_root / "main.py"
        harness = snapshot_root / "scripts" / "run_task_verification.ps1"
        if not entrypoint.exists() or not harness.exists():
            raise ReleaseBlocked("snapshot lacks canonical entrypoint or verification harness")
        source_hash = _hash_bytes(
            json.dumps(
                sorted(record.sha256 for record in descriptor.inputs if record.role == "source")
            ).encode("utf-8")
        )
        return ReleaseManifest(
            release_hash=release_hash,
            attempt_id=descriptor.attempt_id,
            snapshot_root=str(snapshot_root),
            entrypoint_path=str(entrypoint),
            harness_path=str(harness),
            command_manifest_path=str(command_target),
            source_hash=source_hash,
            spec_hash=by_role["spec"].sha256,
            config_hash=by_role["config"].sha256,
            source_baseline_hash=by_role["source_baseline"].sha256,
            interpreter_hash=by_role["interpreter"].sha256,
            node_hash=by_role["node"].sha256,
            lockfile_hashes=lockfiles,
            files=copied,
            ui_enabled=descriptor.ui_enabled,
            sealed=True,
            created_at=datetime.now(timezone.utc),
        )

    def verify(self, manifest_path: Path) -> ReleaseManifest:
        path = Path(manifest_path).resolve(strict=True)
        try:
            manifest = ReleaseManifest.model_validate_json(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise ReleaseBlocked("invalid release manifest") from exc
        release_root = path.parent
        seal = release_root / "SEALED.json"
        if not seal.exists():
            raise ReleaseBlocked("release is not sealed")
        snapshot = Path(manifest.snapshot_root).resolve(strict=True)
        if snapshot.parent != release_root:
            raise ReleaseBlocked("manifest snapshot path escaped release")
        for record in manifest.files:
            target = (snapshot / record.relative_path).resolve(strict=True)
            if not _inside(target, snapshot) or not target.is_file():
                raise ReleaseBlocked("snapshot file is missing or escaped")
            raw = target.read_bytes()
            if len(raw) != record.size or _hash_bytes(raw) != record.sha256:
                raise ReleaseBlocked(f"snapshot drift detected: {record.relative_path}")
        return manifest

    def _read_existing_release(
        self,
        release_root: Path,
        release_hash: str,
        attempt_id: str,
    ) -> CreatedRelease:
        manifest_path = release_root / "release-manifest.json"
        if not manifest_path.exists() or not (release_root / "SEALED.json").exists():
            raise ReleaseBlocked("existing release attempt is incomplete and cannot be repaired")
        manifest = self.verify(manifest_path)
        if manifest.release_hash != release_hash or manifest.attempt_id != attempt_id:
            raise ReleaseBlocked("existing release attempt differs")
        return self._result(manifest_path, manifest)

    @staticmethod
    def _result(
        manifest_path: Path,
        manifest: ReleaseManifest,
    ) -> CreatedRelease:
        return CreatedRelease(
            release_hash=manifest.release_hash,
            release_root=manifest_path.parent,
            snapshot_root=Path(manifest.snapshot_root),
            manifest_path=manifest_path,
            entrypoint_path=Path(manifest.entrypoint_path),
            harness_path=Path(manifest.harness_path),
            command_manifest_path=Path(manifest.command_manifest_path),
        )
