"""One-shot, fail-closed CONNECT proxy contract for T1.3b shadow qualification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import queue
import re
import select
import socket
import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, NoReturn, Protocol

SCHEMA_VERSION = "t13b-loopback-connect-v1"
AUTHORIZATION_MODE = "BASIC_USERINFO_ENV_ONLY"
PROXY_USERNAME = "pkm-t13b"
LISTENER_MODE = "BROKER_PREBOUND_INHERITED"
CODEX_0147_PROXY_AUTH_TRACE_REQUIRED = "CODEX_0147_PROXY_AUTH_TRACE_REQUIRED"
FROZEN_CONNECT_AUTHORITIES = frozenset(
    {
        "api.openai.com:443",
        "chatgpt.com:443",
    }
)
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_HMAC_SHA256_PATTERN = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_TOKEN_PATTERN = re.compile(rb"^[A-Za-z0-9._~-]{32,128}$")
_HEADER_NAME_PATTERN = re.compile(rb"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_TOKEN_HASH_DOMAIN = b"PKM-CODEX-T13B-PROXY-BASIC-TOKEN-V1\0"
_READINESS_HMAC_DOMAIN = b"PKM-CODEX-T13B-PROXY-READINESS-HMAC-V1\0"
_MAX_PROCESS_ID = (1 << 32) - 1
_MAX_LISTENER_HANDLE = (1 << 64) - 1
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "bind_host",
        "bind_port",
        "connect_allowlist",
        "authorization_mode",
        "authorization_username_sha256",
        "listener_mode",
        "token_sha256",
        "max_header_bytes",
        "max_client_hello_bytes",
        "max_tunnel_bytes",
        "deadline_milliseconds",
        "max_connections",
        "manifest_hash",
    }
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _token_sha256(value: bytes | bytearray) -> str:
    digest = hashlib.sha256()
    digest.update(_TOKEN_HASH_DOMAIN)
    digest.update(value)
    return "sha256:" + digest.hexdigest()


def _readiness_hmac(authorization_token: bytes | bytearray, payload: dict[str, object]) -> str:
    proof = hmac.new(authorization_token, digestmod=hashlib.sha256)
    proof.update(_READINESS_HMAC_DOMAIN)
    proof.update(_canonical_json(payload))
    return "hmac-sha256:" + proof.hexdigest()


def _wipe(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0


def _close_socket(value: socket.socket | None) -> None:
    if value is None:
        return
    try:
        value.close()
    except OSError:
        pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("sealed JSON has duplicate fields")
        result[key] = value
    return result


@dataclass(frozen=True)
class ProxyManifest:
    """Hash-bound, canonical policy for exactly one loopback CONNECT."""

    schema_version: str
    bind_host: str
    bind_port: int
    connect_allowlist: tuple[str, ...]
    authorization_mode: str
    authorization_username_sha256: str
    listener_mode: str
    token_sha256: str
    max_header_bytes: int
    max_client_hello_bytes: int
    max_tunnel_bytes: int
    deadline_milliseconds: int
    max_connections: int
    manifest_hash: str

    @classmethod
    def create(
        cls,
        *,
        bind_port: int,
        connect_allowlist: tuple[str, ...],
        authorization_token: bytes,
        max_header_bytes: int = 8192,
        max_client_hello_bytes: int = 32768,
        max_tunnel_bytes: int = 1024 * 1024,
        deadline_milliseconds: int = 900_000,
    ) -> ProxyManifest:
        if (
            not isinstance(authorization_token, bytes)
            or _TOKEN_PATTERN.fullmatch(authorization_token) is None
        ):
            raise ValueError("proxy authorization token must be 32..128 URL-safe bytes")
        provisional = cls(
            schema_version=SCHEMA_VERSION,
            bind_host="127.0.0.1",
            bind_port=bind_port,
            connect_allowlist=connect_allowlist,
            authorization_mode=AUTHORIZATION_MODE,
            authorization_username_sha256=_sha256(PROXY_USERNAME.encode("ascii")),
            listener_mode=LISTENER_MODE,
            token_sha256=_token_sha256(authorization_token),
            max_header_bytes=max_header_bytes,
            max_client_hello_bytes=max_client_hello_bytes,
            max_tunnel_bytes=max_tunnel_bytes,
            deadline_milliseconds=deadline_milliseconds,
            max_connections=1,
            manifest_hash="",
        )
        manifest = replace(
            provisional,
            manifest_hash=_sha256(_canonical_json(provisional._payload())),
        )
        manifest.validate()
        return manifest

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> ProxyManifest:
        if not isinstance(raw, bytes):
            raise ValueError("proxy manifest must be UTF-8 bytes")
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("proxy manifest is not valid UTF-8 JSON") from error
        if not isinstance(value, dict) or set(value) != _MANIFEST_FIELDS:
            raise ValueError("proxy manifest fields are not exact")
        allowlist = value["connect_allowlist"]
        if not isinstance(allowlist, list):
            raise ValueError("proxy manifest allowlist must be an array")
        manifest = cls(
            schema_version=value["schema_version"],
            bind_host=value["bind_host"],
            bind_port=value["bind_port"],
            connect_allowlist=tuple(allowlist),
            authorization_mode=value["authorization_mode"],
            authorization_username_sha256=value["authorization_username_sha256"],
            listener_mode=value["listener_mode"],
            token_sha256=value["token_sha256"],
            max_header_bytes=value["max_header_bytes"],
            max_client_hello_bytes=value["max_client_hello_bytes"],
            max_tunnel_bytes=value["max_tunnel_bytes"],
            deadline_milliseconds=value["deadline_milliseconds"],
            max_connections=value["max_connections"],
            manifest_hash=value["manifest_hash"],
        )
        manifest.validate()
        if raw != manifest.to_json_bytes():
            raise ValueError("proxy manifest is not strict canonical JSON")
        return manifest

    def _payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "bind_host": self.bind_host,
            "bind_port": self.bind_port,
            "connect_allowlist": list(self.connect_allowlist),
            "authorization_mode": self.authorization_mode,
            "authorization_username_sha256": self.authorization_username_sha256,
            "listener_mode": self.listener_mode,
            "token_sha256": self.token_sha256,
            "max_header_bytes": self.max_header_bytes,
            "max_client_hello_bytes": self.max_client_hello_bytes,
            "max_tunnel_bytes": self.max_tunnel_bytes,
            "deadline_milliseconds": self.deadline_milliseconds,
            "max_connections": self.max_connections,
        }

    def to_json_bytes(self) -> bytes:
        self.validate()
        return _canonical_json({**self._payload(), "manifest_hash": self.manifest_hash})

    @property
    def bind_endpoint_sha256(self) -> str:
        return _sha256(f"{self.bind_host}:{self.bind_port}".encode("ascii"))

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.bind_host != "127.0.0.1":
            raise ValueError("proxy manifest identity is invalid")
        if type(self.bind_port) is not int or not 1 <= self.bind_port <= 65535:
            raise ValueError("proxy bind port is invalid")
        if (
            type(self.connect_allowlist) is not tuple
            or not self.connect_allowlist
            or any(type(value) is not str for value in self.connect_allowlist)
            or tuple(sorted(set(self.connect_allowlist))) != self.connect_allowlist
            or any(value not in FROZEN_CONNECT_AUTHORITIES for value in self.connect_allowlist)
        ):
            raise ValueError("proxy CONNECT allowlist is outside the frozen audience")
        if (
            self.authorization_mode != AUTHORIZATION_MODE
            or self.authorization_username_sha256 != _sha256(PROXY_USERNAME.encode("ascii"))
        ):
            raise ValueError("proxy authorization identity is invalid")
        if self.listener_mode != LISTENER_MODE:
            raise ValueError("proxy listener mode is invalid")
        if not isinstance(self.token_sha256, str) or not _SHA256_PATTERN.fullmatch(
            self.token_sha256
        ):
            raise ValueError("proxy token hash is invalid")
        integer_limits = (
            self.max_header_bytes,
            self.max_client_hello_bytes,
            self.max_tunnel_bytes,
            self.deadline_milliseconds,
        )
        if any(type(value) is not int for value in integer_limits):
            raise ValueError("proxy limits must be integers")
        if not 1024 <= self.max_header_bytes <= 32 * 1024:
            raise ValueError("proxy header limit is invalid")
        if not 512 <= self.max_client_hello_bytes <= 64 * 1024:
            raise ValueError("proxy ClientHello limit is invalid")
        if not 1024 <= self.max_tunnel_bytes <= 64 * 1024 * 1024:
            raise ValueError("proxy tunnel limit is invalid")
        if not 1000 <= self.deadline_milliseconds <= 900_000:
            raise ValueError("proxy deadline is invalid")
        if type(self.max_connections) is not int or self.max_connections != 1:
            raise ValueError("proxy must allow exactly one connection")
        if not isinstance(self.manifest_hash, str) or not _SHA256_PATTERN.fullmatch(
            self.manifest_hash
        ):
            raise ValueError("proxy manifest hash is invalid")
        if self.manifest_hash != _sha256(_canonical_json(self._payload())):
            raise ValueError("proxy manifest hash binding is invalid")


class SealedProxyUrl:
    """Secret-bearing URL that is redacted unless explicitly revealed for HTTPS_PROXY."""

    __slots__ = (
        "__secret_value",
        "__redacted_value",
        "manifest_hash",
        "bind_endpoint_sha256",
    )

    def __init__(
        self,
        secret_value: str,
        redacted_value: str,
        manifest_hash: str,
        bind_endpoint_sha256: str,
    ) -> None:
        self.__secret_value: str | None = secret_value
        self.__redacted_value = redacted_value
        self.manifest_hash = manifest_hash
        self.bind_endpoint_sha256 = bind_endpoint_sha256

    @classmethod
    def create(
        cls,
        manifest: ProxyManifest,
        *,
        authorization_token: bytes,
    ) -> SealedProxyUrl:
        manifest.validate()
        if (
            not isinstance(authorization_token, bytes)
            or _TOKEN_PATTERN.fullmatch(authorization_token) is None
        ):
            raise ValueError("proxy authorization token format is invalid")
        if not hmac.compare_digest(_token_sha256(authorization_token), manifest.token_sha256):
            raise ValueError("proxy authorization token does not match manifest")
        redacted = f"http://{manifest.bind_host}:{manifest.bind_port}"
        secret = (
            f"http://{PROXY_USERNAME}:{authorization_token.decode('ascii')}@"
            f"{manifest.bind_host}:{manifest.bind_port}"
        )
        return cls(
            secret_value=secret,
            redacted_value=redacted,
            manifest_hash=manifest.manifest_hash,
            bind_endpoint_sha256=manifest.bind_endpoint_sha256,
        )

    def consume_for_https_proxy_env(self) -> str:
        """Consume the value permitted only in the sealed child HTTPS_PROXY environment."""

        value = self.__secret_value
        if value is None:
            raise RuntimeError("sealed proxy URL was already consumed")
        self.__secret_value = None
        return value

    def to_audit_dict(self) -> dict[str, str]:
        return {
            "manifest_hash": self.manifest_hash,
            "bind_endpoint_sha256": self.bind_endpoint_sha256,
        }

    def __str__(self) -> str:
        return self.__redacted_value

    def __repr__(self) -> str:
        return (
            "SealedProxyUrl("
            f"manifest_hash={self.manifest_hash!r}, "
            f"bind_endpoint_sha256={self.bind_endpoint_sha256!r})"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("SealedProxyUrl cannot be serialized")


READINESS_SCHEMA_VERSION = "t13b-proxy-readiness-v1"
_READINESS_CONTROL_FIELDS = frozenset(
    {
        "schema_version",
        "manifest_hash",
        "bind_endpoint_sha256",
        "challenge_sha256",
        "process_id",
        "listener_handle",
        "listener_identity_sha256",
        "proof_hmac_sha256",
    }
)


@dataclass(frozen=True)
class ProxyReadiness:
    """Nonce-bound proof for an already-listening socket; not a network handshake."""

    schema_version: str
    manifest_hash: str
    bind_endpoint_sha256: str
    challenge_sha256: str
    process_id: int
    listener_handle: int
    listener_identity_sha256: str
    proof_hmac_sha256: str

    @classmethod
    def create(
        cls,
        manifest: ProxyManifest,
        *,
        authorization_token: bytes | bytearray,
        challenge: bytes,
        process_id: int,
        listener_handle: int,
    ) -> ProxyReadiness:
        manifest.validate()
        if (
            not isinstance(authorization_token, (bytes, bytearray))
            or _TOKEN_PATTERN.fullmatch(authorization_token) is None
            or not hmac.compare_digest(_token_sha256(authorization_token), manifest.token_sha256)
        ):
            raise ValueError("proxy authorization token does not match manifest")
        if not isinstance(challenge, bytes) or len(challenge) != 32:
            raise ValueError("proxy readiness challenge must be exactly 32 bytes")
        if type(process_id) is not int or not 0 < process_id <= _MAX_PROCESS_ID:
            raise ValueError("proxy readiness process id is invalid")
        if type(listener_handle) is not int or not 0 <= listener_handle <= _MAX_LISTENER_HANDLE:
            raise ValueError("proxy readiness listener handle is invalid")
        provisional = cls(
            schema_version=READINESS_SCHEMA_VERSION,
            manifest_hash=manifest.manifest_hash,
            bind_endpoint_sha256=manifest.bind_endpoint_sha256,
            challenge_sha256=_sha256(challenge),
            process_id=process_id,
            listener_handle=listener_handle,
            listener_identity_sha256="",
            proof_hmac_sha256="",
        )
        identity = _sha256(_canonical_json(provisional._identity_payload()))
        bound = replace(provisional, listener_identity_sha256=identity)
        readiness = replace(
            bound,
            proof_hmac_sha256=_readiness_hmac(authorization_token, bound._proof_payload()),
        )
        readiness.validate_control()
        return readiness

    @classmethod
    def from_control_json_bytes(cls, raw: bytes) -> ProxyReadiness:
        """Parse canonical bytes; callers must still invoke ``verify`` before trust."""

        if not isinstance(raw, bytes):
            raise ValueError("proxy readiness must be UTF-8 bytes")
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("proxy readiness is not valid UTF-8 JSON") from error
        if not isinstance(value, dict) or set(value) != _READINESS_CONTROL_FIELDS:
            raise ValueError("proxy readiness fields are not exact")
        readiness = cls(
            schema_version=value["schema_version"],
            manifest_hash=value["manifest_hash"],
            bind_endpoint_sha256=value["bind_endpoint_sha256"],
            challenge_sha256=value["challenge_sha256"],
            process_id=value["process_id"],
            listener_handle=value["listener_handle"],
            listener_identity_sha256=value["listener_identity_sha256"],
            proof_hmac_sha256=value["proof_hmac_sha256"],
        )
        readiness.validate_control()
        if raw != readiness.to_control_json_bytes():
            raise ValueError("proxy readiness is not strict canonical JSON")
        return readiness

    def _identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "manifest_hash": self.manifest_hash,
            "bind_endpoint_sha256": self.bind_endpoint_sha256,
            "challenge_sha256": self.challenge_sha256,
            "process_id": self.process_id,
            "listener_handle": self.listener_handle,
        }

    def _proof_payload(self) -> dict[str, object]:
        return {
            **self._identity_payload(),
            "listener_identity_sha256": self.listener_identity_sha256,
        }

    def to_control_dict(self) -> dict[str, object]:
        return {
            **self._proof_payload(),
            "proof_hmac_sha256": self.proof_hmac_sha256,
        }

    def to_control_json_bytes(self) -> bytes:
        return _canonical_json(self.to_control_dict())

    def to_audit_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "manifest_hash": self.manifest_hash,
            "bind_endpoint_sha256": self.bind_endpoint_sha256,
            "listener_identity_sha256": self.listener_identity_sha256,
            "proof_hmac_sha256": self.proof_hmac_sha256,
        }

    def to_audit_json_bytes(self) -> bytes:
        return _canonical_json(self.to_audit_dict())

    def validate_control(self) -> None:
        """Validate structure and listener identity hash, but not the keyed proof."""

        if self.schema_version != READINESS_SCHEMA_VERSION:
            raise ValueError("proxy readiness schema is invalid")
        sha256_values = (
            self.manifest_hash,
            self.bind_endpoint_sha256,
            self.challenge_sha256,
            self.listener_identity_sha256,
        )
        if any(
            not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None
            for value in sha256_values
        ):
            raise ValueError("proxy readiness hash is invalid")
        if type(self.process_id) is not int or not 0 < self.process_id <= _MAX_PROCESS_ID:
            raise ValueError("proxy readiness process id is invalid")
        if (
            type(self.listener_handle) is not int
            or not 0 <= self.listener_handle <= _MAX_LISTENER_HANDLE
        ):
            raise ValueError("proxy readiness listener handle is invalid")
        if (
            not isinstance(self.proof_hmac_sha256, str)
            or _HMAC_SHA256_PATTERN.fullmatch(self.proof_hmac_sha256) is None
        ):
            raise ValueError("proxy readiness HMAC is invalid")
        expected_identity = _sha256(_canonical_json(self._identity_payload()))
        if not hmac.compare_digest(self.listener_identity_sha256, expected_identity):
            raise ValueError("proxy readiness listener identity is invalid")

    @classmethod
    def verify(
        cls,
        readiness: ProxyReadiness,
        manifest: ProxyManifest,
        *,
        authorization_token: bytes | bytearray,
        challenge: bytes,
        expected_process_id: int,
        expected_listener_handle: int,
    ) -> bool:
        if not isinstance(readiness, cls) or not isinstance(manifest, ProxyManifest):
            return False
        try:
            manifest.validate()
            readiness.validate_control()
        except (AttributeError, TypeError, ValueError):
            return False
        if (
            not isinstance(authorization_token, (bytes, bytearray))
            or _TOKEN_PATTERN.fullmatch(authorization_token) is None
            or not hmac.compare_digest(_token_sha256(authorization_token), manifest.token_sha256)
            or not isinstance(challenge, bytes)
            or len(challenge) != 32
            or type(expected_process_id) is not int
            or type(expected_listener_handle) is not int
            or readiness.schema_version != READINESS_SCHEMA_VERSION
            or readiness.manifest_hash != manifest.manifest_hash
            or readiness.bind_endpoint_sha256 != manifest.bind_endpoint_sha256
            or readiness.challenge_sha256 != _sha256(challenge)
            or readiness.process_id != expected_process_id
            or readiness.listener_handle != expected_listener_handle
            or readiness.process_id <= 0
            or readiness.listener_handle < 0
        ):
            return False
        expected_proof = _readiness_hmac(authorization_token, readiness._proof_payload())
        return hmac.compare_digest(readiness.proof_hmac_sha256, expected_proof)


class ProxyTerminal(str, Enum):
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class ProxyResult:
    """Redacted audit projection; never contains request headers or payload."""

    manifest_hash: str
    bind_endpoint_sha256: str
    terminal: ProxyTerminal
    reason_code: str
    authority_sha256: str | None
    peer_sha256: str | None
    dns_answer_set_sha256: str | None
    dns_recheck_answer_set_sha256: str | None
    sni_sha256: str | None
    accepted_connections: int
    rejected_connections: int
    client_to_upstream_bytes: int
    upstream_to_client_bytes: int
    elapsed_ms: int

    def to_audit_dict(self) -> dict[str, object]:
        return {
            "manifest_hash": self.manifest_hash,
            "bind_endpoint_sha256": self.bind_endpoint_sha256,
            "terminal": self.terminal.value,
            "reason_code": self.reason_code,
            "authority_sha256": self.authority_sha256,
            "peer_sha256": self.peer_sha256,
            "dns_answer_set_sha256": self.dns_answer_set_sha256,
            "dns_recheck_answer_set_sha256": self.dns_recheck_answer_set_sha256,
            "sni_sha256": self.sni_sha256,
            "accepted_connections": self.accepted_connections,
            "rejected_connections": self.rejected_connections,
            "client_to_upstream_bytes": self.client_to_upstream_bytes,
            "upstream_to_client_bytes": self.upstream_to_client_bytes,
            "elapsed_ms": self.elapsed_ms,
        }


class _ProxyRejected(Exception):
    def __init__(self, reason_code: str, status: int = 400) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status = status


class _DnsTimeout(TimeoutError):
    pass


_STATUS_LINES = {
    400: b"HTTP/1.1 400 Bad Request\r\n",
    403: b"HTTP/1.1 403 Forbidden\r\n",
    405: b"HTTP/1.1 405 Method Not Allowed\r\n",
    407: b"HTTP/1.1 407 Proxy Authentication Required\r\n",
    502: b"HTTP/1.1 502 Bad Gateway\r\n",
    504: b"HTTP/1.1 504 Gateway Timeout\r\n",
}


class Resolver(Protocol):
    def resolve(self, host: str, port: int, deadline: float) -> tuple[str, ...]: ...


class Connector(Protocol):
    def connect(self, numeric_ip: str, port: int, timeout: float) -> ConnectedUpstream: ...


@dataclass(frozen=True)
class ConnectedUpstream:
    socket: socket.socket
    peer_ip: str


class SystemResolver:
    """Resolve directly with the operating system; no URL or proxy API is involved."""

    def resolve(self, host: str, port: int, deadline: float) -> tuple[str, ...]:
        if deadline <= time.monotonic():
            raise TimeoutError("DNS deadline expired")
        answers = socket.getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        if deadline <= time.monotonic():
            raise TimeoutError("DNS deadline expired")
        return tuple(str(answer[4][0]) for answer in answers)


class NumericSocketConnector:
    """Connect an already validated numeric address without a second name lookup."""

    def connect(self, numeric_ip: str, port: int, timeout: float) -> ConnectedUpstream:
        address = ipaddress.ip_address(numeric_ip)
        family = socket.AF_INET if address.version == 4 else socket.AF_INET6
        upstream = socket.socket(family, socket.SOCK_STREAM)
        try:
            upstream.settimeout(timeout)
            if address.version == 4:
                upstream.connect((address.compressed, port))
            else:
                upstream.connect((address.compressed, port, 0, 0))
            peer_ip = str(upstream.getpeername()[0])
            return ConnectedUpstream(socket=upstream, peer_ip=peer_ip)
        except BaseException:
            _close_socket(upstream)
            raise


class OneShotConnectProxy:
    """Consume one authenticated CONNECT on a broker-prebound IPv4 loopback socket."""

    def __init__(
        self,
        manifest: ProxyManifest,
        *,
        authorization_token: bytes,
        prebound_listener: socket.socket,
        resolver: Resolver | None = None,
        connector: Connector | None = None,
    ) -> None:
        manifest.validate()
        if (
            not isinstance(authorization_token, bytes)
            or _TOKEN_PATTERN.fullmatch(authorization_token) is None
        ):
            raise ValueError("proxy authorization token format is invalid")
        if not hmac.compare_digest(_token_sha256(authorization_token), manifest.token_sha256):
            raise ValueError("proxy authorization token does not match manifest")
        self._manifest = manifest
        self._authorization_token = bytearray(authorization_token)
        self._resolver = resolver if resolver is not None else SystemResolver()
        self._connector = connector if connector is not None else NumericSocketConnector()
        self._serve_lock = threading.Lock()
        self._served = False
        try:
            address = prebound_listener.getsockname()
            socket_type = prebound_listener.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
            accepting = prebound_listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        except OSError as error:
            raise ValueError("broker prebound listener is not live") from error
        if (
            prebound_listener.family != socket.AF_INET
            or socket_type != socket.SOCK_STREAM
            or not isinstance(address, tuple)
            or address[:2] != (manifest.bind_host, manifest.bind_port)
            or accepting != 1
            or prebound_listener.fileno() < 0
        ):
            raise ValueError("broker prebound listener does not match manifest")
        self._listener = prebound_listener

    def close(self) -> None:
        try:
            _close_socket(self._listener)
        finally:
            _wipe(self._authorization_token)

    def challenge_readiness(self, challenge: bytes) -> ProxyReadiness:
        """Prove control of the live listener without consuming its data connection."""

        if not isinstance(challenge, bytes) or len(challenge) != 32:
            raise ValueError("proxy readiness challenge must be exactly 32 bytes")
        with self._serve_lock:
            if self._served:
                raise RuntimeError("one-shot proxy has already been consumed")
            try:
                address = self._listener.getsockname()
                accepting = self._listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
                listener_handle = self._listener.fileno()
            except OSError as error:
                raise RuntimeError("proxy listener is not live") from error
            if (
                not isinstance(address, tuple)
                or address[:2] != (self._manifest.bind_host, self._manifest.bind_port)
                or accepting != 1
                or listener_handle < 0
            ):
                raise RuntimeError("proxy listener is not live")
            return ProxyReadiness.create(
                self._manifest,
                authorization_token=self._authorization_token,
                challenge=challenge,
                process_id=os.getpid(),
                listener_handle=listener_handle,
            )

    def serve_once(self) -> ProxyResult:
        with self._serve_lock:
            if self._served:
                raise RuntimeError("one-shot proxy has already been consumed")
            self._served = True
        started = time.monotonic()
        deadline = started + self._manifest.deadline_milliseconds / 1000
        client: socket.socket | None = None
        upstream: socket.socket | None = None
        authority_hash: str | None = None
        peer_hash: str | None = None
        dns_answer_set_hash: str | None = None
        dns_recheck_answer_set_hash: str | None = None
        sni_hash: str | None = None
        accepted = 0
        client_to_upstream = 0
        upstream_to_client = 0
        tunnel_established = False
        try:
            self._listener.settimeout(self._remaining(deadline))
            client, address = self._listener.accept()
            accepted = 1
            if not isinstance(address, tuple) or address[0] != "127.0.0.1":
                raise _ProxyRejected("CLIENT_NOT_IPV4_LOOPBACK", 403)
            raw_headers = self._read_headers(client, deadline)
            authority = self._parse_connect(raw_headers)
            authority_hash = _sha256(authority.encode("ascii"))
            self._authenticate(raw_headers)
            host = authority[:-4]
            addresses = self._resolve_global(host, deadline)
            dns_answer_set_hash = _sha256(_canonical_json(list(addresses)))
            numeric_ip = addresses[0]
            try:
                connection = self._connector.connect(
                    numeric_ip,
                    443,
                    self._remaining(deadline),
                )
            except (OSError, TimeoutError) as error:
                raise _ProxyRejected("UPSTREAM_CONNECT_FAILED", 502) from error
            upstream = connection.socket
            rebound_addresses = self._resolve_global(host, deadline)
            dns_recheck_answer_set_hash = _sha256(_canonical_json(list(rebound_addresses)))
            if rebound_addresses != addresses:
                raise _ProxyRejected("DNS_REBINDING_REJECTED", 403)
            peer_ip = self._global_address(connection.peer_ip, "PEER_ADDRESS_REJECTED")
            if peer_ip != numeric_ip or peer_ip not in rebound_addresses:
                raise _ProxyRejected("PEER_MISMATCH", 403)
            peer_hash = _sha256(peer_ip.encode("ascii"))
            self._send_established(client, deadline)
            tunnel_established = True
            client_hello = self._read_client_hello(client, deadline, host)
            sni_hash = _sha256(host.encode("ascii"))
            if len(client_hello) > self._manifest.max_tunnel_bytes:
                raise _ProxyRejected("TUNNEL_BYTES_EXCEEDED")
            self._send_all(upstream, client_hello, deadline)
            client_to_upstream = len(client_hello)
            try:
                client_to_upstream, upstream_to_client = self._relay(
                    client,
                    upstream,
                    deadline,
                    client_to_upstream,
                )
            except OSError as error:
                raise _ProxyRejected("TUNNEL_IO_FAILED") from error
            return self._result(
                started,
                ProxyTerminal.COMPLETED,
                "TUNNEL_CLOSED",
                authority_hash,
                peer_hash,
                dns_answer_set_hash,
                dns_recheck_answer_set_hash,
                sni_hash,
                accepted,
                client_to_upstream,
                upstream_to_client,
            )
        except _DnsTimeout:
            if client is not None and not tunnel_established:
                self._send_status(client, 504, deadline)
            return self._result(
                started,
                ProxyTerminal.TIMEOUT,
                "DNS_TIMEOUT",
                authority_hash,
                peer_hash,
                dns_answer_set_hash,
                dns_recheck_answer_set_hash,
                sni_hash,
                accepted,
                client_to_upstream,
                upstream_to_client,
            )
        except TimeoutError:
            if client is not None and not tunnel_established:
                self._send_status(client, 504, deadline)
            return self._result(
                started,
                ProxyTerminal.TIMEOUT,
                "DEADLINE_EXPIRED",
                authority_hash,
                peer_hash,
                dns_answer_set_hash,
                dns_recheck_answer_set_hash,
                sni_hash,
                accepted,
                client_to_upstream,
                upstream_to_client,
            )
        except _ProxyRejected as error:
            if client is not None and not tunnel_established:
                self._send_status(client, error.status, deadline)
            return self._result(
                started,
                ProxyTerminal.REJECTED,
                error.reason_code,
                authority_hash,
                peer_hash,
                dns_answer_set_hash,
                dns_recheck_answer_set_hash,
                sni_hash,
                accepted,
                client_to_upstream,
                upstream_to_client,
            )
        except Exception:
            return self._result(
                started,
                ProxyTerminal.INTERNAL_ERROR,
                "INTERNAL_ERROR",
                authority_hash,
                peer_hash,
                dns_answer_set_hash,
                dns_recheck_answer_set_hash,
                sni_hash,
                accepted,
                client_to_upstream,
                upstream_to_client,
            )
        finally:
            try:
                _close_socket(upstream)
                _close_socket(client)
                _close_socket(self._listener)
            finally:
                _wipe(self._authorization_token)

    def _read_headers(self, client: socket.socket, deadline: float) -> bytes:
        value = bytearray()
        while b"\r\n\r\n" not in value:
            client.settimeout(self._remaining(deadline))
            remaining = self._manifest.max_header_bytes + 1 - len(value)
            if remaining <= 0:
                raise _ProxyRejected("HEADERS_TOO_LARGE")
            chunk = client.recv(min(4096, remaining))
            if not chunk:
                raise _ProxyRejected("HEADERS_TRUNCATED")
            value.extend(chunk)
        marker = value.find(b"\r\n\r\n") + 4
        if marker != len(value):
            raise _ProxyRejected("EARLY_TUNNEL_BYTES_REJECTED")
        if len(value) > self._manifest.max_header_bytes:
            raise _ProxyRejected("HEADERS_TOO_LARGE")
        return bytes(value)

    def _parse_headers(self, raw: bytes) -> tuple[bytes, dict[bytes, bytes]]:
        lines = raw[:-4].split(b"\r\n")
        if not lines or len(lines) > 33:
            raise _ProxyRejected("HEADER_COUNT_INVALID")
        parsed: dict[bytes, bytes] = {}
        for line in lines[1:]:
            if not line or line[:1] in b" \t" or b":" not in line:
                raise _ProxyRejected("HEADER_SYNTAX_INVALID")
            name, value = line.split(b":", 1)
            name = name.lower()
            value = value.strip(b" \t")
            if (
                _HEADER_NAME_PATTERN.fullmatch(name) is None
                or name in parsed
                or any(byte < 32 or byte == 127 for byte in value)
            ):
                raise _ProxyRejected("HEADER_SYNTAX_INVALID")
            parsed[name] = value
        if b"content-length" in parsed or b"transfer-encoding" in parsed:
            raise _ProxyRejected("REQUEST_BODY_REJECTED")
        return lines[0], parsed

    def _parse_connect(self, raw: bytes) -> str:
        request_line, headers = self._parse_headers(raw)
        parts = request_line.split(b" ")
        if len(parts) != 3:
            raise _ProxyRejected("REQUEST_LINE_INVALID")
        method, raw_authority, version = parts
        if method != b"CONNECT":
            raise _ProxyRejected("METHOD_REJECTED", 405)
        if version != b"HTTP/1.1":
            raise _ProxyRejected("HTTP_VERSION_REJECTED")
        try:
            authority = raw_authority.decode("ascii")
        except UnicodeDecodeError as error:
            raise _ProxyRejected("AUTHORITY_INVALID") from error
        if authority not in self._manifest.connect_allowlist:
            raise _ProxyRejected("AUTHORITY_REJECTED", 403)
        if headers.get(b"host") != raw_authority:
            raise _ProxyRejected("HOST_MISMATCH")
        return authority

    def _authenticate(self, raw: bytes) -> None:
        _request_line, headers = self._parse_headers(raw)
        credentials = bytearray(PROXY_USERNAME.encode("ascii") + b":")
        encoded = bytearray()
        expected = bytearray()
        try:
            credentials.extend(self._authorization_token)
            encoded.extend(base64.b64encode(credentials))
            expected.extend(b"Basic ")
            expected.extend(encoded)
            supplied = headers.get(b"proxy-authorization", b"")
            if not hmac.compare_digest(supplied, expected):
                raise _ProxyRejected("AUTH_REJECTED", 407)
        finally:
            _wipe(credentials)
            _wipe(encoded)
            _wipe(expected)

    def _send_established(self, client: socket.socket, deadline: float) -> None:
        self._send_all(
            client,
            b"HTTP/1.1 200 Connection Established\r\n\r\n",
            deadline,
        )

    def _read_client_hello(self, client: socket.socket, deadline: float, host: str) -> bytes:
        wire = bytearray()
        handshake = bytearray()
        expected_handshake_bytes: int | None = None
        while expected_handshake_bytes is None or len(handshake) < expected_handshake_bytes:
            header = self._recv_exact(client, 5, deadline)
            wire.extend(header)
            if len(wire) > self._manifest.max_client_hello_bytes:
                raise _ProxyRejected("CLIENT_HELLO_TOO_LARGE")
            if header[0] != 22:
                raise _ProxyRejected("TLS_HANDSHAKE_RECORD_REQUIRED")
            if header[1:3] not in {b"\x03\x01", b"\x03\x02", b"\x03\x03"}:
                raise _ProxyRejected("TLS_RECORD_VERSION_INVALID")
            record_length = int.from_bytes(header[3:5], "big")
            if record_length <= 0 or record_length > 18_432:
                raise _ProxyRejected("TLS_RECORD_LENGTH_INVALID")
            record = self._recv_exact(client, record_length, deadline)
            wire.extend(record)
            if len(wire) > self._manifest.max_client_hello_bytes:
                raise _ProxyRejected("CLIENT_HELLO_TOO_LARGE")
            handshake.extend(record)
            if expected_handshake_bytes is None and len(handshake) >= 4:
                if handshake[0] != 1:
                    raise _ProxyRejected("TLS_CLIENT_HELLO_REQUIRED")
                expected_handshake_bytes = 4 + int.from_bytes(handshake[1:4], "big")
                if expected_handshake_bytes > self._manifest.max_client_hello_bytes:
                    raise _ProxyRejected("CLIENT_HELLO_TOO_LARGE")
        if len(handshake) != expected_handshake_bytes:
            raise _ProxyRejected("TLS_CLIENT_HELLO_TRAILING_DATA")
        sni = self._client_hello_sni(bytes(handshake[4:]))
        if sni != host:
            raise _ProxyRejected("SNI_MISMATCH", 403)
        return bytes(wire)

    def _recv_exact(self, source: socket.socket, size: int, deadline: float) -> bytes:
        value = bytearray()
        while len(value) < size:
            source.settimeout(self._remaining(deadline))
            chunk = source.recv(size - len(value))
            if not chunk:
                raise _ProxyRejected("TLS_CLIENT_HELLO_TRUNCATED")
            value.extend(chunk)
        return bytes(value)

    @staticmethod
    def _client_hello_sni(body: bytes) -> str:
        if len(body) < 38:
            raise _ProxyRejected("TLS_CLIENT_HELLO_MALFORMED")
        cursor = 34
        session_id_length = body[cursor]
        cursor += 1 + session_id_length
        if cursor + 2 > len(body):
            raise _ProxyRejected("TLS_CLIENT_HELLO_MALFORMED")
        cipher_length = int.from_bytes(body[cursor : cursor + 2], "big")
        cursor += 2
        if cipher_length == 0 or cipher_length % 2 != 0 or cursor + cipher_length > len(body):
            raise _ProxyRejected("TLS_CLIENT_HELLO_MALFORMED")
        cursor += cipher_length
        if cursor >= len(body):
            raise _ProxyRejected("TLS_CLIENT_HELLO_MALFORMED")
        compression_length = body[cursor]
        cursor += 1
        if compression_length == 0 or cursor + compression_length + 2 > len(body):
            raise _ProxyRejected("TLS_CLIENT_HELLO_MALFORMED")
        cursor += compression_length
        extensions_length = int.from_bytes(body[cursor : cursor + 2], "big")
        cursor += 2
        if cursor + extensions_length != len(body):
            raise _ProxyRejected("TLS_EXTENSIONS_MALFORMED")
        end = cursor + extensions_length
        seen_types: set[int] = set()
        server_names: list[str] = []
        while cursor < end:
            if cursor + 4 > end:
                raise _ProxyRejected("TLS_EXTENSIONS_MALFORMED")
            extension_type = int.from_bytes(body[cursor : cursor + 2], "big")
            extension_length = int.from_bytes(body[cursor + 2 : cursor + 4], "big")
            cursor += 4
            if extension_type in seen_types or cursor + extension_length > end:
                raise _ProxyRejected("TLS_EXTENSIONS_MALFORMED")
            seen_types.add(extension_type)
            extension = body[cursor : cursor + extension_length]
            cursor += extension_length
            if extension_type in {0xFE0D, 0xFFCE}:
                raise _ProxyRejected("ECH_REJECTED", 403)
            if extension_type == 0:
                server_names.extend(OneShotConnectProxy._parse_server_names(extension))
        if len(server_names) != 1:
            raise _ProxyRejected("SNI_COUNT_INVALID", 403)
        return server_names[0]

    @staticmethod
    def _parse_server_names(extension: bytes) -> list[str]:
        if len(extension) < 5:
            raise _ProxyRejected("SNI_MALFORMED")
        names_length = int.from_bytes(extension[:2], "big")
        if names_length != len(extension) - 2:
            raise _ProxyRejected("SNI_MALFORMED")
        cursor = 2
        names: list[str] = []
        while cursor < len(extension):
            if cursor + 3 > len(extension) or extension[cursor] != 0:
                raise _ProxyRejected("SNI_MALFORMED")
            name_length = int.from_bytes(extension[cursor + 1 : cursor + 3], "big")
            cursor += 3
            if name_length == 0 or cursor + name_length > len(extension):
                raise _ProxyRejected("SNI_MALFORMED")
            try:
                name = extension[cursor : cursor + name_length].decode("ascii")
            except UnicodeDecodeError as error:
                raise _ProxyRejected("SNI_MALFORMED") from error
            cursor += name_length
            if name != name.lower() or "\x00" in name:
                raise _ProxyRejected("SNI_MALFORMED")
            names.append(name)
        return names

    def _relay(
        self,
        client: socket.socket,
        upstream: socket.socket,
        deadline: float,
        initial_client_bytes: int,
    ) -> tuple[int, int]:
        client_bytes = initial_client_bytes
        upstream_bytes = 0
        while True:
            readable, _writable, _exceptional = select.select(
                [client, upstream],
                [],
                [],
                self._remaining(deadline),
            )
            if not readable:
                raise TimeoutError("proxy deadline expired")
            for source in readable:
                destination = upstream if source is client else client
                used = client_bytes + upstream_bytes
                room = self._manifest.max_tunnel_bytes - used
                source.settimeout(self._remaining(deadline))
                chunk = source.recv(min(16 * 1024, max(1, room + 1)))
                if not chunk:
                    return client_bytes, upstream_bytes
                if len(chunk) > room:
                    raise _ProxyRejected("TUNNEL_BYTES_EXCEEDED")
                self._send_all(destination, chunk, deadline)
                if source is client:
                    client_bytes += len(chunk)
                else:
                    upstream_bytes += len(chunk)

    def _send_all(self, destination: socket.socket, value: bytes, deadline: float) -> None:
        destination.settimeout(self._remaining(deadline))
        destination.sendall(value)

    def _resolve_global(self, host: str, deadline: float) -> tuple[str, ...]:
        mailbox: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)
        resolver = self._resolver

        def resolve_without_secrets() -> None:
            try:
                answers = resolver.resolve(host, 443, deadline)
            except TimeoutError:
                mailbox.put(("timeout", ()))
            except BaseException:
                mailbox.put(("error", ()))
            else:
                mailbox.put(("ok", answers))

        worker = threading.Thread(
            target=resolve_without_secrets,
            name="pkm-proxy-dns",
            daemon=True,
        )
        worker.start()
        try:
            status, raw_answers_object = mailbox.get(timeout=self._remaining(deadline))
        except queue.Empty as error:
            raise _DnsTimeout("DNS deadline expired") from error
        if status == "timeout":
            raise _DnsTimeout("DNS deadline expired")
        if status != "ok":
            raise _ProxyRejected("DNS_RESOLUTION_FAILED", 502)
        try:
            self._remaining(deadline)
        except TimeoutError as error:
            raise _DnsTimeout("DNS deadline expired") from error
        raw_answers = raw_answers_object
        if not isinstance(raw_answers, tuple) or not raw_answers or len(raw_answers) > 16:
            raise _ProxyRejected("DNS_ANSWER_SET_INVALID", 403)
        parsed: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        for raw_answer in raw_answers:
            canonical = self._global_address(raw_answer, "DNS_ADDRESS_REJECTED")
            parsed.add(ipaddress.ip_address(canonical))
        return tuple(
            address.compressed
            for address in sorted(parsed, key=lambda value: (value.version, value.packed))
        )

    @staticmethod
    def _global_address(raw_address: str, reason_code: str) -> str:
        if not isinstance(raw_address, str) or "%" in raw_address:
            raise _ProxyRejected(reason_code, 403)
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as error:
            raise _ProxyRejected(reason_code, 403) from error
        mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
        if (
            not address.is_global
            or address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_unspecified
            or address.is_multicast
            or mapped is not None
        ):
            raise _ProxyRejected(reason_code, 403)
        return address.compressed

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("proxy deadline expired")
        return remaining

    def _send_status(self, client: socket.socket, status: int, deadline: float) -> None:
        try:
            client.settimeout(self._remaining(deadline))
            client.sendall(
                _STATUS_LINES[status] + b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )
        except (OSError, TimeoutError):
            pass

    def _result(
        self,
        started: float,
        terminal: ProxyTerminal,
        reason_code: str,
        authority_sha256: str | None,
        peer_sha256: str | None,
        dns_answer_set_sha256: str | None,
        dns_recheck_answer_set_sha256: str | None,
        sni_sha256: str | None,
        accepted_connections: int,
        client_to_upstream_bytes: int,
        upstream_to_client_bytes: int,
    ) -> ProxyResult:
        return ProxyResult(
            manifest_hash=self._manifest.manifest_hash,
            bind_endpoint_sha256=self._manifest.bind_endpoint_sha256,
            terminal=terminal,
            reason_code=reason_code,
            authority_sha256=authority_sha256,
            peer_sha256=peer_sha256,
            dns_answer_set_sha256=dns_answer_set_sha256,
            dns_recheck_answer_set_sha256=dns_recheck_answer_set_sha256,
            sni_sha256=sni_sha256,
            accepted_connections=accepted_connections,
            rejected_connections=int(
                accepted_connections > 0 and terminal is not ProxyTerminal.COMPLETED
            ),
            client_to_upstream_bytes=client_to_upstream_bytes,
            upstream_to_client_bytes=upstream_to_client_bytes,
            elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
