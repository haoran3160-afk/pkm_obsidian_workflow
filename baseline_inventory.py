"""Read-only inventory for workflow sources and protected Vault paths."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from workflow_contracts import (
    FetchTarget,
    ObservedSourceRecord,
    PathIdentityRecord,
    SourceBaselineManifest,
    UnresolvedReference,
    VaultBaselineManifest,
)

URL_PATTERN = re.compile(r"https?://[^\s<>()\]\"']+")
FETCH_TARGET_NAMESPACE = uuid.UUID("94dfdaaf-5404-48fd-89bb-3974cac0175d")


@dataclass(frozen=True)
class WriteWindow:
    started_at: datetime
    ended_at: datetime

    def __post_init__(self) -> None:
        if self.started_at.tzinfo is None or self.ended_at.tzinfo is None:
            raise ValueError("write windows must be timezone-aware")
        if self.ended_at < self.started_at:
            raise ValueError("write window cannot end before it starts")


@dataclass(frozen=True)
class EntrypointObservation:
    kind: str
    object_id: str
    state: str
    definition_hash: str
    evidence_ref: str
    write_targets: tuple[str, ...] = ()
    active_windows: tuple[WriteWindow, ...] = ()
    definition_text: str = ""


@dataclass(frozen=True)
class ObservedWrite:
    path: str
    observed_at: datetime
    evidence_ref: str

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed write time must be timezone-aware")


@dataclass(frozen=True)
class WriteAttribution:
    path: str
    observed_at: datetime
    evidence_ref: str
    status: str
    owner_object_id: str | None
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class EntrypointAttributionReport:
    generated_at: datetime
    covered_kinds: tuple[str, ...]
    observations: tuple[EntrypointObservation, ...]
    attributions: tuple[WriteAttribution, ...]
    unresolved_references: tuple[UnresolvedReference, ...]
    blocked: bool

    def assert_ready_for_shadow_or_gate_a(self) -> None:
        if self.blocked:
            raise RuntimeError("UNATTRIBUTED_WRITE blocks shadow execution and Gate A")

    def to_json_value(self) -> dict[str, object]:
        def encode(value: object) -> object:
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, tuple):
                return [encode(item) for item in value]
            if hasattr(value, "model_dump"):
                return value.model_dump(mode="json")  # type: ignore[attr-defined]
            if hasattr(value, "__dataclass_fields__"):
                return {key: encode(item) for key, item in asdict(value).items()}
            if isinstance(value, dict):
                return {str(key): encode(item) for key, item in value.items()}
            return value

        encoded = encode(self)
        if not isinstance(encoded, dict):
            raise TypeError("attribution report encoding must be an object")
        return encoded


class EntrypointAttributor:
    REQUIRED_KINDS = (
        "windows_task",
        "codex_automation",
        "python_scheduler",
        "startup",
        "service",
        "login_script",
        "current_process",
        "ui_api",
        "manual_log",
    )

    def attribute(
        self,
        observations: Sequence[EntrypointObservation],
        writes: Sequence[ObservedWrite],
    ) -> EntrypointAttributionReport:
        covered = {observation.kind for observation in observations}
        missing = set(self.REQUIRED_KINDS) - covered
        if missing:
            raise ValueError("entrypoint evidence is missing kinds: " + ", ".join(sorted(missing)))
        attributions: list[WriteAttribution] = []
        for write in writes:
            owners = [
                observation.object_id
                for observation in observations
                if self._matches(observation, write)
            ]
            owner = owners[0] if len(owners) == 1 else None
            attributions.append(
                WriteAttribution(
                    path=write.path,
                    observed_at=write.observed_at,
                    evidence_ref=write.evidence_ref,
                    status=("ATTRIBUTED" if owner is not None else "UNATTRIBUTED_WRITE"),
                    owner_object_id=owner,
                    candidates=tuple(sorted(owners)),
                )
            )
        unresolved = self._definition_references(observations)
        return EntrypointAttributionReport(
            generated_at=_utc_now(),
            covered_kinds=tuple(sorted(covered)),
            observations=tuple(observations),
            attributions=tuple(attributions),
            unresolved_references=tuple(unresolved),
            blocked=any(attribution.status == "UNATTRIBUTED_WRITE" for attribution in attributions),
        )

    @staticmethod
    def _matches(
        observation: EntrypointObservation,
        write: ObservedWrite,
    ) -> bool:
        path = write.path.replace("\\", "/").casefold()
        target_match = any(
            path.startswith(target.replace("\\", "/").casefold().rstrip("/"))
            for target in observation.write_targets
        )
        time_match = any(
            window.started_at <= write.observed_at <= window.ended_at
            for window in observation.active_windows
        )
        return target_match and time_match

    @staticmethod
    def _definition_references(
        observations: Sequence[EntrypointObservation],
    ) -> list[UnresolvedReference]:
        references: list[UnresolvedReference] = []
        for observation in observations:
            raw = observation.definition_text.encode("utf-8")
            origin_hash = _sha256_bytes(raw)
            for index, match in enumerate(URL_PATTERN.finditer(observation.definition_text)):
                references.append(
                    UnresolvedReference(
                        reference_id=f"unresolved-{uuid.uuid4()}",
                        origin_object_id=observation.object_id,
                        origin_hash=origin_hash,
                        value=match.group(0).rstrip(".,;:"),
                        reason=(f"entrypoint-url-{index}-requires-attribution"),
                    )
                )
        return references


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_json(path: Path) -> object:
    text = path.read_text(encoding="utf-8-sig")
    return json.loads(text)


def _origin_key(path: Path, collection: str, index: int) -> str:
    return f"{path.resolve()}::{collection}::{index}"


class InventoryBuilder:
    """Build immutable inventories without mutating any scanned object."""

    def scan_config_files(
        self,
        config_files: Sequence[Path],
        *,
        workflow_files: Sequence[Path] = (),
        previous_records: Sequence[ObservedSourceRecord] = (),
    ) -> SourceBaselineManifest:
        previous_by_origin = {record.legacy_record_id: record for record in previous_records}
        observed: list[ObservedSourceRecord] = []
        target_members: dict[tuple[str, str], list[str]] = {}
        origin_counts: Counter[str] = Counter()

        for supplied_path in config_files:
            path = supplied_path.resolve(strict=True)
            raw = _read_json(path)
            if not isinstance(raw, dict):
                raise ValueError(f"source config must be a JSON object: {path}")

            collections = (
                ("rss_feeds", "rss", "url"),
                ("youtube_channels", "youtube", "channel_id"),
            )
            for collection, adapter, endpoint_key in collections:
                records = raw.get(collection, [])
                if records is None:
                    continue
                if not isinstance(records, list):
                    raise ValueError(f"{collection} must be a list in {path}")

                for index, raw_record in enumerate(records):
                    if not isinstance(raw_record, dict):
                        raise ValueError(f"{collection}[{index}] must be an object in {path}")
                    endpoint_value = str(raw_record.get(endpoint_key, "")).strip()
                    if not endpoint_value:
                        raise ValueError(
                            f"{collection}[{index}] is missing {endpoint_key} in {path}"
                        )
                    endpoint = (
                        endpoint_value
                        if adapter == "rss"
                        else f"https://www.youtube.com/channel/{endpoint_value}"
                    )
                    name = str(raw_record.get("name", "")).strip() or endpoint_value
                    enabled = bool(raw_record.get("enabled", True))
                    legacy_record_id = _origin_key(path, collection, index)
                    previous = previous_by_origin.get(legacy_record_id)
                    now = _utc_now()
                    record = ObservedSourceRecord(
                        observed_record_id=(
                            previous.observed_record_id if previous else f"observed-{uuid.uuid4()}"
                        ),
                        source_id=(previous.source_id if previous else f"source-{uuid.uuid4()}"),
                        legacy_record_id=legacy_record_id,
                        origin_path=str(path),
                        origin_record_index=index,
                        origin_collection=collection,
                        raw_record_hash=_sha256_bytes(_canonical_json(raw_record)),
                        name=name,
                        adapter=adapter,
                        endpoint=endpoint,
                        enabled=enabled,
                        first_seen_at=previous.first_seen_at if previous else now,
                    )
                    observed.append(record)
                    origin_counts[str(path)] += 1
                    if enabled:
                        target_members.setdefault((adapter, endpoint), []).append(
                            record.observed_record_id
                        )

        fetch_targets = [
            FetchTarget(
                fetch_target_id=(
                    "fetch-"
                    + str(
                        uuid.uuid5(
                            FETCH_TARGET_NAMESPACE,
                            f"{adapter}\0{endpoint}",
                        )
                    )
                ),
                adapter=adapter,
                endpoint=endpoint,
                observed_record_ids=sorted(record_ids),
            )
            for (adapter, endpoint), record_ids in sorted(target_members.items())
        ]

        unresolved = self._scan_unresolved_references(workflow_files)
        universe_material = [
            {
                "origin": record.legacy_record_id,
                "observed_record_id": record.observed_record_id,
                "source_id": record.source_id,
                "raw_record_hash": record.raw_record_hash,
                "enabled": record.enabled,
            }
            for record in sorted(observed, key=lambda value: value.legacy_record_id)
        ]
        universe_material.extend(
            {
                "unresolved_reference_id": reference.reference_id,
                "origin": reference.origin_object_id,
                "origin_hash": reference.origin_hash,
                "value": reference.value,
            }
            for reference in unresolved
        )

        return SourceBaselineManifest(
            baseline_id=f"source-baseline-{uuid.uuid4()}",
            generated_at=_utc_now(),
            observed_sources=observed,
            fetch_targets=fetch_targets,
            unresolved_references=unresolved,
            observed_record_count_by_origin=dict(origin_counts),
            observed_record_count=len(observed),
            fetchable_source_count=len(fetch_targets),
            disabled_source_count=sum(not record.enabled for record in observed),
            unresolved_reference_count=len(unresolved),
            universe_hash=_sha256_bytes(_canonical_json(universe_material)),
        )

    def _scan_unresolved_references(
        self,
        workflow_files: Sequence[Path],
    ) -> list[UnresolvedReference]:
        unresolved: list[UnresolvedReference] = []
        for supplied_path in workflow_files:
            path = supplied_path.resolve(strict=True)
            raw = path.read_bytes()
            text = raw.decode("utf-8-sig")
            origin_hash = _sha256_bytes(raw)
            for index, match in enumerate(URL_PATTERN.finditer(text)):
                value = match.group(0).rstrip(".,;:")
                unresolved.append(
                    UnresolvedReference(
                        reference_id=f"unresolved-{uuid.uuid4()}",
                        origin_object_id=str(path),
                        origin_hash=origin_hash,
                        value=value,
                        reason=f"workflow-url-{index}-requires-attribution",
                    )
                )
        return unresolved

    def scan_vault_paths(self, vault_root: Path) -> VaultBaselineManifest:
        root = vault_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"Vault root must be a directory: {root}")

        candidates = [root, *sorted(root.rglob("*"), key=lambda value: str(value))]
        entries: list[PathIdentityRecord] = []
        for path in candidates:
            if path.is_symlink():
                raise ValueError(f"reparse/symlink path is not allowed: {path}")
            stat = path.stat(follow_symlinks=False)
            is_directory = path.is_dir()
            relative = "." if path == root else path.relative_to(root).as_posix()
            entries.append(
                PathIdentityRecord(
                    relative_path=relative,
                    is_directory=is_directory,
                    size=0 if is_directory else stat.st_size,
                    sha256=None if is_directory else _sha256_bytes(path.read_bytes()),
                    device_id=int(stat.st_dev),
                    file_id=int(stat.st_ino),
                )
            )

        multiset_material = [
            entry.model_dump(mode="json")
            for entry in sorted(entries, key=lambda value: value.relative_path)
        ]
        return VaultBaselineManifest(
            vault_root=str(root),
            captured_at=_utc_now(),
            entries=entries,
            multiset_hash=_sha256_bytes(_canonical_json(multiset_material)),
        )


def write_baseline_create_new(
    target: Path,
    manifest: SourceBaselineManifest | VaultBaselineManifest,
) -> None:
    """Persist one immutable baseline generation using exclusive creation."""

    target.parent.resolve(strict=True)
    payload = (
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def load_source_baseline(path: Path) -> SourceBaselineManifest:
    return SourceBaselineManifest.model_validate(_read_json(path))


def observed_origin_multiset(
    records: Iterable[ObservedSourceRecord],
) -> Counter[str]:
    return Counter(record.legacy_record_id for record in records)


def write_attribution_create_new(
    target: Path,
    report: EntrypointAttributionReport,
) -> None:
    target.parent.resolve(strict=True)
    _write_json_create_new = (
        json.dumps(
            report.to_json_value(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(_write_json_create_new)
        stream.flush()
        os.fsync(stream.fileno())
