"""Read-only credential resolution and append-only credential bindings."""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import uuid
from collections.abc import Callable, Mapping
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path


class CredentialLogicalName(str, Enum):
    OPENAI_LLM = "openai_llm"
    OBSIDIAN_REST_READBACK = "obsidian_rest_readback"


class CredentialSource(str, Enum):
    WINDOWS_CREDENTIAL_MANAGER = "windows_credential_manager"
    CONTROLLED_ENVIRONMENT = "controlled_environment"
    LEGACY_DOTENV = "legacy_dotenv"
    MISSING = "missing"


CONTROLLED_ENV_NAMES = {
    CredentialLogicalName.OPENAI_LLM: "PKM_OPENAI_API_KEY",
    CredentialLogicalName.OBSIDIAN_REST_READBACK: ("PKM_OBSIDIAN_REST_API_KEY"),
}
LEGACY_ENV_NAMES = {
    CredentialLogicalName.OPENAI_LLM: "OPENAI_API_KEY",
    CredentialLogicalName.OBSIDIAN_REST_READBACK: "OBSIDIAN_API_KEY",
}


@dataclass(frozen=True)
class CredentialRequirements:
    required: tuple[CredentialLogicalName, ...]

    @classmethod
    def for_workflow(
        cls,
        *,
        write_mode: str,
        llm_enabled: bool,
    ) -> CredentialRequirements:
        if write_mode == "api":
            raise ValueError("API write mode is forbidden; disk is the only writer")
        if write_mode not in {"disk", "both"}:
            raise ValueError("write_mode must be disk or both")
        required: list[CredentialLogicalName] = []
        if llm_enabled:
            required.append(CredentialLogicalName.OPENAI_LLM)
        if write_mode == "both":
            required.append(CredentialLogicalName.OBSIDIAN_REST_READBACK)
        return cls(required=tuple(required))


@dataclass(frozen=True, repr=False)
class ResolvedCredential:
    logical_name: CredentialLogicalName
    source: CredentialSource
    configured: bool
    target_hash: str
    warnings: tuple[str, ...]
    _secret: str | None

    def reveal_for_engine(self) -> str | None:
        return self._secret

    def __repr__(self) -> str:
        return (
            "ResolvedCredential("
            f"logical_name={self.logical_name.value!r}, "
            f"source={self.source.value!r}, "
            f"configured={self.configured!r}, "
            f"target_hash={self.target_hash!r}, "
            f"warnings={self.warnings!r}, secret=[REDACTED])"
        )


@dataclass(frozen=True)
class BindingChallenge:
    challenge_id: str
    logical_name: CredentialLogicalName
    target_name: str
    phrase: str
    expires_at: datetime


@dataclass(frozen=True)
class BindingReceipt:
    binding_id: str
    logical_name: CredentialLogicalName
    target_hash: str
    event_path: str


def _write_json_create_new(path: Path, payload: object) -> None:
    serialized = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())


class CredentialBindingService:
    """Local-interactive, challenge-bound, append-only binding service."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("credential binding root must be a directory")
        self.challenges_root = self.root / "challenges"
        self.bindings_root = self.root / "bindings"
        self.consumptions_root = self.root / "challenge-consumptions"
        for directory in (
            self.challenges_root,
            self.bindings_root,
            self.consumptions_root,
        ):
            directory.mkdir(exist_ok=True)

    def create_challenge(
        self,
        *,
        logical_name: CredentialLogicalName,
        target_name: str,
        lifetime_minutes: int = 10,
    ) -> BindingChallenge:
        if not target_name.strip():
            raise ValueError("target_name cannot be blank")
        if lifetime_minutes < 1:
            raise ValueError("challenge lifetime must be positive")
        challenge_id = f"credential-{uuid.uuid4()}"
        nonce = uuid.uuid4().hex[:8].upper()
        phrase = f"BIND {logical_name.value.upper()} {nonce}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=lifetime_minutes)
        challenge = BindingChallenge(
            challenge_id=challenge_id,
            logical_name=logical_name,
            target_name=target_name,
            phrase=phrase,
            expires_at=expires_at,
        )
        _write_json_create_new(
            self.challenges_root / f"challenge-{challenge_id}.json",
            {
                "schema_version": 1,
                "challenge_id": challenge_id,
                "logical_name": logical_name.value,
                "target_name": target_name,
                "phrase": phrase,
                "expires_at": expires_at.isoformat(),
            },
        )
        return challenge

    def bind_from_tty(
        self,
        *,
        challenge_id: str,
        typed_phrase: str,
        windows_identity: str,
        is_tty: bool,
        caller: str,
    ) -> BindingReceipt:
        if not is_tty:
            raise PermissionError("credential binding requires a local TTY")
        if caller != "local_cli":
            raise PermissionError("credential binding is available only to the local CLI")
        challenge_path = self.challenges_root / f"challenge-{challenge_id}.json"
        if not challenge_path.exists():
            raise PermissionError("credential challenge does not exist")
        consumption_path = self.consumptions_root / f"consumed-{challenge_id}.json"
        binding_path = self.bindings_root / f"binding-{challenge_id}.json"
        if consumption_path.exists() or binding_path.exists():
            raise PermissionError("credential challenge was already consumed")
        challenge = json.loads(challenge_path.read_text(encoding="utf-8-sig"))
        expires_at = datetime.fromisoformat(str(challenge["expires_at"]))
        if datetime.now(timezone.utc) > expires_at:
            raise PermissionError("credential challenge expired")
        if not hmac.compare_digest(
            typed_phrase,
            str(challenge["phrase"]),
        ):
            raise PermissionError("credential challenge phrase did not match")
        identity_hash = hashlib.sha256(windows_identity.encode("utf-8")).hexdigest()
        target_name = str(challenge["target_name"])
        target_hash = hashlib.sha256(target_name.encode("utf-8")).hexdigest()
        binding_id = f"binding-{uuid.uuid4()}"
        record = {
            "schema_version": 1,
            "binding_id": binding_id,
            "challenge_id": challenge_id,
            "logical_name": str(challenge["logical_name"]),
            "provider": CredentialSource.WINDOWS_CREDENTIAL_MANAGER.value,
            "target_name": target_name,
            "target_hash": target_hash,
            "windows_identity_hash": identity_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_create_new(binding_path, record)
        _write_json_create_new(
            consumption_path,
            {
                "schema_version": 1,
                "challenge_id": challenge_id,
                "binding_id": binding_id,
                "consumed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return BindingReceipt(
            binding_id=binding_id,
            logical_name=CredentialLogicalName(record["logical_name"]),
            target_hash=target_hash,
            event_path=str(binding_path),
        )


class CredentialProvider:
    """Resolve secrets internally while exposing only redacted status."""

    def __init__(
        self,
        *,
        binding_root: Path,
        environ: Mapping[str, str],
        credential_reader: Callable[[str], str | None],
        mode: str,
        legacy_env_path: Path | None = None,
    ) -> None:
        self.binding_root = Path(binding_root).resolve(strict=True)
        self.environ = dict(environ)
        self.credential_reader = credential_reader
        self.mode = mode
        self.legacy_env_path = (
            Path(legacy_env_path).resolve(strict=True) if legacy_env_path is not None else None
        )
        if mode not in {"shadow", "observed", "unattended"}:
            raise ValueError("invalid workflow mode")

    def resolve(
        self,
        logical_name: CredentialLogicalName,
    ) -> ResolvedCredential:
        binding = self._latest_binding(logical_name)
        if binding is not None:
            target_name = str(binding["target_name"])
            secret = self.credential_reader(target_name)
            return ResolvedCredential(
                logical_name=logical_name,
                source=CredentialSource.WINDOWS_CREDENTIAL_MANAGER,
                configured=bool(secret),
                target_hash=str(binding["target_hash"]),
                warnings=(),
                _secret=secret,
            )

        controlled_name = CONTROLLED_ENV_NAMES[logical_name]
        controlled_secret = self.environ.get(controlled_name, "").strip()
        if controlled_secret:
            return ResolvedCredential(
                logical_name=logical_name,
                source=CredentialSource.CONTROLLED_ENVIRONMENT,
                configured=True,
                target_hash=hashlib.sha256(controlled_name.encode("utf-8")).hexdigest(),
                warnings=(),
                _secret=controlled_secret,
            )

        if self.mode == "shadow":
            legacy_secret = (
                self._legacy_values()
                .get(
                    LEGACY_ENV_NAMES[logical_name],
                    "",
                )
                .strip()
            )
            if legacy_secret:
                legacy_name = LEGACY_ENV_NAMES[logical_name]
                return ResolvedCredential(
                    logical_name=logical_name,
                    source=CredentialSource.LEGACY_DOTENV,
                    configured=True,
                    target_hash=hashlib.sha256(legacy_name.encode("utf-8")).hexdigest(),
                    warnings=("LEGACY_SECRET_SOURCE",),
                    _secret=legacy_secret,
                )
        return ResolvedCredential(
            logical_name=logical_name,
            source=CredentialSource.MISSING,
            configured=False,
            target_hash="",
            warnings=(),
            _secret=None,
        )

    def status(
        self,
        logical_name: CredentialLogicalName,
    ) -> dict[str, object]:
        resolved = self.resolve(logical_name)
        return {
            "logical_name": logical_name.value,
            "provider": resolved.source.value,
            "configured": resolved.configured,
            "target_hash": resolved.target_hash,
            "warnings": list(resolved.warnings),
        }

    def _latest_binding(
        self,
        logical_name: CredentialLogicalName,
    ) -> dict[str, object] | None:
        bindings_root = self.binding_root / "bindings"
        if not bindings_root.exists():
            return None
        matches: list[dict[str, object]] = []
        for path in bindings_root.glob("binding-*.json"):
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if payload.get("logical_name") == logical_name.value:
                matches.append(payload)
        if not matches:
            return None
        return max(matches, key=lambda payload: str(payload["created_at"]))

    def _legacy_values(self) -> dict[str, str]:
        if self.legacy_env_path is None:
            return {}
        values: dict[str, str] = {}
        for line in self.legacy_env_path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key in LEGACY_ENV_NAMES.values():
                values[key] = value
        return values


def read_windows_generic_credential(target_name: str) -> str | None:
    """Read a Generic Credential through stdlib ctypes on Windows."""

    if os.name != "nt":
        return None

    class Credential(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWrittenLow", wintypes.DWORD),
            ("LastWrittenHigh", wintypes.DWORD),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    credential_pointer = ctypes.POINTER(Credential)()
    advapi32 = ctypes.windll.advapi32
    if not advapi32.CredReadW(
        target_name,
        1,
        0,
        ctypes.byref(credential_pointer),
    ):
        return None
    try:
        credential = credential_pointer.contents
        raw = ctypes.string_at(
            credential.CredentialBlob,
            credential.CredentialBlobSize,
        )
        try:
            return raw.decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError:
            return raw.decode("utf-8")
    finally:
        advapi32.CredFree(credential_pointer)
