from __future__ import annotations

import base64
import json
import socket
import ssl
import struct
import threading
import time
from dataclasses import asdict, replace
from typing import Any, cast
from urllib.parse import urlsplit

import pytest

from pkm_workflow.codex_proxy import (
    CODEX_0147_PROXY_AUTH_TRACE_REQUIRED,
    PROXY_USERNAME,
    ConnectedUpstream,
    Connector,
    NumericSocketConnector,
    OneShotConnectProxy,
    ProxyManifest,
    ProxyReadiness,
    ProxyResult,
    ProxyTerminal,
    Resolver,
    SealedProxyUrl,
    SystemResolver,
)

TOKEN = b"CANARY_proxy_token_0123456789abcdef_ABCDEF"
AUTHORIZATION = b"Basic " + base64.b64encode(PROXY_USERNAME.encode("ascii") + b":" + TOKEN)
GOLDEN_MANIFEST_JSON = (
    b'{"authorization_mode":"BASIC_USERINFO_ENV_ONLY",'
    b'"authorization_username_sha256":"sha256:5f27b95a2a488a37d08dd32e7ea7977a39d791e29aa7911b1a74038f6f076827",'
    b'"bind_host":"127.0.0.1","bind_port":43117,'
    b'"connect_allowlist":["chatgpt.com:443"],"deadline_milliseconds":900000,'
    b'"listener_mode":"BROKER_PREBOUND_INHERITED",'
    b'"manifest_hash":"sha256:d31aa2078742d704be2dacfdae1056d9af8b254842375d7827e0108c9dba71a0",'
    b'"max_client_hello_bytes":32768,"max_connections":1,'
    b'"max_header_bytes":8192,"max_tunnel_bytes":1048576,'
    b'"schema_version":"t13b-loopback-connect-v1",'
    b'"token_sha256":"sha256:52174e6d68471b6768d4d0d88a552bb083b49d341462d47ee0adc434ea9d5271"}'
)
GOLDEN_READINESS_CONTROL_JSON = (
    b'{"bind_endpoint_sha256":"sha256:af4873d2ce02ac6018b9b5b21d868f12e293635af76f82173c23e34cb32ffc48",'
    b'"challenge_sha256":"sha256:4113d54b0b611294b7f595b691c9db541fc0fc719848d6c5c34522eacc0b3a24",'
    b'"listener_handle":31337,'
    b'"listener_identity_sha256":"sha256:5eb18d795c7530ca3edb4cd1f7b9120aaeb78f9798d7dcf19e00acb2a537c352",'
    b'"manifest_hash":"sha256:d31aa2078742d704be2dacfdae1056d9af8b254842375d7827e0108c9dba71a0",'
    b'"process_id":4242,'
    b'"proof_hmac_sha256":"hmac-sha256:24dec4f786acbf2393c47b08b1a6c1012511105afea081e07acff392e68d2e54",'
    b'"schema_version":"t13b-proxy-readiness-v1"}'
)
GOLDEN_READINESS_AUDIT_JSON = (
    b'{"bind_endpoint_sha256":"sha256:af4873d2ce02ac6018b9b5b21d868f12e293635af76f82173c23e34cb32ffc48",'
    b'"listener_identity_sha256":"sha256:5eb18d795c7530ca3edb4cd1f7b9120aaeb78f9798d7dcf19e00acb2a537c352",'
    b'"manifest_hash":"sha256:d31aa2078742d704be2dacfdae1056d9af8b254842375d7827e0108c9dba71a0",'
    b'"proof_hmac_sha256":"hmac-sha256:24dec4f786acbf2393c47b08b1a6c1012511105afea081e07acff392e68d2e54",'
    b'"schema_version":"t13b-proxy-readiness-v1"}'
)
RUST_VECTOR_TOKEN = b"CANARY_rust_proxy_token_0123456789abcdef_XYZ"
RUST_VECTOR_MANIFEST_JSON = (
    b'{"authorization_mode":"BASIC_USERINFO_ENV_ONLY",'
    b'"authorization_username_sha256":"sha256:5f27b95a2a488a37d08dd32e7ea7977a39d791e29aa7911b1a74038f6f076827",'
    b'"bind_host":"127.0.0.1","bind_port":54219,'
    b'"connect_allowlist":["api.openai.com:443"],"deadline_milliseconds":900000,'
    b'"listener_mode":"BROKER_PREBOUND_INHERITED",'
    b'"manifest_hash":"sha256:f5bd26b178dacd6fa37c0e91d77e86876307af93cac6c9ba62864210777ca3eb",'
    b'"max_client_hello_bytes":32768,"max_connections":1,'
    b'"max_header_bytes":8192,"max_tunnel_bytes":1048576,'
    b'"schema_version":"t13b-loopback-connect-v1",'
    b'"token_sha256":"sha256:b53f58bd02c2fdd86bb8ccc4e05ab30496b60ca6631779b1680b78fd1e3c8d49"}'
)
RUST_VECTOR_READINESS_JSON = (
    b'{"bind_endpoint_sha256":"sha256:f6b98659566ba56ad11f251cef702d2caed3ac0a460964b04b7a736d27db7b4d",'
    b'"challenge_sha256":"sha256:16b72cfab7dbca73cb348f4e59a74b5c56d6e95574c1e9ca84850d69f5fa9430",'
    b'"listener_handle":424242,'
    b'"listener_identity_sha256":"sha256:d6362b72ea3085c5c24b8f88821dda08be564a96c7c47b79be94db6fd24ca801",'
    b'"manifest_hash":"sha256:f5bd26b178dacd6fa37c0e91d77e86876307af93cac6c9ba62864210777ca3eb",'
    b'"process_id":7331,'
    b'"proof_hmac_sha256":"hmac-sha256:4fb52539cd1b0cc948b32c8e196d5cbdcd1e60d6a20c7d608b36b6b62724f456",'
    b'"schema_version":"t13b-proxy-readiness-v1"}'
)


def _manifest() -> ProxyManifest:
    return ProxyManifest.create(
        bind_port=43117,
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
    )


def _free_loopback_port() -> int:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


def _prebound_listener(manifest: ProxyManifest) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            listener.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        listener.bind((manifest.bind_host, manifest.bind_port))
        listener.listen(1)
        return listener
    except BaseException:
        listener.close()
        raise


def _new_proxy(
    manifest: ProxyManifest,
    *,
    authorization_token: bytes = TOKEN,
    resolver: Resolver | None = None,
    connector: Connector | None = None,
) -> OneShotConnectProxy:
    listener = _prebound_listener(manifest)
    try:
        return OneShotConnectProxy(
            manifest,
            authorization_token=authorization_token,
            prebound_listener=listener,
            resolver=resolver,
            connector=connector,
        )
    except BaseException:
        listener.close()
        raise


class _StaticResolver:
    def __init__(self, *answers: str) -> None:
        self._answers = answers

    def resolve(self, host: str, port: int, deadline: float) -> tuple[str, ...]:
        assert host == "chatgpt.com"
        assert port == 443
        assert deadline > time.monotonic()
        return self._answers


class _ExplodingConnector:
    def connect(self, numeric_ip: str, port: int, timeout: float) -> ConnectedUpstream:
        raise AssertionError("unsafe DNS must be rejected before connect")


class _SequenceResolver:
    def __init__(self, *answer_sets: tuple[str, ...]) -> None:
        self._answer_sets = list(answer_sets)

    def resolve(self, host: str, port: int, deadline: float) -> tuple[str, ...]:
        assert host == "chatgpt.com"
        assert port == 443
        assert deadline > time.monotonic()
        return self._answer_sets.pop(0)


class _LocalConnector:
    def __init__(self, port: int, claimed_peer: str) -> None:
        self._port = port
        self._claimed_peer = claimed_peer

    def connect(self, numeric_ip: str, port: int, timeout: float) -> ConnectedUpstream:
        assert numeric_ip == "8.8.8.8"
        assert port == 443
        connected = socket.create_connection(("127.0.0.1", self._port), timeout=timeout)
        return ConnectedUpstream(socket=connected, peer_ip=self._claimed_peer)


class _FailingResolver:
    def resolve(self, host: str, port: int, deadline: float) -> tuple[str, ...]:
        raise OSError("redacted resolver failure")


class _ImmediateTimeoutResolver:
    def resolve(self, host: str, port: int, deadline: float) -> tuple[str, ...]:
        raise TimeoutError("redacted resolver timeout")


class _FailingConnector:
    def connect(self, numeric_ip: str, port: int, timeout: float) -> ConnectedUpstream:
        raise OSError("redacted connector failure")


class _CloseFailingSocket:
    def close(self) -> None:
        raise OSError("redacted close failure")


class _CloseFailingConnector:
    def connect(self, numeric_ip: str, port: int, timeout: float) -> ConnectedUpstream:
        return ConnectedUpstream(
            socket=cast(socket.socket, _CloseFailingSocket()),
            peer_ip="127.0.0.1",
        )


class _FalsyResolver(_StaticResolver):
    def __bool__(self) -> bool:
        return False


class _FalsyConnector(_FailingConnector):
    def __bool__(self) -> bool:
        return False


class _BlockingResolver:
    def __init__(self, answer: str = "8.8.8.8") -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.answer = answer

    def resolve(self, host: str, port: int, deadline: float) -> tuple[str, ...]:
        self.started.set()
        self.release.wait(5)
        return (self.answer,)


def _client_hello(host: str) -> bytes:
    incoming = ssl.MemoryBIO()
    outgoing = ssl.MemoryBIO()
    context = ssl.create_default_context()
    tls = context.wrap_bio(incoming, outgoing, server_side=False, server_hostname=host)
    with pytest.raises(ssl.SSLWantReadError):
        tls.do_handshake()
    return outgoing.read()


def _minimal_client_hello(
    names: tuple[bytes, ...] | None,
    *,
    extra_extensions: tuple[tuple[int, bytes], ...] = (),
    fragment_at: int | None = None,
) -> bytes:
    extensions: list[tuple[int, bytes]] = []
    if names is not None:
        entries = b"".join(b"\x00" + len(name).to_bytes(2, "big") + name for name in names)
        extensions.append((0, len(entries).to_bytes(2, "big") + entries))
    extensions.extend(extra_extensions)
    encoded_extensions = b"".join(
        extension_type.to_bytes(2, "big") + len(value).to_bytes(2, "big") + value
        for extension_type, value in extensions
    )
    body = (
        b"\x03\x03"
        + b"R" * 32
        + b"\x00"
        + b"\x00\x02\x13\x01"
        + b"\x01\x00"
        + len(encoded_extensions).to_bytes(2, "big")
        + encoded_extensions
    )
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body

    def record(value: bytes) -> bytes:
        return b"\x16\x03\x01" + len(value).to_bytes(2, "big") + value

    if fragment_at is None:
        return record(handshake)
    return record(handshake[:fragment_at]) + record(handshake[fragment_at:])


def _tls_exchange(
    hello: bytes,
    *,
    extra_client_bytes: bytes = b"",
    max_client_hello_bytes: int = 32768,
    max_tunnel_bytes: int = 1024 * 1024,
) -> tuple[bytes, ProxyResult, bytes]:
    upstream_received = bytearray()
    upstream_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream_listener.bind(("127.0.0.1", 0))
    upstream_listener.listen(1)

    def serve_upstream() -> None:
        connection, _address = upstream_listener.accept()
        try:
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                upstream_received.extend(chunk)
        finally:
            connection.close()
            upstream_listener.close()

    upstream_worker = threading.Thread(target=serve_upstream)
    upstream_worker.start()
    manifest = ProxyManifest.create(
        bind_port=_free_loopback_port(),
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
        max_client_hello_bytes=max_client_hello_bytes,
        max_tunnel_bytes=max_tunnel_bytes,
    )
    proxy = _new_proxy(
        manifest,
        authorization_token=TOKEN,
        resolver=_SequenceResolver(("8.8.8.8",), ("8.8.8.8",)),
        connector=_LocalConnector(int(upstream_listener.getsockname()[1]), "8.8.8.8"),
    )
    results: list[ProxyResult] = []
    proxy_worker = threading.Thread(target=lambda: results.append(proxy.serve_once()))
    proxy_worker.start()
    client = socket.create_connection(("127.0.0.1", manifest.bind_port), timeout=2)
    try:
        client.sendall(
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
            b"Host: chatgpt.com:443\r\n"
            b"Proxy-Authorization: " + AUTHORIZATION + b"\r\n\r\n"
        )
        response = client.recv(4096)
        client.sendall(hello + extra_client_bytes)
        client.shutdown(socket.SHUT_WR)
        try:
            while client.recv(4096):
                pass
        except ConnectionResetError:
            pass
    finally:
        client.close()
    proxy_worker.join(2)
    upstream_worker.join(2)
    assert not proxy_worker.is_alive()
    assert not upstream_worker.is_alive()
    return response, results[0], bytes(upstream_received)


def _raw_exchange(
    raw_request: bytes,
    *,
    max_header_bytes: int = 8192,
    resolver: Resolver | None = None,
    connector: Connector | None = None,
) -> tuple[bytes, ProxyResult, OneShotConnectProxy]:
    manifest = ProxyManifest.create(
        bind_port=_free_loopback_port(),
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
        max_header_bytes=max_header_bytes,
    )
    proxy = _new_proxy(
        manifest,
        authorization_token=TOKEN,
        resolver=resolver if resolver is not None else _StaticResolver("127.0.0.1"),
        connector=connector if connector is not None else _ExplodingConnector(),
    )
    results: list[ProxyResult] = []
    worker = threading.Thread(target=lambda: results.append(proxy.serve_once()))
    worker.start()
    client = socket.create_connection(("127.0.0.1", manifest.bind_port), timeout=2)
    try:
        client.sendall(raw_request)
        client.shutdown(socket.SHUT_WR)
        response = client.recv(4096)
    finally:
        client.close()
    worker.join(2)
    assert not worker.is_alive()
    return response, results[0], proxy


def test_proxy_manifest_is_strict_canonical_and_hash_bound() -> None:
    manifest = _manifest()

    assert manifest.manifest_hash == (
        "sha256:d31aa2078742d704be2dacfdae1056d9af8b254842375d7827e0108c9dba71a0"
    )
    assert manifest.listener_mode == "BROKER_PREBOUND_INHERITED"
    assert manifest.deadline_milliseconds == 900_000
    assert manifest.authorization_mode == "BASIC_USERINFO_ENV_ONLY"
    assert manifest.authorization_username_sha256 == (
        "sha256:5f27b95a2a488a37d08dd32e7ea7977a39d791e29aa7911b1a74038f6f076827"
    )
    assert manifest.token_sha256 == (
        "sha256:52174e6d68471b6768d4d0d88a552bb083b49d341462d47ee0adc434ea9d5271"
    )
    assert manifest.to_json_bytes() == GOLDEN_MANIFEST_JSON
    assert TOKEN not in GOLDEN_MANIFEST_JSON
    assert ProxyManifest.from_json_bytes(manifest.to_json_bytes()) == manifest

    decoded = json.loads(manifest.to_json_bytes())
    decoded["unexpected"] = True
    with pytest.raises(ValueError, match="fields are not exact"):
        ProxyManifest.from_json_bytes(
            json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )

    with pytest.raises(ValueError, match="canonical JSON"):
        ProxyManifest.from_json_bytes(b" " + manifest.to_json_bytes())

    with pytest.raises(ValueError, match="frozen audience"):
        ProxyManifest.create(
            bind_port=43117,
            connect_allowlist=("evil.chatgpt.com:443",),
            authorization_token=TOKEN,
        )


def test_sealed_proxy_url_encodes_basic_userinfo_contract_and_is_redacted() -> None:
    manifest = _manifest()
    with pytest.raises(ValueError, match="format"):
        SealedProxyUrl.create(
            manifest,
            authorization_token="not-bytes",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="does not match"):
        SealedProxyUrl.create(manifest, authorization_token=b"B" * 32)
    sealed_url = SealedProxyUrl.create(manifest, authorization_token=TOKEN)
    secret_value = sealed_url.consume_for_https_proxy_env()
    parsed = urlsplit(secret_value)

    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == manifest.bind_port
    assert parsed.username == PROXY_USERNAME
    assert parsed.password == TOKEN.decode("ascii")
    assert CODEX_0147_PROXY_AUTH_TRACE_REQUIRED == "CODEX_0147_PROXY_AUTH_TRACE_REQUIRED"
    reqwest_equivalent = b"Basic " + base64.b64encode(
        parsed.username.encode("ascii") + b":" + parsed.password.encode("ascii")
    )
    assert reqwest_equivalent == AUTHORIZATION
    assert manifest.bind_endpoint_sha256 == (
        "sha256:af4873d2ce02ac6018b9b5b21d868f12e293635af76f82173c23e34cb32ffc48"
    )

    redacted_values = (str(sealed_url), repr(sealed_url), json.dumps(sealed_url.to_audit_dict()))
    for value in redacted_values:
        assert PROXY_USERNAME not in value
        assert TOKEN.decode("ascii") not in value
        assert "@" not in value
    assert str(sealed_url) == "http://127.0.0.1:43117"
    assert sealed_url.to_audit_dict() == {
        "manifest_hash": manifest.manifest_hash,
        "bind_endpoint_sha256": manifest.bind_endpoint_sha256,
    }
    with pytest.raises(TypeError):
        asdict(cast(Any, sealed_url))
    with pytest.raises(TypeError):
        vars(sealed_url)
    with pytest.raises(RuntimeError, match="already consumed"):
        sealed_url.consume_for_https_proxy_env()

    different_port = ProxyManifest.create(
        bind_port=43118,
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
    )
    assert different_port.manifest_hash != manifest.manifest_hash
    assert different_port.bind_endpoint_sha256 != manifest.bind_endpoint_sha256


def test_readiness_challenge_binds_live_listener_without_consuming_data_connection() -> None:
    manifest = ProxyManifest.create(
        bind_port=_free_loopback_port(),
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
    )
    proxy = _new_proxy(manifest)
    challenge = b"C" * 32
    readiness = proxy.challenge_readiness(challenge)
    control = readiness.to_control_dict()

    assert ProxyReadiness.verify(
        readiness,
        manifest,
        authorization_token=TOKEN,
        challenge=challenge,
        expected_process_id=readiness.process_id,
        expected_listener_handle=readiness.listener_handle,
    )
    assert not ProxyReadiness.verify(
        readiness,
        manifest,
        authorization_token=b"B" * 32,
        challenge=challenge,
        expected_process_id=readiness.process_id,
        expected_listener_handle=readiness.listener_handle,
    )
    assert not ProxyReadiness.verify(
        readiness,
        manifest,
        authorization_token=TOKEN,
        challenge=b"D" * 32,
        expected_process_id=readiness.process_id,
        expected_listener_handle=readiness.listener_handle,
    )
    forged = replace(readiness, proof_hmac_sha256="hmac-sha256:" + "0" * 64)
    assert not ProxyReadiness.verify(
        forged,
        manifest,
        authorization_token=TOKEN,
        challenge=challenge,
        expected_process_id=readiness.process_id,
        expected_listener_handle=readiness.listener_handle,
    )
    assert control["process_id"] == readiness.process_id
    assert control["listener_handle"] == readiness.listener_handle
    audit_text = json.dumps(readiness.to_audit_dict())
    assert TOKEN.decode("ascii") not in audit_text
    assert challenge.decode("ascii") not in audit_text
    assert "listener_handle" not in readiness.to_audit_dict()

    results: list[ProxyResult] = []
    worker = threading.Thread(target=lambda: results.append(proxy.serve_once()))
    worker.start()
    client = socket.create_connection(("127.0.0.1", manifest.bind_port), timeout=2)
    try:
        client.sendall(
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
            b"Host: chatgpt.com:443\r\n"
            b"Proxy-Authorization: Basic rejected\r\n\r\n"
        )
        assert client.recv(4096).startswith(b"HTTP/1.1 407")
    finally:
        client.close()
    worker.join(2)
    assert results[0].reason_code == "AUTH_REJECTED"
    with pytest.raises(RuntimeError, match="already been consumed"):
        proxy.serve_once()
    with pytest.raises(RuntimeError, match="already been consumed"):
        proxy.challenge_readiness(challenge)


def test_readiness_has_fixed_canonical_python_golden_vector() -> None:
    manifest = _manifest()
    challenge = b"C" * 32
    readiness = ProxyReadiness.create(
        manifest,
        authorization_token=TOKEN,
        challenge=challenge,
        process_id=4242,
        listener_handle=31337,
    )

    assert readiness.to_control_json_bytes() == GOLDEN_READINESS_CONTROL_JSON
    assert readiness.to_audit_json_bytes() == GOLDEN_READINESS_AUDIT_JSON
    assert ProxyReadiness.from_control_json_bytes(GOLDEN_READINESS_CONTROL_JSON) == readiness
    assert ProxyReadiness.verify(
        readiness,
        manifest,
        authorization_token=TOKEN,
        challenge=challenge,
        expected_process_id=4242,
        expected_listener_handle=31337,
    )
    for serialized in (
        GOLDEN_MANIFEST_JSON,
        GOLDEN_READINESS_CONTROL_JSON,
        GOLDEN_READINESS_AUDIT_JSON,
    ):
        assert TOKEN not in serialized
        assert challenge not in serialized
        assert PROXY_USERNAME.encode("ascii") not in serialized
        assert b"@" not in serialized
    audit = json.loads(GOLDEN_READINESS_AUDIT_JSON)
    assert "challenge_sha256" not in audit
    assert "process_id" not in audit
    assert "listener_handle" not in audit


def test_python_verifies_independently_generated_rust_proxy_vector() -> None:
    manifest = ProxyManifest.from_json_bytes(RUST_VECTOR_MANIFEST_JSON)
    readiness = ProxyReadiness.from_control_json_bytes(RUST_VECTOR_READINESS_JSON)

    assert manifest.bind_port == 54219
    assert manifest.connect_allowlist == ("api.openai.com:443",)
    assert ProxyReadiness.verify(
        readiness,
        manifest,
        authorization_token=RUST_VECTOR_TOKEN,
        challenge=b"R" * 32,
        expected_process_id=7331,
        expected_listener_handle=424242,
    )


def test_readiness_parser_rejects_noncanonical_unknown_duplicate_and_invalid_fields() -> None:
    with pytest.raises(ValueError, match="canonical JSON"):
        ProxyReadiness.from_control_json_bytes(b" " + GOLDEN_READINESS_CONTROL_JSON)
    with pytest.raises(ValueError, match="duplicate fields"):
        ProxyReadiness.from_control_json_bytes(
            b'{"bind_endpoint_sha256":"sha256:'
            + b"0" * 64
            + b'",'
            + GOLDEN_READINESS_CONTROL_JSON[1:]
        )
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        ProxyReadiness.from_control_json_bytes("not bytes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="valid UTF-8 JSON"):
        ProxyReadiness.from_control_json_bytes(b"\xff")

    decoded = json.loads(GOLDEN_READINESS_CONTROL_JSON)
    decoded["unexpected"] = True
    with pytest.raises(ValueError, match="fields are not exact"):
        ProxyReadiness.from_control_json_bytes(
            json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()
        )
    decoded.pop("unexpected")
    decoded["process_id"] = True
    with pytest.raises(ValueError, match="process id"):
        ProxyReadiness.from_control_json_bytes(
            json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()
        )


def test_readiness_factory_and_verifier_reject_every_unbound_control_identity() -> None:
    manifest = _manifest()
    readiness = ProxyReadiness.create(
        manifest,
        authorization_token=TOKEN,
        challenge=b"C" * 32,
        process_id=4242,
        listener_handle=31337,
    )
    with pytest.raises(ValueError, match="does not match"):
        ProxyReadiness.create(
            manifest,
            authorization_token=b"B" * 32,
            challenge=b"C" * 32,
            process_id=4242,
            listener_handle=31337,
        )
    with pytest.raises(ValueError, match="32 bytes"):
        ProxyReadiness.create(
            manifest,
            authorization_token=TOKEN,
            challenge=b"short",
            process_id=4242,
            listener_handle=31337,
        )

    invalid_controls = [
        (replace(readiness, schema_version="wrong"), "schema"),
        (replace(readiness, manifest_hash="sha256:" + "A" * 64), "hash"),
        (replace(readiness, listener_handle=True), "listener handle"),
        (replace(readiness, proof_hmac_sha256="hmac-sha256:" + "A" * 64), "HMAC"),
        (replace(readiness, listener_identity_sha256="sha256:" + "0" * 64), "identity"),
    ]
    for invalid, message in invalid_controls:
        with pytest.raises(ValueError, match=message):
            invalid.validate_control()

    verify_arguments: dict[str, Any] = {
        "authorization_token": TOKEN,
        "challenge": b"C" * 32,
        "expected_process_id": 4242,
        "expected_listener_handle": 31337,
    }
    assert not ProxyReadiness.verify(cast(Any, object()), manifest, **verify_arguments)
    assert not ProxyReadiness.verify(
        replace(readiness, schema_version="wrong"), manifest, **verify_arguments
    )


def test_readiness_rejects_invalid_nonce_and_closed_listener() -> None:
    manifest = ProxyManifest.create(
        bind_port=_free_loopback_port(),
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
    )
    proxy = _new_proxy(manifest)
    with pytest.raises(ValueError, match="32 bytes"):
        proxy.challenge_readiness(b"short")
    proxy.close()
    with pytest.raises(RuntimeError, match="not live"):
        proxy.challenge_readiness(b"C" * 32)


@pytest.mark.parametrize(
    ("process_id", "listener_handle", "message"),
    [
        (0, 1, "process id"),
        (1 << 32, 1, "process id"),
        (True, 1, "process id"),
        (1, -1, "listener handle"),
        (1, 1 << 64, "listener handle"),
        (1, True, "listener handle"),
    ],
)
def test_readiness_factory_rejects_non_windows_identity_ranges(
    process_id: int, listener_handle: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ProxyReadiness.create(
            _manifest(),
            authorization_token=TOKEN,
            challenge=b"C" * 32,
            process_id=process_id,
            listener_handle=listener_handle,
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema_version": "wrong"}, "identity"),
        ({"bind_host": "0.0.0.0"}, "identity"),
        ({"bind_port": 0}, "bind port"),
        ({"bind_port": True}, "bind port"),
        ({"connect_allowlist": ()}, "frozen audience"),
        (
            {"connect_allowlist": ("chatgpt.com:443", "chatgpt.com:443")},
            "frozen audience",
        ),
        ({"token_sha256": "sha256:" + "A" * 64}, "token hash"),
        ({"authorization_mode": "BEARER"}, "authorization identity"),
        ({"listener_mode": "SELF_BOUND"}, "listener mode"),
        ({"max_header_bytes": 1000}, "header limit"),
        ({"max_client_hello_bytes": 100}, "ClientHello limit"),
        ({"max_tunnel_bytes": 100}, "tunnel limit"),
        ({"deadline_milliseconds": 100}, "deadline"),
        ({"deadline_milliseconds": 900_001}, "deadline"),
        ({"max_header_bytes": True}, "integers"),
        ({"max_connections": 2}, "exactly one"),
        ({"manifest_hash": "sha256:" + "A" * 64}, "hash is invalid"),
        ({"manifest_hash": "sha256:" + "0" * 64}, "hash binding"),
    ],
)
def test_proxy_manifest_rejects_each_mutated_security_field(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(_manifest(), **cast(Any, changes)).validate()


@pytest.mark.parametrize("token", [b"short", b"A" * 31, b"A" * 129, b"A" * 31 + b" "])
def test_proxy_manifest_rejects_unusable_authorization_tokens(token: bytes) -> None:
    with pytest.raises(ValueError, match="authorization token"):
        ProxyManifest.create(
            bind_port=43117,
            connect_allowlist=("chatgpt.com:443",),
            authorization_token=token,
        )


def test_proxy_manifest_parser_rejects_duplicate_invalid_and_non_array_json() -> None:
    raw = _manifest().to_json_bytes()
    duplicated = b'{"bind_host":"127.0.0.1",' + raw[1:]
    with pytest.raises(ValueError, match="duplicate fields"):
        ProxyManifest.from_json_bytes(duplicated)
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        ProxyManifest.from_json_bytes("not bytes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="valid UTF-8 JSON"):
        ProxyManifest.from_json_bytes(b"\xff")
    with pytest.raises(ValueError, match="valid UTF-8 JSON"):
        ProxyManifest.from_json_bytes(b"{")

    decoded = json.loads(raw)
    decoded["connect_allowlist"] = "chatgpt.com:443"
    with pytest.raises(ValueError, match="allowlist must be an array"):
        ProxyManifest.from_json_bytes(
            json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()
        )
    decoded["connect_allowlist"] = [{}]
    with pytest.raises(ValueError, match="frozen audience"):
        ProxyManifest.from_json_bytes(
            json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()
        )


@pytest.mark.parametrize(
    ("raw_request", "status", "reason"),
    [
        (
            b"GET chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n\r\n",
            b"405",
            "METHOD_REJECTED",
        ),
        (
            b"CONNECT evil.chatgpt.com:443 HTTP/1.1\r\nHost: evil.chatgpt.com:443\r\n\r\n",
            b"403",
            "AUTHORITY_REJECTED",
        ),
        (
            b"CONNECT chatgpt.com:444 HTTP/1.1\r\nHost: chatgpt.com:444\r\n\r\n",
            b"403",
            "AUTHORITY_REJECTED",
        ),
        (
            b"CONNECT CHATGPT.com:443 HTTP/1.1\r\nHost: CHATGPT.com:443\r\n\r\n",
            b"403",
            "AUTHORITY_REJECTED",
        ),
        (
            b"CONNECT chatgpt.com:443 HTTP/1.0\r\nHost: chatgpt.com:443\r\n\r\n",
            b"400",
            "HTTP_VERSION_REJECTED",
        ),
        (
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: api.openai.com:443\r\n\r\n",
            b"400",
            "HOST_MISMATCH",
        ),
        (
            b"CONNECT  chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n\r\n",
            b"400",
            "REQUEST_LINE_INVALID",
        ),
        (
            b"CONNECT \xff:443 HTTP/1.1\r\nHost: \xff:443\r\n\r\n",
            b"400",
            "AUTHORITY_INVALID",
        ),
        (
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
            b"Host: chatgpt.com:443\r\nHost: chatgpt.com:443\r\n\r\n",
            b"400",
            "HEADER_SYNTAX_INVALID",
        ),
        (
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
            b"Host: chatgpt.com:443\r\n Content: folded\r\n\r\n",
            b"400",
            "HEADER_SYNTAX_INVALID",
        ),
        (
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
            b"Host: chatgpt.com:443\r\nContent-Length: 1\r\n\r\n",
            b"400",
            "REQUEST_BODY_REJECTED",
        ),
    ],
)
def test_proxy_rejects_non_connect_authority_and_header_smuggling(
    raw_request: bytes, status: bytes, reason: str
) -> None:
    response, result, _proxy = _raw_exchange(raw_request)

    assert response.startswith(b"HTTP/1.1 " + status)
    assert result.reason_code == reason


def test_proxy_rejects_early_tunnel_bytes_and_oversized_headers() -> None:
    valid = (
        b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
        b"Host: chatgpt.com:443\r\n"
        b"Proxy-Authorization: " + AUTHORIZATION + b"\r\n\r\n"
    )
    response, result, _proxy = _raw_exchange(valid + b"early-tls")
    assert response.startswith(b"HTTP/1.1 400")
    assert result.reason_code == "EARLY_TUNNEL_BYTES_REJECTED"

    oversized = b"X" * 1025
    response, result, _proxy = _raw_exchange(oversized, max_header_bytes=1024)
    assert response.startswith(b"HTTP/1.1 400")
    assert result.reason_code == "HEADERS_TOO_LARGE"


def test_proxy_rejects_truncated_headers_and_excess_header_count() -> None:
    response, result, _proxy = _raw_exchange(
        b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n"
    )
    assert response.startswith(b"HTTP/1.1 400")
    assert result.reason_code == "HEADERS_TRUNCATED"

    too_many = (
        b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
        + b"".join(f"X-{index}: value\r\n".encode() for index in range(33))
        + b"\r\n"
    )
    response, result, _proxy = _raw_exchange(too_many)
    assert response.startswith(b"HTTP/1.1 400")
    assert result.reason_code == "HEADER_COUNT_INVALID"


def test_proxy_rejects_resolver_connector_and_peer_failures() -> None:
    valid = (
        b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
        b"Host: chatgpt.com:443\r\n"
        b"Proxy-Authorization: " + AUTHORIZATION + b"\r\n\r\n"
    )
    response, result, _proxy = _raw_exchange(
        valid,
        resolver=_FailingResolver(),
        connector=_ExplodingConnector(),
    )
    assert response.startswith(b"HTTP/1.1 502")
    assert result.reason_code == "DNS_RESOLUTION_FAILED"

    response, result, _proxy = _raw_exchange(
        valid,
        resolver=_ImmediateTimeoutResolver(),
        connector=_ExplodingConnector(),
    )
    assert response.startswith(b"HTTP/1.1 504")
    assert result.reason_code == "DNS_TIMEOUT"

    response, result, _proxy = _raw_exchange(
        valid,
        resolver=_StaticResolver("8.8.8.8"),
        connector=_FailingConnector(),
    )
    assert response.startswith(b"HTTP/1.1 502")
    assert result.reason_code == "UPSTREAM_CONNECT_FAILED"

    for peer, expected in [
        ("1.1.1.1", "PEER_MISMATCH"),
        ("127.0.0.1", "PEER_ADDRESS_REJECTED"),
    ]:
        upstream_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        upstream_listener.bind(("127.0.0.1", 0))
        upstream_listener.listen(1)
        try:
            response, result, _proxy = _raw_exchange(
                valid,
                resolver=_SequenceResolver(("8.8.8.8",), ("8.8.8.8",)),
                connector=_LocalConnector(int(upstream_listener.getsockname()[1]), peer),
            )
        finally:
            upstream_listener.close()
        assert response.startswith(b"HTTP/1.1 403")
        assert result.reason_code == expected

    response, result, _proxy = _raw_exchange(
        valid,
        resolver=_StaticResolver("8.8.8.8"),
        connector=_CloseFailingConnector(),
    )
    assert response.startswith(b"HTTP/1.1 403")
    assert result.reason_code == "PEER_ADDRESS_REJECTED"


@pytest.mark.parametrize(
    "answers",
    [
        (),
        tuple("8.8.8.8" for _ in range(17)),
        ("8.8.8.8", "10.0.0.1"),
        ("not-an-ip",),
        ("fe80::1%3",),
        ("::ffff:8.8.8.8",),
    ],
)
def test_proxy_rejects_invalid_or_mixed_dns_answer_sets(answers: tuple[str, ...]) -> None:
    valid = (
        b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
        b"Host: chatgpt.com:443\r\n"
        b"Proxy-Authorization: " + AUTHORIZATION + b"\r\n\r\n"
    )
    response, result, _proxy = _raw_exchange(
        valid,
        resolver=_StaticResolver(*answers),
        connector=_ExplodingConnector(),
    )

    assert response.startswith(b"HTTP/1.1 403")
    assert result.reason_code in {"DNS_ANSWER_SET_INVALID", "DNS_ADDRESS_REJECTED"}


def test_system_resolver_and_numeric_connector_use_local_socket_without_proxy() -> None:
    answers = SystemResolver().resolve("localhost", 443, time.monotonic() + 1)
    assert answers
    assert all(isinstance(answer, str) for answer in answers)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    connector = NumericSocketConnector()
    connected = connector.connect("127.0.0.1", int(listener.getsockname()[1]), 1.0)
    accepted, _address = listener.accept()
    try:
        assert connected.peer_ip == "127.0.0.1"
    finally:
        connected.socket.close()
        accepted.close()
        listener.close()

    unused_port = _free_loopback_port()
    with pytest.raises(OSError):
        connector.connect("127.0.0.1", unused_port, 0.1)


def test_system_resolver_checks_deadline_before_and_after_direct_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = SystemResolver()
    with pytest.raises(TimeoutError, match="deadline expired"):
        resolver.resolve("chatgpt.com", 443, time.monotonic() - 1)

    with monkeypatch.context() as scoped:
        ticks = iter((1.0, 3.0))
        scoped.setattr(time, "monotonic", lambda: next(ticks))
        scoped.setattr(
            socket,
            "getaddrinfo",
            lambda *_args, **_kwargs: (
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("8.8.8.8", 443),
                ),
            ),
        )
        with pytest.raises(TimeoutError, match="deadline expired"):
            resolver.resolve("chatgpt.com", 443, 2.0)


def test_proxy_bind_token_and_one_shot_lifecycle_are_fail_closed() -> None:
    manifest = ProxyManifest.create(
        bind_port=_free_loopback_port(),
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
        deadline_milliseconds=1000,
    )
    with pytest.raises(ValueError, match="format"):
        _new_proxy(manifest, authorization_token=b"short")
    with pytest.raises(ValueError, match="format"):
        _new_proxy(manifest, authorization_token=cast(Any, "not-bytes"))
    with pytest.raises(ValueError, match="does not match"):
        _new_proxy(manifest, authorization_token=b"B" * 32)

    first = _new_proxy(manifest)
    with pytest.raises(OSError):
        _new_proxy(manifest)
    timeout_result = first.serve_once()
    assert timeout_result.terminal is ProxyTerminal.TIMEOUT
    assert timeout_result.accepted_connections == 0
    assert timeout_result.rejected_connections == 0
    with pytest.raises(RuntimeError, match="already been consumed"):
        first.serve_once()

    second_manifest = ProxyManifest.create(
        bind_port=_free_loopback_port(),
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
    )
    closed = _new_proxy(second_manifest)
    closed.close()
    assert closed.serve_once().terminal is ProxyTerminal.INTERNAL_ERROR


def test_proxy_accepts_only_broker_prebound_listener_matching_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = ProxyManifest.create(
        bind_port=_free_loopback_port(),
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
    )
    wrong_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    wrong_listener.bind(("127.0.0.1", 0))
    wrong_listener.listen(1)
    try:
        with pytest.raises(ValueError, match="does not match manifest"):
            OneShotConnectProxy(
                manifest,
                authorization_token=TOKEN,
                prebound_listener=wrong_listener,
            )
    finally:
        wrong_listener.close()

    not_listening = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    not_listening.bind((manifest.bind_host, manifest.bind_port))
    try:
        with pytest.raises(ValueError, match="does not match manifest"):
            OneShotConnectProxy(
                manifest,
                authorization_token=TOKEN,
                prebound_listener=not_listening,
            )
    finally:
        not_listening.close()

    inherited_listener = _prebound_listener(manifest)

    def unexpected_socket_creation(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("proxy attempted to self-bind instead of using broker listener")

    monkeypatch.setattr(socket, "socket", unexpected_socket_creation)
    inherited_proxy = OneShotConnectProxy(
        manifest,
        authorization_token=TOKEN,
        prebound_listener=inherited_listener,
    )
    inherited_proxy.close()


def test_broken_client_cannot_replace_original_rejection_receipt() -> None:
    manifest = ProxyManifest.create(
        bind_port=_free_loopback_port(),
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
    )
    resolver = _BlockingResolver("127.0.0.1")
    proxy = _new_proxy(
        manifest,
        resolver=resolver,
        connector=_ExplodingConnector(),
    )
    results: list[ProxyResult] = []
    worker = threading.Thread(target=lambda: results.append(proxy.serve_once()))
    worker.start()
    client = socket.create_connection(("127.0.0.1", manifest.bind_port), timeout=2)
    client.sendall(
        b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
        b"Host: chatgpt.com:443\r\n"
        b"Proxy-Authorization: " + AUTHORIZATION + b"\r\n\r\n"
    )
    assert resolver.started.wait(1)
    client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0))
    client.close()
    resolver.release.set()
    worker.join(2)

    assert not worker.is_alive()
    assert results[0].terminal is ProxyTerminal.REJECTED
    assert results[0].reason_code == "DNS_ADDRESS_REJECTED"


def test_falsy_resolver_and_connector_are_not_replaced_by_real_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid = (
        b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
        b"Host: chatgpt.com:443\r\n"
        b"Proxy-Authorization: " + AUTHORIZATION + b"\r\n\r\n"
    )

    def unexpected_dns(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        raise AssertionError("falsy resolver was replaced")

    monkeypatch.setattr(SystemResolver, "resolve", unexpected_dns)
    response, result, _proxy = _raw_exchange(
        valid,
        resolver=_FalsyResolver("127.0.0.1"),
        connector=_ExplodingConnector(),
    )
    assert response.startswith(b"HTTP/1.1 403")
    assert result.reason_code == "DNS_ADDRESS_REJECTED"

    def unexpected_connect(
        _self: object, _ip: str, _port: int, _timeout: float
    ) -> ConnectedUpstream:
        raise AssertionError("falsy connector was replaced")

    monkeypatch.setattr(NumericSocketConnector, "connect", unexpected_connect)
    response, result, _proxy = _raw_exchange(
        valid,
        resolver=_StaticResolver("8.8.8.8"),
        connector=_FalsyConnector(),
    )
    assert response.startswith(b"HTTP/1.1 502")
    assert result.reason_code == "UPSTREAM_CONNECT_FAILED"


def test_blocking_dns_obeys_shared_deadline_without_upstream_or_secret_leak() -> None:
    resolver = _BlockingResolver()
    manifest = ProxyManifest.create(
        bind_port=_free_loopback_port(),
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
        deadline_milliseconds=1000,
    )
    proxy = _new_proxy(
        manifest,
        resolver=resolver,
        connector=_ExplodingConnector(),
    )
    results: list[ProxyResult] = []
    started = time.monotonic()
    worker = threading.Thread(target=lambda: results.append(proxy.serve_once()))
    worker.start()
    client = socket.create_connection(("127.0.0.1", manifest.bind_port), timeout=2)
    try:
        client.sendall(
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
            b"Host: chatgpt.com:443\r\n"
            b"Proxy-Authorization: " + AUTHORIZATION + b"\r\n\r\n"
        )
        assert resolver.started.wait(1)
        assert client.recv(4096) == b""
    finally:
        client.close()
        resolver.release.set()
    worker.join(2)

    assert time.monotonic() - started < 1.5
    assert results[0].terminal is ProxyTerminal.TIMEOUT
    assert results[0].reason_code == "DNS_TIMEOUT"
    assert TOKEN.decode("ascii") not in json.dumps(results[0].to_audit_dict())


@pytest.mark.parametrize(
    ("hello", "reason"),
    [
        (_minimal_client_hello((b"api.openai.com",)), "SNI_MISMATCH"),
        (
            _minimal_client_hello(
                None,
                extra_extensions=((43, b"\x02\x03\x04"),),
            ),
            "SNI_COUNT_INVALID",
        ),
        (
            _minimal_client_hello((b"chatgpt.com", b"api.openai.com")),
            "SNI_COUNT_INVALID",
        ),
        (
            _minimal_client_hello(
                (b"chatgpt.com",),
                extra_extensions=((0xFE0D, b"\x00"),),
            ),
            "ECH_REJECTED",
        ),
        (
            _minimal_client_hello(
                (b"chatgpt.com",),
                extra_extensions=((0xFFCE, b"\x00"),),
            ),
            "ECH_REJECTED",
        ),
        (
            _minimal_client_hello(
                (b"chatgpt.com",),
                extra_extensions=((0, b"\x00\x00"),),
            ),
            "TLS_EXTENSIONS_MALFORMED",
        ),
        (_minimal_client_hello((b"CHATGPT.COM",)), "SNI_MALFORMED"),
        (
            b"\x17" + _minimal_client_hello((b"chatgpt.com",))[1:],
            "TLS_HANDSHAKE_RECORD_REQUIRED",
        ),
        (
            _minimal_client_hello((b"chatgpt.com",))[:1]
            + b"\x00\x00"
            + _minimal_client_hello((b"chatgpt.com",))[3:],
            "TLS_RECORD_VERSION_INVALID",
        ),
        (
            _minimal_client_hello((b"chatgpt.com",))[:5]
            + b"\x02"
            + _minimal_client_hello((b"chatgpt.com",))[6:],
            "TLS_CLIENT_HELLO_REQUIRED",
        ),
        (b"\x16\x03\x01\x00\x00", "TLS_RECORD_LENGTH_INVALID"),
    ],
)
def test_proxy_never_forwards_invalid_sni_ech_or_tls_bytes(hello: bytes, reason: str) -> None:
    response, result, upstream_received = _tls_exchange(hello)

    assert response == b"HTTP/1.1 200 Connection Established\r\n\r\n"
    assert result.terminal is ProxyTerminal.REJECTED
    assert result.reason_code == reason
    assert result.client_to_upstream_bytes == 0
    assert result.sni_sha256 is None
    assert upstream_received == b""


def test_proxy_accepts_fragmented_client_hello_but_enforces_total_tunnel_bytes() -> None:
    fragmented = _minimal_client_hello((b"chatgpt.com",), fragment_at=11)
    response, result, upstream_received = _tls_exchange(fragmented)
    assert response.startswith(b"HTTP/1.1 200")
    assert result.terminal is ProxyTerminal.COMPLETED
    assert upstream_received == fragmented

    hello = _minimal_client_hello((b"chatgpt.com",))
    overflow = b"X" * (1024 - len(hello) + 1)
    response, result, upstream_received = _tls_exchange(
        hello,
        extra_client_bytes=overflow,
        max_tunnel_bytes=1024,
    )
    assert response.startswith(b"HTTP/1.1 200")
    assert result.terminal is ProxyTerminal.REJECTED
    assert result.reason_code == "TUNNEL_BYTES_EXCEEDED"
    assert upstream_received == hello


def test_proxy_rejects_oversized_and_trailing_client_hello_before_forwarding() -> None:
    declared_oversized = b"\x16\x03\x01\x00\x04\x01\x00\x10\x00"
    _response, result, upstream_received = _tls_exchange(
        declared_oversized,
        max_client_hello_bytes=512,
    )
    assert result.reason_code == "CLIENT_HELLO_TOO_LARGE"
    assert upstream_received == b""

    oversized_record = b"\x16\x03\x01" + (513).to_bytes(2, "big") + b"\x00" * 513
    _response, result, upstream_received = _tls_exchange(
        oversized_record,
        max_client_hello_bytes=512,
    )
    assert result.reason_code == "CLIENT_HELLO_TOO_LARGE"
    assert upstream_received == b""

    truncated_record = b"\x16\x03\x01\x00\x05\x01"
    _response, result, upstream_received = _tls_exchange(truncated_record)
    assert result.reason_code == "TLS_CLIENT_HELLO_TRUNCATED"
    assert upstream_received == b""

    hello = _minimal_client_hello((b"chatgpt.com",))
    original_length = int.from_bytes(hello[3:5], "big")
    trailing = hello[:3] + (original_length + 1).to_bytes(2, "big") + hello[5:] + b"\x00"
    _response, result, upstream_received = _tls_exchange(trailing)
    assert result.reason_code == "TLS_CLIENT_HELLO_TRAILING_DATA"
    assert upstream_received == b""

    padded = _minimal_client_hello(
        (b"chatgpt.com",),
        extra_extensions=((21, b"\x00" * 1024),),
    )
    _response, result, upstream_received = _tls_exchange(
        padded,
        max_tunnel_bytes=1024,
    )
    assert result.reason_code == "TUNNEL_BYTES_EXCEEDED"
    assert upstream_received == b""


def test_one_shot_proxy_rejects_bad_token_without_disclosing_request() -> None:
    manifest = ProxyManifest.create(
        bind_port=_free_loopback_port(),
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
    )
    proxy = _new_proxy(manifest)
    result: list[ProxyResult] = []
    worker = threading.Thread(target=lambda: result.append(proxy.serve_once()))
    worker.start()
    client = socket.create_connection(("127.0.0.1", manifest.bind_port), timeout=2)
    try:
        client.sendall(
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
            b"Host: chatgpt.com:443\r\n"
            b"Proxy-Authorization: Basic not-the-run-token\r\n\r\n"
        )
        response = client.recv(4096)
    finally:
        client.close()
    worker.join(2)

    assert not worker.is_alive()
    assert response.startswith(b"HTTP/1.1 407 Proxy Authentication Required\r\n")
    audit = result[0].to_audit_dict()
    assert audit["terminal"] == ProxyTerminal.REJECTED.value
    assert audit["reason_code"] == "AUTH_REJECTED"
    assert audit["accepted_connections"] == 1
    assert "not-the-run-token" not in repr(result[0])
    assert "not-the-run-token" not in json.dumps(audit)


@pytest.mark.parametrize(
    "answer",
    ["127.0.0.1", "10.0.0.8", "169.254.1.2", "192.0.2.8", "::1", "fe80::1"],
)
def test_proxy_rejects_every_non_global_dns_answer_before_connect(answer: str) -> None:
    manifest = ProxyManifest.create(
        bind_port=_free_loopback_port(),
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
    )
    proxy = _new_proxy(
        manifest,
        authorization_token=TOKEN,
        resolver=_StaticResolver(answer),
        connector=_ExplodingConnector(),
    )
    result: list[ProxyResult] = []
    worker = threading.Thread(target=lambda: result.append(proxy.serve_once()))
    worker.start()
    client = socket.create_connection(("127.0.0.1", manifest.bind_port), timeout=2)
    try:
        client.sendall(
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
            b"Host: chatgpt.com:443\r\n"
            b"Proxy-Authorization: " + AUTHORIZATION + b"\r\n\r\n"
        )
        response = client.recv(4096)
    finally:
        client.close()
    worker.join(2)

    assert response.startswith(b"HTTP/1.1 403 Forbidden\r\n")
    audit = result[0].to_audit_dict()
    assert audit["reason_code"] == "DNS_ADDRESS_REJECTED"
    assert audit["peer_sha256"] is None


def test_proxy_rejects_dns_rebinding_after_numeric_connect() -> None:
    upstream_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream_listener.bind(("127.0.0.1", 0))
    upstream_listener.listen(1)
    manifest = ProxyManifest.create(
        bind_port=_free_loopback_port(),
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
    )
    proxy = _new_proxy(
        manifest,
        authorization_token=TOKEN,
        resolver=_SequenceResolver(("8.8.8.8",), ("1.1.1.1",)),
        connector=_LocalConnector(int(upstream_listener.getsockname()[1]), "8.8.8.8"),
    )
    result: list[ProxyResult] = []
    worker = threading.Thread(target=lambda: result.append(proxy.serve_once()))
    worker.start()
    client = socket.create_connection(("127.0.0.1", manifest.bind_port), timeout=2)
    try:
        client.sendall(
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
            b"Host: chatgpt.com:443\r\n"
            b"Proxy-Authorization: " + AUTHORIZATION + b"\r\n\r\n"
        )
        response = client.recv(4096)
    finally:
        client.close()
        upstream_listener.close()
    worker.join(2)

    assert response.startswith(b"HTTP/1.1 403 Forbidden\r\n")
    audit = result[0].to_audit_dict()
    assert audit["reason_code"] == "DNS_REBINDING_REJECTED"
    assert audit["dns_answer_set_sha256"] == (
        "sha256:460a04f42b55e33be24421a368d8044651fda06f9526b930d680a392de95de0a"
    )
    assert audit["dns_recheck_answer_set_sha256"] == (
        "sha256:fed6626955584d44ee31af537ee46e318e52711245ebb82759294cae44f329d7"
    )


def test_proxy_relays_only_after_real_client_hello_sni_is_validated() -> None:
    hello = _client_hello("chatgpt.com")
    upstream_reply = b"bounded-server-flight"
    upstream_received = bytearray()
    upstream_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream_listener.bind(("127.0.0.1", 0))
    upstream_listener.listen(1)

    def serve_upstream() -> None:
        connection, _address = upstream_listener.accept()
        try:
            while len(upstream_received) < len(hello):
                chunk = connection.recv(len(hello) - len(upstream_received))
                if not chunk:
                    break
                upstream_received.extend(chunk)
            connection.sendall(upstream_reply)
        finally:
            connection.close()
            upstream_listener.close()

    upstream_worker = threading.Thread(target=serve_upstream)
    upstream_worker.start()
    manifest = ProxyManifest.create(
        bind_port=_free_loopback_port(),
        connect_allowlist=("chatgpt.com:443",),
        authorization_token=TOKEN,
    )
    proxy = _new_proxy(
        manifest,
        authorization_token=TOKEN,
        resolver=_SequenceResolver(("8.8.8.8",), ("8.8.8.8",)),
        connector=_LocalConnector(int(upstream_listener.getsockname()[1]), "8.8.8.8"),
    )
    results: list[ProxyResult] = []
    proxy_worker = threading.Thread(target=lambda: results.append(proxy.serve_once()))
    proxy_worker.start()
    client = socket.create_connection(("127.0.0.1", manifest.bind_port), timeout=2)
    try:
        client.sendall(
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\n"
            b"Host: chatgpt.com:443\r\n"
            b"Proxy-Authorization: " + AUTHORIZATION + b"\r\n\r\n"
        )
        response = client.recv(4096)
        assert response == b"HTTP/1.1 200 Connection Established\r\n\r\n"
        client.sendall(hello)
        assert client.recv(4096) == upstream_reply
    finally:
        client.close()
    proxy_worker.join(2)
    upstream_worker.join(2)

    assert bytes(upstream_received) == hello
    audit = results[0].to_audit_dict()
    assert audit["terminal"] == ProxyTerminal.COMPLETED.value
    assert audit["reason_code"] == "TUNNEL_CLOSED"
    assert audit["client_to_upstream_bytes"] == len(hello)
    assert audit["upstream_to_client_bytes"] == len(upstream_reply)
    assert audit["dns_answer_set_sha256"] == (
        "sha256:460a04f42b55e33be24421a368d8044651fda06f9526b930d680a392de95de0a"
    )
    assert audit["dns_recheck_answer_set_sha256"] == audit["dns_answer_set_sha256"]
    assert audit["sni_sha256"] == (
        "sha256:c6240ca88eed5b237451d4ceab51a5df1e8bc3b0a4a0e099fbe4b9f0d5cf23a2"
    )
    assert audit["peer_sha256"] == (
        "sha256:838c4c2573848f58e74332341a7ca6bc5cd86a8aec7d644137d53b4d597f10f5"
    )
    serialized = json.dumps(audit)
    assert "chatgpt.com" not in serialized
    assert "8.8.8.8" not in serialized
    assert upstream_reply.decode() not in serialized
