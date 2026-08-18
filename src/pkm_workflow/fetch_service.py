"""Registry-bound HTTPS fetching with explicit terminal records."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import socket
import ssl
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from .provenance import (
    ExpectedEndpoint,
    SourcePolicyGeneration,
    SourceSnapshot,
    canonical_json_bytes,
)


class FetchOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    DNS_REJECTED = "DNS_REJECTED"
    PEER_REJECTED = "PEER_REJECTED"
    REDIRECT_REJECTED = "REDIRECT_REJECTED"
    HTTP_ERROR = "HTTP_ERROR"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    MIME_REJECTED = "MIME_REJECTED"
    ENCODING_REJECTED = "ENCODING_REJECTED"
    SIZE_REJECTED = "SIZE_REJECTED"
    DECOMPRESSION_REJECTED = "DECOMPRESSION_REJECTED"
    UNSUPPORTED_ADAPTER = "UNSUPPORTED_ADAPTER"


FETCHABLE_ADAPTERS = frozenset({"rss", "youtube"})


class _ResponseRejected(ValueError):
    def __init__(self, outcome: FetchOutcome, error_code: str) -> None:
        super().__init__(error_code)
        self.outcome = outcome
        self.error_code = error_code


@dataclass(frozen=True)
class FetchLimits:
    connect_timeout_seconds: int = 10
    read_timeout_seconds: int = 20
    total_timeout_seconds: int = 30
    max_redirects: int = 5
    max_compressed_bytes: int = 2 * 1024 * 1024
    max_decompressed_bytes: int = 8 * 1024 * 1024
    max_compression_ratio: int = 50

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class WireRequest:
    url: str
    host: str
    port: int
    target: str
    connect_ip: str


@dataclass(frozen=True)
class WireResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    peer_ip: str


class Resolver(Protocol):
    def resolve(self, host: str, port: int) -> tuple[str, ...]: ...


class Transport(Protocol):
    def request(
        self,
        request: WireRequest,
        limits: FetchLimits,
        deadline: float,
    ) -> WireResponse: ...


class _ReadableResponse(Protocol):
    status: int

    def read(self, maximum: int) -> bytes: ...

    def getheaders(self) -> list[tuple[str, str]]: ...


class _TimeoutSocket(Protocol):
    def settimeout(self, timeout: float) -> None: ...


class _PinnedConnectionLike(Protocol):
    peer_ip: str | None
    sock: _TimeoutSocket | None

    def request(
        self,
        method: str,
        target: str,
        *,
        headers: dict[str, str],
    ) -> None: ...

    def getresponse(self) -> _ReadableResponse: ...

    def close(self) -> None: ...


class SystemResolver:
    """Resolve every TCP address; policy validation rejects mixed unsafe answers."""

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        answers = socket.getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        return tuple(sorted({str(answer[4][0]) for answer in answers}))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        port: int,
        connect_ip: str,
        *,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._connect_ip = connect_ip
        self._ssl_context = context
        self.peer_ip: str | None = None

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._connect_ip, self.port),
            timeout=self.timeout,
        )
        try:
            self.sock = self._ssl_context.wrap_socket(
                raw_socket,
                server_hostname=self.host,
            )
        except BaseException:
            raw_socket.close()
            raise
        self.peer_ip = str(self.sock.getpeername()[0])


class PinnedHttpsTransport:
    """Issue one HTTPS GET to a validated IP without automatic redirects."""

    def __init__(
        self,
        *,
        context: ssl.SSLContext | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        connection_factory: Callable[[WireRequest, float], _PinnedConnectionLike] | None = None,
    ) -> None:
        self._context = context or ssl.create_default_context()
        self._monotonic = monotonic
        self._connection_factory = connection_factory

    def _new_connection(
        self,
        request: WireRequest,
        timeout: float,
    ) -> _PinnedConnectionLike:
        if self._connection_factory is not None:
            return self._connection_factory(request, timeout)
        return _PinnedHTTPSConnection(
            request.host,
            request.port,
            request.connect_ip,
            timeout=timeout,
            context=self._context,
        )

    def request(
        self,
        request: WireRequest,
        limits: FetchLimits,
        deadline: float,
    ) -> WireResponse:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise TimeoutError("total fetch deadline expired")
        connection = self._new_connection(
            request,
            min(float(limits.connect_timeout_seconds), remaining),
        )
        try:
            connection.request(
                "GET",
                request.target,
                headers={
                    "Accept": (
                        "application/rss+xml, application/atom+xml, "
                        "application/feed+json, application/xml, text/xml"
                    ),
                    "Accept-Encoding": "gzip, deflate",
                    "Connection": "close",
                    "User-Agent": "pkm-workflow/8 fixed-source-fetch",
                },
            )
            response = connection.getresponse()
            if connection.peer_ip is None or connection.sock is None:
                raise OSError("TLS connection did not expose a peer")
            body = bytearray()
            while True:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise TimeoutError("total fetch deadline expired")
                connection.sock.settimeout(min(float(limits.read_timeout_seconds), remaining))
                remaining_bytes = limits.max_compressed_bytes + 1 - len(body)
                if remaining_bytes <= 0:
                    break
                chunk = response.read(min(64 * 1024, remaining_bytes))
                if not chunk:
                    break
                body.extend(chunk)
            return WireResponse(
                status=response.status,
                headers=tuple(response.getheaders()),
                body=bytes(body),
                peer_ip=connection.peer_ip,
            )
        finally:
            connection.close()


@dataclass(frozen=True)
class FetchRecord:
    source_id: str
    source_generation: str
    endpoint_id: str
    endpoint_generation: str
    adapter: str
    requested_url: str
    final_url: str
    redirect_chain: tuple[str, ...]
    observed_at: datetime
    outcome: FetchOutcome
    status: int | None
    content_type: str | None
    content_encoding: str | None
    peer_ips: tuple[str, ...]
    encoded_length: int
    body_length: int
    encoded_hash: str | None
    body_hash: str | None
    error_code: str | None
    encoded_body: bytes | None
    body: bytes | None
    fetch_record_hash: str

    def manifest_payload(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "source_generation": self.source_generation,
            "endpoint_id": self.endpoint_id,
            "endpoint_generation": self.endpoint_generation,
            "adapter": self.adapter,
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "redirect_chain": list(self.redirect_chain),
            "observed_at": self.observed_at.isoformat(timespec="microseconds"),
            "outcome": self.outcome.value,
            "status": self.status,
            "content_type": self.content_type,
            "content_encoding": self.content_encoding,
            "peer_ips": list(self.peer_ips),
            "encoded_length": self.encoded_length,
            "body_length": self.body_length,
            "encoded_hash": self.encoded_hash,
            "body_hash": self.body_hash,
            "error_code": self.error_code,
        }

    def validate(self) -> None:
        if not self.adapter:
            raise ValueError("fetch record adapter is required")
        if _hash(self.manifest_payload()) != self.fetch_record_hash:
            raise ValueError("fetch record hash is invalid")
        if self.outcome is FetchOutcome.SUCCESS:
            if (
                self.encoded_body is None
                or self.body is None
                or self.error_code is not None
                or self.status is None
                or not 200 <= self.status < 300
                or self.encoded_length != len(self.encoded_body)
                or self.body_length != len(self.body)
                or self.encoded_hash != _body_hash(self.encoded_body)
                or self.body_hash != _body_hash(self.body)
            ):
                raise ValueError("successful fetch record body binding is invalid")
        elif (
            self.encoded_body is not None
            or self.body is not None
            or self.encoded_length != 0
            or self.body_length != 0
            or self.encoded_hash is not None
            or self.body_hash is not None
            or not self.error_code
        ):
            raise ValueError("failed fetch record must not carry response body")


@dataclass(frozen=True)
class FetchBatch:
    source_registry_hash: str
    source_policy_hash: str
    source_snapshot_hash: str
    observed_at: datetime
    records: tuple[FetchRecord, ...]
    fetch_batch_hash: str

    def manifest_payload(self) -> dict[str, object]:
        return {
            "source_registry_hash": self.source_registry_hash,
            "source_policy_hash": self.source_policy_hash,
            "source_snapshot_hash": self.source_snapshot_hash,
            "observed_at": self.observed_at.isoformat(timespec="microseconds"),
            "records": [record.manifest_payload() for record in self.records],
        }

    def validate(
        self,
        policy: SourcePolicyGeneration,
        snapshot: SourceSnapshot,
    ) -> None:
        policy.validate()
        if self.observed_at.tzinfo is None:
            raise ValueError("fetch batch observed_at must be timezone-aware")
        expected_snapshot = SourceSnapshot.create(
            policy,
            workflow=snapshot.workflow,
            content_date=snapshot.content_date,
        )
        if expected_snapshot != snapshot:
            raise ValueError("fetch batch snapshot is not the policy projection")
        if (
            self.source_registry_hash != policy.source_registry_hash
            or self.source_policy_hash != policy.source_policy_hash
            or self.source_snapshot_hash != snapshot.source_snapshot_hash
        ):
            raise ValueError("fetch batch proof bindings are inconsistent")
        expected_keys = {
            (expected.source_id, expected.endpoint_id): expected
            for expected in snapshot.expected_endpoints
        }
        registry_sources = {source.source_id: source for source in policy.registry_sources}
        record_keys = [(record.source_id, record.endpoint_id) for record in self.records]
        if len(record_keys) != len(set(record_keys)) or set(record_keys) != set(expected_keys):
            raise ValueError("fetch records must exactly match snapshot endpoints")
        if tuple(sorted(record_keys)) != tuple(record_keys):
            raise ValueError("fetch records are not in canonical order")
        for record in self.records:
            expected = expected_keys[(record.source_id, record.endpoint_id)]
            registry_source = registry_sources[record.source_id]
            if (
                record.source_generation != expected.source_generation
                or record.endpoint_generation != expected.endpoint_generation
                or record.observed_at != self.observed_at
            ):
                raise ValueError("fetch record generation or time binding is invalid")
            if record.adapter != registry_source.adapter:
                raise ValueError("fetch record adapter does not match registry adapter")
            if registry_source.adapter not in FETCHABLE_ADAPTERS:
                if (
                    record.outcome is not FetchOutcome.UNSUPPORTED_ADAPTER
                    or record.requested_url != registry_source.endpoint
                    or record.final_url != registry_source.endpoint
                    or record.redirect_chain
                    or record.peer_ips
                    or record.status is not None
                    or record.error_code != _unsupported_adapter_code(registry_source.adapter)
                ):
                    raise ValueError("unsupported adapter terminal record is invalid")
                record.validate()
                continue
            if _parse_fetch_url(record.requested_url) != _parse_fetch_url(registry_source.endpoint):
                raise ValueError("fetch record requested URL does not match registry URL")
            approved_host, approved_port, _approved_target = _parse_fetch_url(
                registry_source.endpoint
            )
            approved_origin = (approved_host, approved_port)
            try:
                redirect_origins = tuple(_parse_fetch_url(url)[:2] for url in record.redirect_chain)
            except ValueError as error:
                raise ValueError("fetch redirect chain has an invalid origin") from error
            if any(origin != approved_origin for origin in redirect_origins):
                raise ValueError("fetch redirect chain left the registry origin")
            if record.outcome is FetchOutcome.SUCCESS:
                try:
                    final = _parse_fetch_url(record.final_url)
                    expected_final = _parse_fetch_url(
                        record.redirect_chain[-1] if record.redirect_chain else record.requested_url
                    )
                except ValueError as error:
                    raise ValueError("successful fetch has an invalid final origin") from error
                if final[:2] != approved_origin or final != expected_final:
                    raise ValueError("successful fetch left the registry origin")
            record.validate()
        if _hash(self.manifest_payload()) != self.fetch_batch_hash:
            raise ValueError("fetch batch hash is invalid")


def _hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _body_hash(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _rebind_record(
    record: FetchRecord,
    expected: ExpectedEndpoint,
) -> FetchRecord:
    rebound = replace(
        record,
        source_id=expected.source_id,
        source_generation=expected.source_generation,
        endpoint_id=expected.endpoint_id,
        endpoint_generation=expected.endpoint_generation,
        fetch_record_hash="",
    )
    return replace(
        rebound,
        fetch_record_hash=_hash(rebound.manifest_payload()),
    )


def _bind_adapter(record: FetchRecord, adapter: str) -> FetchRecord:
    bound = replace(record, adapter=adapter, fetch_record_hash="")
    return replace(
        bound,
        fetch_record_hash=_hash(bound.manifest_payload()),
    )


def _unsupported_adapter_code(adapter: str) -> str:
    suffix = "".join(
        character if character.isalnum() else "_" for character in adapter.upper()
    ).strip("_")
    return f"UNSUPPORTED_ADAPTER_{suffix or 'UNKNOWN'}"


class FetchService:
    def __init__(
        self,
        resolver: Resolver,
        transport: Transport,
        limits: FetchLimits,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._resolver = resolver
        self._transport = transport
        self._limits = limits
        self._monotonic = monotonic

    def fetch(
        self,
        policy: SourcePolicyGeneration,
        snapshot: SourceSnapshot,
        observed_at: datetime,
    ) -> FetchBatch:
        policy.validate()
        expected_snapshot = SourceSnapshot.create(
            policy,
            workflow=snapshot.workflow,
            content_date=snapshot.content_date,
        )
        if expected_snapshot != snapshot:
            raise ValueError("fetch snapshot is not the deterministic policy projection")
        if observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        registry = {source.source_id: source for source in policy.registry_sources}
        records = []
        fetched_by_endpoint: dict[tuple[str, str, str], FetchRecord] = {}
        for expected in snapshot.expected_endpoints:
            source = registry[expected.source_id]
            if source.endpoint_id != expected.endpoint_id:
                raise ValueError("snapshot endpoint identity does not match registry")
            cache_key = (
                source.adapter,
                source.canonical_endpoint,
                expected.endpoint_generation,
            )
            fetched = fetched_by_endpoint.get(cache_key)
            if fetched is None:
                if source.adapter not in FETCHABLE_ADAPTERS:
                    fetched = self._failed_record(
                        expected,
                        source.endpoint,
                        observed_at,
                        FetchOutcome.UNSUPPORTED_ADAPTER,
                        _unsupported_adapter_code(source.adapter),
                    )
                else:
                    deadline = float(self._monotonic()) + self._limits.total_timeout_seconds
                    fetched = self._fetch_one(
                        expected,
                        source.endpoint,
                        observed_at,
                        deadline,
                    )
                fetched = _bind_adapter(fetched, source.adapter)
                fetched_by_endpoint[cache_key] = fetched
            elif (
                fetched.source_id != expected.source_id
                or fetched.endpoint_id != expected.endpoint_id
            ):
                fetched = _rebind_record(fetched, expected)
            records.append(fetched)
        ordered = tuple(sorted(records, key=lambda record: (record.source_id, record.endpoint_id)))
        payload = {
            "source_registry_hash": policy.source_registry_hash,
            "source_policy_hash": policy.source_policy_hash,
            "source_snapshot_hash": snapshot.source_snapshot_hash,
            "observed_at": observed_at.isoformat(timespec="microseconds"),
            "records": [record.manifest_payload() for record in ordered],
        }
        return FetchBatch(
            source_registry_hash=policy.source_registry_hash,
            source_policy_hash=policy.source_policy_hash,
            source_snapshot_hash=snapshot.source_snapshot_hash,
            observed_at=observed_at,
            records=ordered,
            fetch_batch_hash=_hash(payload),
        )

    def _fetch_one(
        self,
        expected: ExpectedEndpoint,
        endpoint: str,
        observed_at: datetime,
        deadline: float,
    ) -> FetchRecord:
        approved_host, approved_port, _target = _parse_fetch_url(endpoint)
        approved_origin = (approved_host, approved_port)
        current_url = endpoint
        redirect_chain: list[str] = []
        seen_urls = {endpoint}
        peer_ips: list[str] = []

        while True:
            try:
                host, port, target = _parse_fetch_url(current_url)
            except ValueError:
                return self._failed_record(
                    expected,
                    endpoint,
                    observed_at,
                    FetchOutcome.REDIRECT_REJECTED,
                    "REDIRECT_URL_INVALID",
                    final_url=current_url,
                    redirect_chain=tuple(redirect_chain),
                    peer_ips=tuple(peer_ips),
                )
            if (host, port) != approved_origin:
                return self._failed_record(
                    expected,
                    endpoint,
                    observed_at,
                    FetchOutcome.REDIRECT_REJECTED,
                    "REDIRECT_ORIGIN_NOT_APPROVED",
                    final_url=current_url,
                    redirect_chain=tuple(redirect_chain),
                    peer_ips=tuple(peer_ips),
                )
            try:
                addresses = self._resolver.resolve(host, port)
                public_addresses = _validated_public_addresses(addresses)
            except (OSError, ValueError):
                return self._failed_record(
                    expected,
                    endpoint,
                    observed_at,
                    FetchOutcome.DNS_REJECTED,
                    "DNS_NOT_PUBLIC",
                    final_url=current_url,
                    redirect_chain=tuple(redirect_chain),
                    peer_ips=tuple(peer_ips),
                )
            request = WireRequest(
                url=current_url,
                host=host,
                port=port,
                target=target,
                connect_ip=public_addresses[0],
            )
            if float(self._monotonic()) >= deadline:
                return self._failed_record(
                    expected,
                    endpoint,
                    observed_at,
                    FetchOutcome.TIMEOUT,
                    "TOTAL_TIMEOUT",
                    final_url=current_url,
                    redirect_chain=tuple(redirect_chain),
                    peer_ips=tuple(peer_ips),
                )
            try:
                response = self._transport.request(
                    request,
                    self._limits,
                    deadline,
                )
            except TimeoutError:
                return self._failed_record(
                    expected,
                    endpoint,
                    observed_at,
                    FetchOutcome.TIMEOUT,
                    "NETWORK_TIMEOUT",
                    final_url=current_url,
                    redirect_chain=tuple(redirect_chain),
                    peer_ips=tuple(peer_ips),
                )
            except OSError:
                return self._failed_record(
                    expected,
                    endpoint,
                    observed_at,
                    FetchOutcome.NETWORK_ERROR,
                    "NETWORK_ERROR",
                    final_url=current_url,
                    redirect_chain=tuple(redirect_chain),
                    peer_ips=tuple(peer_ips),
                )
            try:
                peer = str(ipaddress.ip_address(response.peer_ip))
            except ValueError:
                peer = response.peer_ip
            if peer not in public_addresses:
                return self._failed_record(
                    expected,
                    endpoint,
                    observed_at,
                    FetchOutcome.PEER_REJECTED,
                    "PEER_DNS_MISMATCH",
                    final_url=current_url,
                    redirect_chain=tuple(redirect_chain),
                    peer_ips=(*peer_ips, peer),
                    status=response.status,
                )
            peer_ips.append(peer)

            if response.status in {301, 302, 303, 307, 308}:
                try:
                    location = _single_header(response.headers, "location")
                except ValueError:
                    return self._failed_record(
                        expected,
                        endpoint,
                        observed_at,
                        FetchOutcome.REDIRECT_REJECTED,
                        "REDIRECT_LOCATION_INVALID",
                        final_url=current_url,
                        redirect_chain=tuple(redirect_chain),
                        peer_ips=tuple(peer_ips),
                        status=response.status,
                    )
                if len(redirect_chain) >= self._limits.max_redirects:
                    return self._failed_record(
                        expected,
                        endpoint,
                        observed_at,
                        FetchOutcome.REDIRECT_REJECTED,
                        "REDIRECT_LIMIT_EXCEEDED",
                        final_url=current_url,
                        redirect_chain=tuple(redirect_chain),
                        peer_ips=tuple(peer_ips),
                        status=response.status,
                    )
                next_url = urljoin(current_url, location)
                try:
                    next_host, next_port, _next_target = _parse_fetch_url(next_url)
                except ValueError:
                    return self._failed_record(
                        expected,
                        endpoint,
                        observed_at,
                        FetchOutcome.REDIRECT_REJECTED,
                        "REDIRECT_URL_INVALID",
                        final_url=next_url,
                        redirect_chain=tuple(redirect_chain),
                        peer_ips=tuple(peer_ips),
                        status=response.status,
                    )
                if (next_host, next_port) != approved_origin:
                    return self._failed_record(
                        expected,
                        endpoint,
                        observed_at,
                        FetchOutcome.REDIRECT_REJECTED,
                        "REDIRECT_ORIGIN_NOT_APPROVED",
                        final_url=next_url,
                        redirect_chain=tuple(redirect_chain),
                        peer_ips=tuple(peer_ips),
                        status=response.status,
                    )
                if next_url in seen_urls:
                    return self._failed_record(
                        expected,
                        endpoint,
                        observed_at,
                        FetchOutcome.REDIRECT_REJECTED,
                        "REDIRECT_LOOP",
                        final_url=next_url,
                        redirect_chain=tuple(redirect_chain),
                        peer_ips=tuple(peer_ips),
                        status=response.status,
                    )
                redirect_chain.append(next_url)
                seen_urls.add(next_url)
                current_url = next_url
                continue

            if response.status < 200 or response.status >= 300:
                return self._failed_record(
                    expected,
                    endpoint,
                    observed_at,
                    FetchOutcome.HTTP_ERROR,
                    f"HTTP_{response.status}",
                    final_url=current_url,
                    redirect_chain=tuple(redirect_chain),
                    peer_ips=tuple(peer_ips),
                    status=response.status,
                )
            try:
                media_type, content_encoding, decoded_body = _decode_response_body(
                    response.headers,
                    response.body,
                    self._limits,
                )
            except _ResponseRejected as error:
                return self._failed_record(
                    expected,
                    endpoint,
                    observed_at,
                    error.outcome,
                    error.error_code,
                    final_url=current_url,
                    redirect_chain=tuple(redirect_chain),
                    peer_ips=tuple(peer_ips),
                    status=response.status,
                )
            encoded_hash = _body_hash(response.body)
            body_hash = _body_hash(decoded_body)
            record_payload = {
                "source_id": expected.source_id,
                "source_generation": expected.source_generation,
                "endpoint_id": expected.endpoint_id,
                "endpoint_generation": expected.endpoint_generation,
                "adapter": "",
                "requested_url": endpoint,
                "final_url": current_url,
                "redirect_chain": redirect_chain,
                "observed_at": observed_at.isoformat(timespec="microseconds"),
                "outcome": FetchOutcome.SUCCESS.value,
                "status": response.status,
                "content_type": media_type,
                "content_encoding": content_encoding,
                "peer_ips": peer_ips,
                "encoded_length": len(response.body),
                "body_length": len(decoded_body),
                "encoded_hash": encoded_hash,
                "body_hash": body_hash,
                "error_code": None,
            }
            return FetchRecord(
                source_id=expected.source_id,
                source_generation=expected.source_generation,
                endpoint_id=expected.endpoint_id,
                endpoint_generation=expected.endpoint_generation,
                adapter="",
                requested_url=endpoint,
                final_url=current_url,
                redirect_chain=tuple(redirect_chain),
                observed_at=observed_at,
                outcome=FetchOutcome.SUCCESS,
                status=response.status,
                content_type=media_type,
                content_encoding=content_encoding,
                peer_ips=tuple(peer_ips),
                encoded_length=len(response.body),
                body_length=len(decoded_body),
                encoded_hash=encoded_hash,
                body_hash=body_hash,
                error_code=None,
                encoded_body=response.body,
                body=decoded_body,
                fetch_record_hash=_hash(record_payload),
            )

    def _failed_record(
        self,
        expected: ExpectedEndpoint,
        requested_url: str,
        observed_at: datetime,
        outcome: FetchOutcome,
        error_code: str,
        *,
        final_url: str | None = None,
        redirect_chain: tuple[str, ...] = (),
        peer_ips: tuple[str, ...] = (),
        status: int | None = None,
    ) -> FetchRecord:
        resolved_final_url = final_url or requested_url
        payload = {
            "source_id": expected.source_id,
            "source_generation": expected.source_generation,
            "endpoint_id": expected.endpoint_id,
            "endpoint_generation": expected.endpoint_generation,
            "adapter": "",
            "requested_url": requested_url,
            "final_url": resolved_final_url,
            "redirect_chain": list(redirect_chain),
            "observed_at": observed_at.isoformat(timespec="microseconds"),
            "outcome": outcome.value,
            "status": status,
            "content_type": None,
            "content_encoding": None,
            "peer_ips": list(peer_ips),
            "encoded_length": 0,
            "body_length": 0,
            "encoded_hash": None,
            "body_hash": None,
            "error_code": error_code,
        }
        return FetchRecord(
            source_id=expected.source_id,
            source_generation=expected.source_generation,
            endpoint_id=expected.endpoint_id,
            endpoint_generation=expected.endpoint_generation,
            adapter="",
            requested_url=requested_url,
            final_url=resolved_final_url,
            redirect_chain=redirect_chain,
            observed_at=observed_at,
            outcome=outcome,
            status=status,
            content_type=None,
            content_encoding=None,
            peer_ips=peer_ips,
            encoded_length=0,
            body_length=0,
            encoded_hash=None,
            body_hash=None,
            error_code=error_code,
            encoded_body=None,
            body=None,
            fetch_record_hash=_hash(payload),
        )


def _validated_public_addresses(addresses: tuple[str, ...]) -> tuple[str, ...]:
    if not addresses:
        raise ValueError("DNS returned no addresses")
    normalized = []
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("DNS answer is not a global public address")
        normalized.append(str(ip))
    return tuple(sorted(set(normalized)))


def _parse_fetch_url(url: str) -> tuple[str, int, str]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("fetch URL must be credential-free HTTPS without fragment")
    try:
        port = parsed.port or 443
    except ValueError as error:
        raise ValueError("fetch URL has an invalid port") from error
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    return parsed.hostname.lower(), port, target


def _single_header(headers: tuple[tuple[str, str], ...], name: str) -> str:
    values = [value.strip() for key, value in headers if key.lower() == name]
    if len(values) != 1 or not values[0]:
        raise ValueError(f"response requires exactly one {name} header")
    return values[0]


def _optional_single_header(
    headers: tuple[tuple[str, str], ...],
    name: str,
) -> str | None:
    values = [value.strip() for key, value in headers if key.lower() == name]
    if len(values) > 1 or (values and not values[0]):
        raise ValueError(f"response has ambiguous {name} header")
    return values[0] if values else None


def _decode_response_body(
    headers: tuple[tuple[str, str], ...],
    encoded: bytes,
    limits: FetchLimits,
) -> tuple[str, str, bytes]:
    try:
        content_type = _single_header(headers, "content-type")
    except ValueError as error:
        raise _ResponseRejected(
            FetchOutcome.MIME_REJECTED,
            "CONTENT_TYPE_INVALID",
        ) from error
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type not in {
        "application/rss+xml",
        "application/atom+xml",
        "application/feed+json",
        "application/xml",
        "text/xml",
    }:
        raise _ResponseRejected(
            FetchOutcome.MIME_REJECTED,
            "CONTENT_TYPE_NOT_ALLOWED",
        )
    try:
        content_length = _optional_single_header(headers, "content-length")
    except ValueError as error:
        raise _ResponseRejected(
            FetchOutcome.SIZE_REJECTED,
            "CONTENT_LENGTH_INVALID",
        ) from error
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as error:
            raise _ResponseRejected(
                FetchOutcome.SIZE_REJECTED,
                "CONTENT_LENGTH_INVALID",
            ) from error
        if declared_length < 0 or declared_length > limits.max_compressed_bytes:
            raise _ResponseRejected(
                FetchOutcome.SIZE_REJECTED,
                "COMPRESSED_SIZE_LIMIT",
            )
    if len(encoded) > limits.max_compressed_bytes:
        raise _ResponseRejected(
            FetchOutcome.SIZE_REJECTED,
            "COMPRESSED_SIZE_LIMIT",
        )
    try:
        encoding_header = _optional_single_header(headers, "content-encoding")
    except ValueError as error:
        raise _ResponseRejected(
            FetchOutcome.ENCODING_REJECTED,
            "CONTENT_ENCODING_NOT_ALLOWED",
        ) from error
    encoding = (encoding_header or "identity").lower()
    if encoding not in {"identity", "gzip", "deflate"}:
        raise _ResponseRejected(
            FetchOutcome.ENCODING_REJECTED,
            "CONTENT_ENCODING_NOT_ALLOWED",
        )
    if encoding == "identity":
        decoded = encoded
    else:
        window_bits = zlib.MAX_WBITS | 16 if encoding == "gzip" else zlib.MAX_WBITS
        decompressor = zlib.decompressobj(window_bits)
        decoded_buffer = bytearray()
        pending = encoded
        try:
            while pending:
                remaining = limits.max_decompressed_bytes + 1 - len(decoded_buffer)
                decoded_buffer.extend(decompressor.decompress(pending, remaining))
                if len(decoded_buffer) > limits.max_decompressed_bytes:
                    raise _ResponseRejected(
                        FetchOutcome.DECOMPRESSION_REJECTED,
                        "DECOMPRESSED_SIZE_LIMIT",
                    )
                pending = decompressor.unconsumed_tail
            remaining = limits.max_decompressed_bytes + 1 - len(decoded_buffer)
            decoded_buffer.extend(decompressor.flush(remaining))
        except zlib.error as error:
            raise _ResponseRejected(
                FetchOutcome.DECOMPRESSION_REJECTED,
                "DECOMPRESSION_INVALID",
            ) from error
        if not decompressor.eof:
            raise _ResponseRejected(
                FetchOutcome.DECOMPRESSION_REJECTED,
                "DECOMPRESSION_INVALID",
            )
        decoded = bytes(decoded_buffer)
    if len(decoded) > limits.max_decompressed_bytes:
        raise _ResponseRejected(
            FetchOutcome.DECOMPRESSION_REJECTED,
            "DECOMPRESSED_SIZE_LIMIT",
        )
    if encoded and len(decoded) > len(encoded) * limits.max_compression_ratio:
        raise _ResponseRejected(
            FetchOutcome.DECOMPRESSION_REJECTED,
            "COMPRESSION_RATIO_LIMIT",
        )
    return media_type, encoding, decoded
