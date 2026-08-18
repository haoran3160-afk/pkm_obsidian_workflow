#!/usr/bin/env python3
"""
writer.py — Submits formatted Markdown strings to Obsidian.
Can use disk-write (for local usage) or REST API (Obsidian Local REST API).
Includes retry logic for API calls.
"""

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from urllib.parse import quote

import requests
from pydantic import Field, field_validator
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from path_policy import PathPolicyError, VaultPathPolicy
from run_store import RunStore
from workflow_contracts import ContractModel

log = logging.getLogger("pkm.writer")
SESSION = requests.Session()
SAFE_NAME_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ObsidianAPIError(Exception):
    """Custom exception raised when Obsidian REST API calls fail."""

    pass


class ArtifactKind(str, Enum):
    RAW_INBOX = "raw-inbox"
    RAW_ARCHIVE = "raw-archive"
    AI_DAILY = "ai-daily"
    IELTS_PREVIEW = "ielts-preview"
    WEEKLY = "weekly"
    LINT_REPORT = "lint-report"
    SOURCE_HEALTH = "source-health"
    USED_ARTICLES = "used-articles"
    SOURCE_ROTATION = "source-rotation"
    FEED_CACHE = "feed-cache"
    STAGING = "staging"
    ABORTED = "aborted"
    PARTIAL = "partial"


class ArtifactNamingRequest(ContractModel):
    artifact_kind: ArtifactKind
    idempotency_key: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    generation_id: str = Field(min_length=1)
    local_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timestamp: datetime

    @field_validator("run_id", "generation_id")
    @classmethod
    def identifiers_are_safe_path_components(cls, value: str) -> str:
        if SAFE_NAME_COMPONENT.fullmatch(value) is None:
            raise ValueError("identifier contains unsafe path characters")
        return value

    @field_validator("timestamp")
    @classmethod
    def timestamp_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class ArtifactNameReservation(ContractModel):
    reservation_id: str = Field(min_length=1)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_kind: ArtifactKind
    idempotency_key: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    revision: int | None = Field(default=None, ge=1)
    created_at: datetime


def _write_create_new_json(path: Path, value: object) -> None:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


class ArtifactNamer:
    """Reserve stable names so replay never reallocates another revision."""

    def __init__(self, store: RunStore, vault_policy: VaultPathPolicy) -> None:
        self._store = store
        self._vault_policy = vault_policy
        self._reservations_root = store.runtime_root / "artifact-names"
        self._reservations_root.mkdir(exist_ok=True)

    def reserve(self, request: ArtifactNamingRequest) -> ArtifactNameReservation:
        request_payload = request.model_dump(mode="json")
        request_hash = hashlib.sha256(
            json.dumps(
                request_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        key_hash = hashlib.sha256(request.idempotency_key.encode("utf-8")).hexdigest()
        target = self._reservations_root / f"{key_hash}.json"

        with self._store.coordination_lock():
            if target.exists():
                reservation = self._read_reservation(target)
                if (
                    reservation.idempotency_key != request.idempotency_key
                    or reservation.request_hash != request_hash
                ):
                    raise ValueError("idempotency key is bound to a different naming request")
                return reservation

            relative_path, revision = self._allocate_path(request)
            reservation = ArtifactNameReservation(
                reservation_id=f"artifact-name-{key_hash}",
                request_hash=request_hash,
                artifact_kind=request.artifact_kind,
                idempotency_key=request.idempotency_key,
                relative_path=relative_path,
                revision=revision,
                created_at=datetime.now(timezone.utc),
            )
            _write_create_new_json(
                target,
                {
                    "schema_version": 1,
                    "request": request_payload,
                    "reservation": reservation.model_dump(mode="json"),
                },
            )
            return reservation

    def _allocate_path(
        self,
        request: ArtifactNamingRequest,
    ) -> tuple[str, int | None]:
        kind = request.artifact_kind
        date = request.local_date
        run_id = request.run_id
        timestamp = request.timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        if kind in {ArtifactKind.RAW_INBOX, ArtifactKind.LINT_REPORT}:
            raise PathPolicyError(f"legacy Vault artifact route is disabled in v7.3: {kind.value}")

        deterministic: dict[ArtifactKind, str] = {
            ArtifactKind.RAW_ARCHIVE: (f"01-Raw/daily-feeds/{date[:7]}/{date}-Raw-Daily-Feeds.md"),
            ArtifactKind.SOURCE_HEALTH: (f"events/source-health-{timestamp}-{run_id}.json"),
            ArtifactKind.USED_ARTICLES: (
                f"events/used-articles-selection-{timestamp}-{run_id}.json"
            ),
            ArtifactKind.SOURCE_ROTATION: (f"events/source-rotation-{timestamp}-{run_id}.json"),
            ArtifactKind.FEED_CACHE: (f"cache/feed-cache-{request.generation_id}.json"),
            ArtifactKind.STAGING: (f"staging/{request.generation_id}/{run_id}.json"),
            ArtifactKind.ABORTED: (f"aborted/{request.generation_id}-{run_id}.json"),
            ArtifactKind.PARTIAL: (f"partial/{request.generation_id}-{run_id}.json"),
        }
        if kind in deterministic:
            relative = deterministic[kind]
            if self._actual_target_exists(kind, relative):
                raise FileExistsError(f"deterministic artifact target already exists: {relative}")
            return relative, None

        bases = {
            ArtifactKind.AI_DAILY: (f"30-Daily/AI-News/AI-Daily-{date}"),
            ArtifactKind.IELTS_PREVIEW: (f"30-Daily/IELTS-Preview/IELTS-Preview-{date}"),
            ArtifactKind.WEEKLY: f"30-Daily/Weekly/AI-Weekly-{date}",
        }
        base = bases[kind]
        candidate = f"{base}.md"
        reserved_paths = self._reserved_paths()
        target = self._vault_policy.resolve_target(candidate)
        normalized = os.path.normcase(candidate)
        if target.exists() or normalized in reserved_paths:
            raise FileExistsError(f"canonical artifact target already exists: {candidate}")
        return candidate, None

    def _actual_target_exists(self, kind: ArtifactKind, relative: str) -> bool:
        vault_kinds = {
            ArtifactKind.RAW_ARCHIVE,
        }
        if kind in vault_kinds:
            return self._vault_policy.resolve_target(relative).exists()
        return (self._store.runtime_root / Path(relative)).exists()

    def _reserved_paths(self) -> set[str]:
        paths: set[str] = set()
        for path in self._reservations_root.glob("*.json"):
            reservation = self._read_reservation(path)
            paths.add(os.path.normcase(reservation.relative_path))
        return paths

    @staticmethod
    def _read_reservation(path: Path) -> ArtifactNameReservation:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if payload.get("schema_version") != 1:
                raise ValueError("unsupported schema")
            return ArtifactNameReservation.model_validate(payload["reservation"])
        except (OSError, KeyError, ValueError) as exc:
            raise RuntimeError(f"invalid artifact name reservation: {path}") from exc


# ── Local File System ─────────────────────────────────────────────────────────


def write_to_obsidian_disk(vault_path: str, filepath: str, content: str) -> bool:
    """Fail closed: only the qualified native broker may publish to the Vault."""
    del vault_path, content
    log.error(f"[disk-write-blocked] Native broker is required: {filepath}")
    return False


def append_to_obsidian_disk(vault_path: str, filepath: str, content: str) -> bool:
    """Fail closed: existing Vault notes are immutable to the workflow."""
    del vault_path, content
    log.error(f"[append-blocked] Existing Vault artifact is immutable: {filepath}")
    return False


# ── Obsidian REST API ─────────────────────────────────────────────────────────


def _build_api_url(api_base: str, endpoint: str) -> str:
    base = api_base.rstrip("/")
    normalized_endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    return f"{base}{normalized_endpoint}"


def _vault_endpoint(filepath: str) -> str:
    normalized_path = filepath.replace("\\", "/")
    encoded_path = quote(normalized_path, safe="/-_.~")
    return f"/vault/{encoded_path}"


@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, ObsidianAPIError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _api_get(api_base: str, endpoint: str, headers: dict) -> requests.Response:
    """Make an API GET request with retry logic."""
    r = SESSION.get(_build_api_url(api_base, endpoint), headers=headers, timeout=5)
    r.raise_for_status()
    return r


@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, ObsidianAPIError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _api_put(api_base: str, endpoint: str, headers: dict, data: bytes) -> requests.Response:
    """Fail closed: REST mutation is outside the v7.3 publication boundary."""
    del api_base, endpoint, headers, data
    raise ObsidianAPIError("Obsidian REST PUT is disabled; native broker required")


@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, ObsidianAPIError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _api_post(api_base: str, endpoint: str, headers: dict, data: bytes) -> requests.Response:
    """Fail closed: REST append is outside the v7.3 publication boundary."""
    del api_base, endpoint, headers, data
    raise ObsidianAPIError("Obsidian REST POST is disabled; native broker required")


def check_api_connection(api_base: str, headers: dict) -> bool:
    """Check if the Obsidian Local REST API is reachable."""
    try:
        r = _api_get(api_base, "/", headers)
        return r.status_code == 200
    except Exception as e:
        log.debug(f"API Connection Check Failed: {e}")
        return False


def note_exists_api(api_base: str, filepath: str, headers: dict) -> bool:
    """Check if a note exists via API."""
    try:
        endpoint = _vault_endpoint(filepath)
        r = SESSION.get(_build_api_url(api_base, endpoint), headers=headers, timeout=5)
        if r.status_code in (401, 403, 500):
            raise ObsidianAPIError(f"HTTP {r.status_code}: {r.text}")
        if r.status_code == 404:
            return False
        return r.status_code == 200
    except requests.exceptions.RequestException as exc:
        raise ObsidianAPIError(f"Cannot connect to Obsidian Local REST API: {exc}") from exc


def write_via_api(
    api_base: str, filepath: str, content: str, headers: dict, overwrite: bool = False
) -> bool:
    """Fail closed: disk CREATE_NEW is the only production writer."""
    del api_base, content, headers, overwrite
    log.error(f"[api-write-blocked] REST write is disabled: {filepath}")
    return False


def append_via_api(api_base: str, filepath: str, content: str, headers: dict) -> bool:
    """Fail closed: existing Vault notes are immutable to the workflow."""
    del api_base, content, headers
    log.error(f"[api-append-blocked] Native broker is required: {filepath}")
    return False
