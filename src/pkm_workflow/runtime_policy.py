"""Sealed v7.4 workflow entry and read-only activation selection.

This module deliberately contains no fetch, model, Scheduler, or Vault code.
It validates the control-plane contract before later tasks attach business
stages to the same ``execute_workflow`` seam.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import msvcrt
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ACTIVATION_NAME_PATTERN = re.compile(
    r"^(?P<generation>[0-9]{20})-(?P<activation_id>[0-9a-f-]{36})\.json$"
)
ACTIVATION_SCHEMA = "pkm.workflow.activation-generation.v1"
MAX_CONTROL_FILE_BYTES = 1024 * 1024
REPARSE_POINT_ATTRIBUTE = 0x400
DEFAULT_USER_CONTEXT_RAW_HASH = (
    "sha256:280c4d64166424cd04e88442a72f96d90dc9c0332aaefad764e1ec8f92029e50"
)
DEFAULT_USER_CONTEXT_CANONICAL_HASH = (
    "sha256:ce307d029565fe20659ec9e43561d949ac1ec7d7ec67720bad25c7ad73460e3f"
)
ACTIVATION_FIELDS = frozenset(
    {
        "schema",
        "generation",
        "activation_id",
        "prev_activation_hash",
        "approved_workflows",
        "release_manifest_hash",
        "config_hash",
        "source_policy_hashes",
        "prompt_hashes",
        "schema_hashes",
        "models",
        "task_bundle_hash",
        "user_context_raw_hash",
        "user_context_canonical_hash",
        "shadow_evidence_hashes",
        "red_team_report_hashes",
        "approved_at",
        "challenge_id",
    }
)


class Workflow(str, Enum):
    AI = "ai"
    IELTS = "ielts"
    WEEKLY = "weekly"


class Mode(str, Enum):
    DOCTOR = "doctor"
    DRY_RUN = "dry-run"
    SHADOW = "shadow"
    PRODUCTION = "production"


@dataclass(frozen=True)
class RunRequest:
    workflow: Workflow
    mode: Mode
    due_only: bool = False


@dataclass(frozen=True)
class RuntimeEnvironment:
    durable_root: Path
    user_context_path: Path = Path(
        r"D:\personal\obsidian_workflow\UserContextContract-v7.3.json"
    )
    expected_user_context_raw_hash: str = DEFAULT_USER_CONTEXT_RAW_HASH
    expected_user_context_canonical_hash: str = DEFAULT_USER_CONTEXT_CANONICAL_HASH

    @classmethod
    def production(cls) -> RuntimeEnvironment:
        return cls(Path(r"D:\personal\obsidian_workflow-runtime\durable"))


@dataclass(frozen=True)
class ActivationSelection:
    generation: int
    activation_id: str
    activation_hash: str
    approved_workflows: tuple[Workflow, ...]
    release_manifest_hash: str
    release_manifest_path: Path
    config_hash: str
    config_path: Path


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    payload: dict[str, object]

    def __post_init__(self) -> None:
        if self.exit_code not in {0, 2, 3, 4, 5, 8, 10}:
            raise ValueError("execution result uses an unknown domain exit code")


class ActivationBlocked(RuntimeError):
    """A fail-closed activation selection result with one stable reason code."""


def _blocked(code: str) -> ActivationBlocked:
    return ActivationBlocked(code)


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _canonical_semantic_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _blocked("ACTIVATION_SCHEMA_INVALID")
        result[key] = value
    return result


def _parse_json_object(data: bytes, *, exact_canonical: bool) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                _blocked("ACTIVATION_SCHEMA_INVALID")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ActivationBlocked) as error:
        if isinstance(error, ActivationBlocked):
            raise
        raise _blocked("ACTIVATION_SCHEMA_INVALID") from error
    if type(payload) is not dict or (exact_canonical and data != _canonical_bytes(payload)):
        raise _blocked("ACTIVATION_SCHEMA_INVALID")
    return payload


def _parse_exact_json(data: bytes) -> dict[str, Any]:
    return _parse_json_object(data, exact_canonical=True)


def _is_reparse(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & REPARSE_POINT_ATTRIBUTE)


def _reject_reparse_chain(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    if not absolute.is_absolute() or not parts:
        raise _blocked("CONTROL_PATH_INVALID")
    cursor = Path(parts[0])
    for part in parts[1:]:
        cursor /= part
        if _is_reparse(cursor):
            raise _blocked("CONTROL_PATH_REPARSE_FORBIDDEN")


def _final_path_from_fd(file_descriptor: int) -> Path:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_final_path = kernel32.GetFinalPathNameByHandleW
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_final_path(
        ctypes.c_void_p(msvcrt.get_osfhandle(file_descriptor)),
        buffer,
        len(buffer),
        0,
    )
    if length == 0 or length >= len(buffer):
        raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW failed")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(os.path.abspath(value))


def _is_within(path: Path, authority_root: Path) -> bool:
    try:
        return os.path.commonpath(
            (os.path.normcase(path), os.path.normcase(authority_root))
        ) == os.path.normcase(authority_root)
    except ValueError:
        return False


def _read_regular_file(
    path: Path,
    *,
    authority_root: Path,
    missing_code: str,
    invalid_code: str,
) -> bytes:
    file_descriptor = -1
    try:
        _reject_reparse_chain(authority_root)
        _reject_reparse_chain(path)
        file_descriptor = os.open(
            path,
            os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT,
        )
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise _blocked(invalid_code)
        final_path = _final_path_from_fd(file_descriptor)
        if not _is_within(final_path, Path(os.path.abspath(authority_root))):
            raise _blocked(invalid_code)
        size = before.st_size
        if size <= 0 or size > MAX_CONTROL_FILE_BYTES:
            raise _blocked(invalid_code)
        chunks = []
        remaining = MAX_CONTROL_FILE_BYTES + 1
        while remaining:
            chunk = os.read(file_descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(file_descriptor)
    except FileNotFoundError as error:
        raise _blocked(missing_code) from error
    except ActivationBlocked as error:
        raise _blocked(invalid_code) from error
    except OSError as error:
        raise _blocked(invalid_code) from error
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
    if (
        len(data) != size
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
    ):
        raise _blocked(invalid_code)
    return data


def _require_hash(value: object) -> str:
    if type(value) is not str or HASH_PATTERN.fullmatch(value) is None:
        raise _blocked("ACTIVATION_SCHEMA_INVALID")
    return value


def _require_uuid(value: object) -> str:
    if type(value) is not str:
        raise _blocked("ACTIVATION_SCHEMA_INVALID")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise _blocked("ACTIVATION_SCHEMA_INVALID") from error
    if str(parsed) != value:
        raise _blocked("ACTIVATION_SCHEMA_INVALID")
    return value


def _require_hash_map(value: object, workflows: tuple[str, ...]) -> dict[str, str]:
    if type(value) is not dict or tuple(sorted(value)) != workflows:
        raise _blocked("ACTIVATION_SCHEMA_INVALID")
    return {key: _require_hash(item) for key, item in value.items()}


def _require_hash_list(value: object, *, minimum: int) -> tuple[str, ...]:
    if (
        type(value) is not list
        or len(value) < minimum
        or value != sorted(set(value))
    ):
        raise _blocked("ACTIVATION_SCHEMA_INVALID")
    return tuple(_require_hash(item) for item in value)


def _require_hash_lists_map(
    value: object,
    workflows: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    if type(value) is not dict or tuple(sorted(value)) != workflows:
        raise _blocked("ACTIVATION_SCHEMA_INVALID")
    return {key: _require_hash_list(items, minimum=1) for key, items in value.items()}


def _validate_activation(
    payload: dict[str, Any],
    *,
    expected_generation: int,
    expected_activation_id: str,
    expected_prev: str | None,
) -> tuple[Workflow, ...]:
    if frozenset(payload) != ACTIVATION_FIELDS or payload.get("schema") != ACTIVATION_SCHEMA:
        raise _blocked("ACTIVATION_SCHEMA_INVALID")
    if type(payload["generation"]) is not int or payload["generation"] != expected_generation:
        raise _blocked("ACTIVATION_SCHEMA_INVALID")
    if _require_uuid(payload["activation_id"]) != expected_activation_id:
        raise _blocked("ACTIVATION_SCHEMA_INVALID")
    previous = payload["prev_activation_hash"]
    if previous is not None:
        previous = _require_hash(previous)
    if previous != expected_prev:
        raise _blocked("ACTIVATION_CHAIN_INVALID")
    raw_workflows = payload["approved_workflows"]
    if (
        type(raw_workflows) is not list
        or not raw_workflows
        or any(type(item) is not str for item in raw_workflows)
        or raw_workflows != sorted(set(raw_workflows))
    ):
        raise _blocked("ACTIVATION_SCHEMA_INVALID")
    try:
        workflows = tuple(Workflow(item) for item in raw_workflows)
    except ValueError as error:
        raise _blocked("ACTIVATION_SCHEMA_INVALID") from error
    workflow_names = tuple(item.value for item in workflows)
    for field in ("source_policy_hashes", "prompt_hashes", "schema_hashes"):
        _require_hash_map(payload[field], workflow_names)
    models = payload["models"]
    if type(models) is not dict or tuple(sorted(models)) != workflow_names:
        raise _blocked("ACTIVATION_SCHEMA_INVALID")
    for value in models.values():
        if type(value) is not dict or frozenset(value) != {"generator", "reviewer"}:
            raise _blocked("ACTIVATION_SCHEMA_INVALID")
        if value != {"generator": "gpt-5.6-terra", "reviewer": "gpt-5.6-sol"}:
            raise _blocked("ACTIVATION_SCHEMA_INVALID")
    for field in ("release_manifest_hash", "config_hash", "task_bundle_hash"):
        _require_hash(payload[field])
    _require_hash(payload["user_context_raw_hash"])
    _require_hash(payload["user_context_canonical_hash"])
    _require_hash_lists_map(payload["shadow_evidence_hashes"], workflow_names)
    _require_hash_list(payload["red_team_report_hashes"], minimum=2)
    if type(payload["approved_at"]) is not str:
        raise _blocked("ACTIVATION_SCHEMA_INVALID")
    try:
        approved_at = datetime.fromisoformat(payload["approved_at"])
    except ValueError as error:
        raise _blocked("ACTIVATION_SCHEMA_INVALID") from error
    if approved_at.tzinfo is None or approved_at.utcoffset() is None:
        raise _blocked("ACTIVATION_SCHEMA_INVALID")
    _require_uuid(payload["challenge_id"])
    return workflows


def _read_hash_bound(
    path: Path,
    *,
    authority_root: Path,
    expected_hash: str,
    code: str,
) -> bytes:
    data = _read_regular_file(
        path,
        authority_root=authority_root,
        missing_code=code,
        invalid_code=code,
    )
    if _sha256(data) != expected_hash:
        raise _blocked(code)
    return data


def _require_exact_fields(
    payload: dict[str, Any],
    fields: frozenset[str],
    schema: str,
    code: str,
) -> None:
    if frozenset(payload) != fields or payload.get("schema") != schema:
        raise _blocked(code)


def _validate_user_context(payload: dict[str, Any]) -> None:
    fields = frozenset(
        {
            "schema_version",
            "contract_id",
            "projects",
            "technical_environment",
            "priorities",
            "style",
            "prohibited_inferences",
            "personalization_rule",
            "vault_inference_allowed",
        }
    )
    if frozenset(payload) != fields:
        raise _blocked("ACTIVATION_USER_CONTEXT_MISMATCH")
    for field in ("schema_version", "contract_id", "personalization_rule"):
        if type(payload[field]) is not str or not payload[field]:
            raise _blocked("ACTIVATION_USER_CONTEXT_MISMATCH")
    for field in ("projects", "technical_environment", "prohibited_inferences"):
        values = payload[field]
        if type(values) is not list or not values or any(type(item) is not str for item in values):
            raise _blocked("ACTIVATION_USER_CONTEXT_MISMATCH")
    priorities = payload["priorities"]
    if type(priorities) is not list or not priorities:
        raise _blocked("ACTIVATION_USER_CONTEXT_MISMATCH")
    for priority in priorities:
        if (
            type(priority) is not dict
            or frozenset(priority) != {"rank", "topics"}
            or type(priority["rank"]) is not int
            or priority["rank"] <= 0
            or type(priority["topics"]) is not list
            or not priority["topics"]
            or any(type(item) is not str for item in priority["topics"])
        ):
            raise _blocked("ACTIVATION_USER_CONTEXT_MISMATCH")
    style = payload["style"]
    if (
        type(style) is not dict
        or frozenset(style)
        != {
            "language",
            "orientation",
            "evidence_first",
            "precision_over_recall",
            "abstention_allowed",
        }
        or type(style["language"]) is not str
        or type(style["orientation"]) is not str
        or any(
            type(style[field]) is not bool
            for field in ("evidence_first", "precision_over_recall", "abstention_allowed")
        )
        or payload["vault_inference_allowed"] is not False
    ):
        raise _blocked("ACTIVATION_USER_CONTEXT_MISMATCH")


def _parse_aware_time(value: object, code: str) -> datetime:
    if type(value) is not str:
        raise _blocked(code)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise _blocked(code) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _blocked(code)
    return parsed


def _validate_approval_artifacts(
    root: Path,
    activation: dict[str, Any],
    activation_hash: str,
    workflows: tuple[str, ...],
) -> None:
    code = "ACTIVATION_APPROVAL_MISMATCH"
    challenge_id = activation["challenge_id"]
    challenge_path = root / "activation-challenges" / f"{challenge_id}.json"
    challenge_bytes = _read_regular_file(
        challenge_path,
        authority_root=root,
        missing_code=code,
        invalid_code=code,
    )
    challenge_hash = _sha256(challenge_bytes)
    try:
        challenge = _parse_exact_json(challenge_bytes)
        _require_exact_fields(
            challenge,
            frozenset(
                {
                    "schema",
                    "challenge_id",
                    "activation_hash",
                    "approved_workflows",
                    "expires_at",
                }
            ),
            "pkm.workflow.activation-challenge.v1",
            code,
        )
    except ActivationBlocked as error:
        raise _blocked(code) from error
    if (
        challenge["challenge_id"] != challenge_id
        or challenge["activation_hash"] != activation_hash
        or challenge["approved_workflows"] != list(workflows)
    ):
        raise _blocked(code)
    expires_at = _parse_aware_time(challenge["expires_at"], code)
    consume_path = root / "activation-consumed" / f"{challenge_id}.json"
    consume_bytes = _read_regular_file(
        consume_path,
        authority_root=root,
        missing_code=code,
        invalid_code=code,
    )
    try:
        consume = _parse_exact_json(consume_bytes)
        _require_exact_fields(
            consume,
            frozenset(
                {
                    "schema",
                    "challenge_id",
                    "challenge_hash",
                    "activation_hash",
                    "consumed_at",
                }
            ),
            "pkm.workflow.activation-consume.v1",
            code,
        )
    except ActivationBlocked as error:
        raise _blocked(code) from error
    consumed_at = _parse_aware_time(consume["consumed_at"], code)
    if (
        consume["challenge_id"] != challenge_id
        or consume["challenge_hash"] != challenge_hash
        or consume["activation_hash"] != activation_hash
        or consume["consumed_at"] != activation["approved_at"]
        or consumed_at > expires_at
    ):
        raise _blocked(code)


class ActivationReader:
    """Select the only valid activation chain under one fixed durable root."""

    def __init__(
        self,
        durable_root: Path,
        user_context_path: Path,
        expected_user_context_raw_hash: str,
        expected_user_context_canonical_hash: str,
    ) -> None:
        self._root = durable_root
        self._user_context_path = user_context_path
        self._expected_user_context_raw_hash = _require_hash(
            expected_user_context_raw_hash
        )
        self._expected_user_context_canonical_hash = _require_hash(
            expected_user_context_canonical_hash
        )

    def select(self, workflow: Workflow) -> ActivationSelection:
        activation_root = self._root / "activation"
        try:
            _reject_reparse_chain(activation_root)
            entries = list(activation_root.iterdir())
        except ActivationBlocked as error:
            raise _blocked("ACTIVATION_ROOT_INVALID") from error
        except FileNotFoundError as error:
            raise _blocked("ACTIVATION_MISSING") from error
        except OSError as error:
            raise _blocked("ACTIVATION_ROOT_INVALID") from error
        if not entries:
            raise _blocked("ACTIVATION_MISSING")
        parsed_entries: list[tuple[int, str, Path]] = []
        seen_generations: set[int] = set()
        for path in entries:
            match = ACTIVATION_NAME_PATTERN.fullmatch(path.name)
            if match is None:
                raise _blocked("ACTIVATION_SCHEMA_INVALID")
            generation = int(match.group("generation"))
            if generation in seen_generations:
                raise _blocked("ACTIVATION_FORK")
            seen_generations.add(generation)
            parsed_entries.append((generation, match.group("activation_id"), path))
        parsed_entries.sort(key=lambda item: item[0])
        if [item[0] for item in parsed_entries] != list(range(1, len(parsed_entries) + 1)):
            raise _blocked("ACTIVATION_CHAIN_INVALID")
        previous_hash: str | None = None
        current_payload: dict[str, Any] | None = None
        current_hash = ""
        current_workflows: tuple[Workflow, ...] = ()
        for generation, activation_id, path in parsed_entries:
            data = _read_regular_file(
                path,
                authority_root=activation_root,
                missing_code="ACTIVATION_MISSING",
                invalid_code="ACTIVATION_SCHEMA_INVALID",
            )
            payload = _parse_exact_json(data)
            current_workflows = _validate_activation(
                payload,
                expected_generation=generation,
                expected_activation_id=activation_id,
                expected_prev=previous_hash,
            )
            current_payload = payload
            current_hash = _sha256(data)
            _validate_approval_artifacts(
                self._root,
                payload,
                current_hash,
                tuple(item.value for item in current_workflows),
            )
            previous_hash = current_hash
        assert current_payload is not None
        if workflow not in current_workflows:
            raise _blocked("ACTIVATION_SCOPE_MISMATCH")
        release_hash = _require_hash(current_payload["release_manifest_hash"])
        config_hash = _require_hash(current_payload["config_hash"])
        release_path = (
            self._root
            / "releases"
            / f"sha256-{release_hash.removeprefix('sha256:')}"
            / "manifest.json"
        )
        config_path = self._root / "config" / (
            f"sha256-{config_hash.removeprefix('sha256:')}.json"
        )
        _read_hash_bound(
            release_path,
            authority_root=self._root,
            expected_hash=release_hash,
            code="ACTIVATION_RELEASE_HASH_MISMATCH",
        )
        _read_hash_bound(
            config_path,
            authority_root=self._root,
            expected_hash=config_hash,
            code="ACTIVATION_CONFIG_HASH_MISMATCH",
        )
        workflow_names = tuple(item.value for item in current_workflows)
        task_bundle_hash = current_payload["task_bundle_hash"]
        task_bundle_path = (
            self._root
            / "task-bundles"
            / f"sha256-{task_bundle_hash.removeprefix('sha256:')}"
            / "manifest.json"
        )
        _read_hash_bound(
            task_bundle_path,
            authority_root=self._root,
            expected_hash=task_bundle_hash,
            code="ACTIVATION_TASK_BUNDLE_MISMATCH",
        )
        for workflow_name in workflow_names:
            source_policy_hash = current_payload["source_policy_hashes"][workflow_name]
            _read_hash_bound(
                self._root
                / "source-policies"
                / workflow_name
                / f"sha256-{source_policy_hash.removeprefix('sha256:')}.json",
                authority_root=self._root,
                expected_hash=source_policy_hash,
                code="ACTIVATION_SOURCE_POLICY_MISMATCH",
            )
            prompt_hash = current_payload["prompt_hashes"][workflow_name]
            _read_hash_bound(
                self._root
                / "prompts"
                / workflow_name
                / f"sha256-{prompt_hash.removeprefix('sha256:')}.txt",
                authority_root=self._root,
                expected_hash=prompt_hash,
                code="ACTIVATION_PROMPT_MISMATCH",
            )
            schema_hash = current_payload["schema_hashes"][workflow_name]
            _read_hash_bound(
                self._root
                / "schemas"
                / workflow_name
                / f"sha256-{schema_hash.removeprefix('sha256:')}.json",
                authority_root=self._root,
                expected_hash=schema_hash,
                code="ACTIVATION_MODEL_SCHEMA_MISMATCH",
            )
            for evidence_hash in current_payload["shadow_evidence_hashes"][workflow_name]:
                _read_hash_bound(
                    self._root
                    / "shadow-evidence"
                    / workflow_name
                    / f"sha256-{evidence_hash.removeprefix('sha256:')}.json",
                    authority_root=self._root,
                    expected_hash=evidence_hash,
                    code="ACTIVATION_SHADOW_EVIDENCE_MISMATCH",
                )
        for report_hash in current_payload["red_team_report_hashes"]:
            _read_hash_bound(
                self._root
                / "red-team-reports"
                / f"sha256-{report_hash.removeprefix('sha256:')}.json",
                authority_root=self._root,
                expected_hash=report_hash,
                code="ACTIVATION_RED_TEAM_EVIDENCE_MISMATCH",
            )
        user_context_bytes = _read_regular_file(
            self._user_context_path,
            authority_root=self._user_context_path.parent,
            missing_code="ACTIVATION_USER_CONTEXT_MISMATCH",
            invalid_code="ACTIVATION_USER_CONTEXT_MISMATCH",
        )
        if (
            current_payload["user_context_raw_hash"]
            != self._expected_user_context_raw_hash
            or current_payload["user_context_canonical_hash"]
            != self._expected_user_context_canonical_hash
            or _sha256(user_context_bytes) != self._expected_user_context_raw_hash
        ):
            raise _blocked("ACTIVATION_USER_CONTEXT_MISMATCH")
        try:
            user_context = _parse_json_object(user_context_bytes, exact_canonical=False)
        except ActivationBlocked as error:
            raise _blocked("ACTIVATION_USER_CONTEXT_MISMATCH") from error
        _validate_user_context(user_context)
        if _sha256(_canonical_semantic_bytes(user_context)) != self._expected_user_context_canonical_hash:
            raise _blocked("ACTIVATION_USER_CONTEXT_MISMATCH")
        return ActivationSelection(
            generation=len(parsed_entries),
            activation_id=current_payload["activation_id"],
            activation_hash=current_hash,
            approved_workflows=current_workflows,
            release_manifest_hash=release_hash,
            release_manifest_path=release_path,
            config_hash=config_hash,
            config_path=config_path,
        )


def execute_workflow(
    request: RunRequest,
    environment: RuntimeEnvironment,
) -> ExecutionResult:
    common: dict[str, object] = {
        "schema_version": "7.4",
        "engine": "v7.4-sealed-entry",
        "workflow": request.workflow.value,
        "mode": request.mode.value,
        "due_only": request.due_only,
        "status": "BLOCKED",
        "vault_write": False,
        "production_activation": False,
        "activation_structure_validated": False,
        "activation_validated": False,
    }
    if request.due_only and request.mode in {Mode.DOCTOR, Mode.DRY_RUN}:
        return ExecutionResult(2, common | {"reason": "DUE_ONLY_MODE_INVALID"})
    if request.mode is Mode.DOCTOR:
        return ExecutionResult(
            3,
            common
            | {
                "reason": "MVP_QUALIFICATION_INCOMPLETE",
                "blockers": [
                    "SHADOW_ENGINE_NOT_IMPLEMENTED",
                    "TASK_BUNDLE_NOT_INSTALLED",
                    "USER_APPROVAL_REQUIRED",
                ],
            },
        )
    if request.mode in {Mode.DRY_RUN, Mode.SHADOW}:
        return ExecutionResult(3, common | {"reason": "SHADOW_ENGINE_NOT_IMPLEMENTED"})
    try:
        activation = ActivationReader(
            environment.durable_root,
            environment.user_context_path,
            environment.expected_user_context_raw_hash,
            environment.expected_user_context_canonical_hash,
        ).select(request.workflow)
    except ActivationBlocked as error:
        return ExecutionResult(3, common | {"reason": str(error)})
    return ExecutionResult(
        3,
        common
        | {
            "reason": "ACTIVATION_EVIDENCE_SEMANTICS_NOT_IMPLEMENTED",
            "activation_structure_validated": True,
            "activation_validated": False,
            "activation_generation": activation.generation,
            "activation_hash": activation.activation_hash,
            "release_manifest_hash": activation.release_manifest_hash,
            "config_hash": activation.config_hash,
        },
    )
