from __future__ import annotations

import gzip
import hashlib
import inspect
import socket
import zlib
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from pkm_workflow.fetch_service import (
    FetchLimits,
    FetchOutcome,
    FetchService,
    PinnedHttpsTransport,
    SystemResolver,
    WireRequest,
    WireResponse,
)
from pkm_workflow.provenance import (
    PolicyState,
    SourceLane,
    SourcePolicyEntry,
    SourcePolicyGeneration,
    SourceSnapshot,
    Workflow,
    canonical_json_bytes,
)
from pkm_workflow.source_import import build_source_baseline


class ScriptedResolver:
    def __init__(self, addresses: tuple[str, ...]) -> None:
        self.addresses = addresses
        self.calls: list[tuple[str, int]] = []

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        return self.addresses


class ScriptedTransport:
    def __init__(self, response: WireResponse) -> None:
        self.response = response
        self.requests: list[WireRequest] = []

    def request(
        self,
        request: WireRequest,
        limits: FetchLimits,
        deadline: float,
    ) -> WireResponse:
        self.requests.append(request)
        return self.response


class RaisingTransport:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.requests: list[WireRequest] = []

    def request(
        self,
        request: WireRequest,
        limits: FetchLimits,
        deadline: float,
    ) -> WireResponse:
        self.requests.append(request)
        raise self.error


class SequenceTransport:
    def __init__(self, responses: tuple[WireResponse, ...]) -> None:
        self.responses = list(responses)
        self.requests: list[WireRequest] = []

    def request(
        self,
        request: WireRequest,
        limits: FetchLimits,
        deadline: float,
    ) -> WireResponse:
        self.requests.append(request)
        return self.responses.pop(0)


class FakeSocket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)


class FakeHTTPResponse:
    status = 200

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def read(self, maximum: int) -> bytes:
        chunk = self.chunks.pop(0) if self.chunks else b""
        return chunk[:maximum]

    def getheaders(self) -> list[tuple[str, str]]:
        return [("Content-Type", "application/rss+xml")]


class FakePinnedConnection:
    def __init__(self, chunks: list[bytes]) -> None:
        self.peer_ip = "93.184.216.34"
        self.sock = FakeSocket()
        self.response = FakeHTTPResponse(chunks)
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.closed = False

    def request(
        self,
        method: str,
        target: str,
        *,
        headers: dict[str, str],
    ) -> None:
        self.requests.append((method, target, headers))

    def getresponse(self) -> FakeHTTPResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


def _policy_and_snapshot(
    *,
    duplicate_count: int = 1,
    endpoint: str = "https://feeds.example.test/rss",
    endpoints: tuple[str, ...] | None = None,
) -> tuple[SourcePolicyGeneration, SourceSnapshot]:
    feeds = [
        {"url": value}
        for value in (endpoints if endpoints is not None else (endpoint,) * duplicate_count)
    ]
    baseline = build_source_baseline(
        (
            (
                "synthetic",
                {"rss_feeds": feeds},
                hashlib.sha256(b"source-doc").hexdigest(),
            ),
        )
    )
    policy = SourcePolicyGeneration.create(
        baseline,
        entries=tuple(
            SourcePolicyEntry(
                source_id=record.source_id,
                workflows=(Workflow.AI,),
                lane=SourceLane.OFFICIAL_LABS,
                origin_group="example-origin",
                weight_bps=1_000,
                endpoint_generation="endpoint-gen-1",
                evidence_role="primary",
                state=PolicyState.ACTIVE,
            )
            for record in baseline.records
        ),
        approval_generation="approval-1",
    )
    snapshot = SourceSnapshot.create(
        policy,
        workflow=Workflow.AI,
        content_date=date(2026, 8, 17),
    )
    return policy, snapshot


def _policy_and_snapshot_from_document(
    source_document: dict[str, object],
) -> tuple[SourcePolicyGeneration, SourceSnapshot]:
    baseline = build_source_baseline(
        (
            (
                "synthetic",
                source_document,
                hashlib.sha256(b"adapter-source-doc").hexdigest(),
            ),
        )
    )
    policy = SourcePolicyGeneration.create(
        baseline,
        entries=tuple(
            SourcePolicyEntry(
                source_id=record.source_id,
                workflows=(Workflow.AI,),
                lane=SourceLane.OFFICIAL_LABS,
                origin_group=f"adapter-origin-{index}",
                weight_bps=1_000,
                endpoint_generation="endpoint-gen-1",
                evidence_role="primary",
                state=PolicyState.ACTIVE,
            )
            for index, record in enumerate(baseline.records)
        ),
        approval_generation="approval-1",
    )
    return policy, SourceSnapshot.create(
        policy,
        workflow=Workflow.AI,
        content_date=date(2026, 8, 17),
    )


def test_fetch_seam_derives_the_only_request_from_the_validated_snapshot() -> None:
    policy, snapshot = _policy_and_snapshot()
    body = b"<rss version='2.0'><channel /></rss>"
    resolver = ScriptedResolver(("93.184.216.34",))
    transport = ScriptedTransport(
        WireResponse(
            status=200,
            headers=(("Content-Type", "application/rss+xml; charset=utf-8"),),
            body=body,
            peer_ip="93.184.216.34",
        )
    )
    service = FetchService(resolver, transport, FetchLimits())

    batch = service.fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert tuple(inspect.signature(FetchService.fetch).parameters) == (
        "self",
        "policy",
        "snapshot",
        "observed_at",
    )
    assert resolver.calls == [("feeds.example.test", 443)]
    assert [request.url for request in transport.requests] == ["https://feeds.example.test/rss"]
    assert len(batch.records) == 1
    assert batch.records[0].outcome is FetchOutcome.SUCCESS
    assert batch.records[0].body == body
    assert batch.source_snapshot_hash == snapshot.source_snapshot_hash
    assert batch.fetch_batch_hash.startswith("sha256:")


@pytest.mark.parametrize(
    "addresses",
    (
        ("127.0.0.1",),
        ("10.0.0.1",),
        ("169.254.1.2",),
        ("93.184.216.34", "192.168.1.2"),
        ("::ffff:127.0.0.1",),
    ),
)
def test_private_or_mixed_dns_is_a_terminal_rejection(
    addresses: tuple[str, ...],
) -> None:
    policy, snapshot = _policy_and_snapshot()
    resolver = ScriptedResolver(addresses)
    transport = ScriptedTransport(
        WireResponse(status=200, headers=(), body=b"", peer_ip=addresses[0])
    )

    batch = FetchService(resolver, transport, FetchLimits()).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert transport.requests == []
    assert len(batch.records) == 1
    assert batch.records[0].outcome is FetchOutcome.DNS_REJECTED
    assert batch.records[0].body is None
    assert batch.records[0].error_code == "DNS_NOT_PUBLIC"


def test_response_peer_must_match_the_validated_dns_set() -> None:
    policy, snapshot = _policy_and_snapshot()
    transport = ScriptedTransport(
        WireResponse(
            status=200,
            headers=(("Content-Type", "application/rss+xml"),),
            body=b"<rss version='2.0'><channel /></rss>",
            peer_ip="93.184.216.35",
        )
    )

    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert len(transport.requests) == 1
    assert batch.records[0].outcome is FetchOutcome.PEER_REJECTED
    assert batch.records[0].error_code == "PEER_DNS_MISMATCH"
    assert batch.records[0].body is None


@pytest.mark.parametrize("status", (429, 500, 503))
def test_http_failures_remain_explicit_terminal_records(status: int) -> None:
    policy, snapshot = _policy_and_snapshot()
    transport = ScriptedTransport(
        WireResponse(
            status=status,
            headers=(),
            body=b"upstream failure",
            peer_ip="93.184.216.34",
        )
    )

    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert batch.records[0].outcome is FetchOutcome.HTTP_ERROR
    assert batch.records[0].status == status
    assert batch.records[0].error_code == f"HTTP_{status}"
    assert batch.records[0].body is None


@pytest.mark.parametrize(
    ("error", "outcome", "code"),
    (
        (TimeoutError("slow"), FetchOutcome.TIMEOUT, "NETWORK_TIMEOUT"),
        (OSError("down"), FetchOutcome.NETWORK_ERROR, "NETWORK_ERROR"),
    ),
)
def test_transport_errors_remain_explicit_terminal_records(
    error: Exception,
    outcome: FetchOutcome,
    code: str,
) -> None:
    policy, snapshot = _policy_and_snapshot()
    transport = RaisingTransport(error)

    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert batch.records[0].outcome is outcome
    assert batch.records[0].error_code == code
    assert batch.records[0].body is None


def test_same_origin_redirect_is_revalidated_one_hop_at_a_time() -> None:
    policy, snapshot = _policy_and_snapshot()
    body = b"<rss version='2.0'><channel /></rss>"
    transport = SequenceTransport(
        (
            WireResponse(
                status=302,
                headers=(("Location", "/moved.xml"),),
                body=b"",
                peer_ip="93.184.216.34",
            ),
            WireResponse(
                status=200,
                headers=(("Content-Type", "application/rss+xml"),),
                body=body,
                peer_ip="93.184.216.34",
            ),
        )
    )
    resolver = ScriptedResolver(("93.184.216.34",))

    batch = FetchService(resolver, transport, FetchLimits()).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert resolver.calls == [
        ("feeds.example.test", 443),
        ("feeds.example.test", 443),
    ]
    assert [request.url for request in transport.requests] == [
        "https://feeds.example.test/rss",
        "https://feeds.example.test/moved.xml",
    ]
    assert batch.records[0].outcome is FetchOutcome.SUCCESS
    assert batch.records[0].final_url == "https://feeds.example.test/moved.xml"
    assert batch.records[0].redirect_chain == ("https://feeds.example.test/moved.xml",)


def test_cross_origin_redirect_is_rejected_before_dns_or_second_request() -> None:
    policy, snapshot = _policy_and_snapshot()
    transport = SequenceTransport(
        (
            WireResponse(
                status=302,
                headers=(("Location", "https://evil.example.test/feed"),),
                body=b"",
                peer_ip="93.184.216.34",
            ),
        )
    )
    resolver = ScriptedResolver(("93.184.216.34",))

    batch = FetchService(resolver, transport, FetchLimits()).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert resolver.calls == [("feeds.example.test", 443)]
    assert len(transport.requests) == 1
    assert batch.records[0].outcome is FetchOutcome.REDIRECT_REJECTED
    assert batch.records[0].error_code == "REDIRECT_ORIGIN_NOT_APPROVED"


@pytest.mark.parametrize(
    "headers",
    (
        (),
        (("Content-Type", "text/html"),),
        (
            ("Content-Type", "application/rss+xml"),
            ("content-type", "application/atom+xml"),
        ),
    ),
)
def test_bad_or_ambiguous_mime_is_a_terminal_rejection(
    headers: tuple[tuple[str, str], ...],
) -> None:
    policy, snapshot = _policy_and_snapshot()
    transport = ScriptedTransport(
        WireResponse(
            status=200,
            headers=headers,
            body=b"<rss />",
            peer_ip="93.184.216.34",
        )
    )

    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert batch.records[0].outcome is FetchOutcome.MIME_REJECTED
    assert batch.records[0].body is None


@pytest.mark.parametrize(
    ("headers", "body", "limits", "outcome", "code"),
    (
        (
            (
                ("Content-Type", "application/rss+xml"),
                ("Content-Encoding", "br"),
            ),
            b"encoded",
            FetchLimits(),
            FetchOutcome.ENCODING_REJECTED,
            "CONTENT_ENCODING_NOT_ALLOWED",
        ),
        (
            (("Content-Type", "application/rss+xml"),),
            b"12345678901",
            FetchLimits(max_compressed_bytes=10),
            FetchOutcome.SIZE_REJECTED,
            "COMPRESSED_SIZE_LIMIT",
        ),
        (
            (
                ("Content-Type", "application/rss+xml"),
                ("Content-Length", "999"),
            ),
            b"small",
            FetchLimits(max_compressed_bytes=10),
            FetchOutcome.SIZE_REJECTED,
            "COMPRESSED_SIZE_LIMIT",
        ),
        (
            (
                ("Content-Type", "application/rss+xml"),
                ("Content-Encoding", "gzip"),
            ),
            gzip.compress(b"A" * 1_000),
            FetchLimits(max_decompressed_bytes=100),
            FetchOutcome.DECOMPRESSION_REJECTED,
            "DECOMPRESSED_SIZE_LIMIT",
        ),
        (
            (
                ("Content-Type", "application/rss+xml"),
                ("Content-Encoding", "gzip"),
            ),
            gzip.compress(b"A" * 1_000),
            FetchLimits(max_compression_ratio=2),
            FetchOutcome.DECOMPRESSION_REJECTED,
            "COMPRESSION_RATIO_LIMIT",
        ),
        (
            (
                ("Content-Type", "application/rss+xml"),
                ("Content-Encoding", "gzip"),
            ),
            b"not-gzip",
            FetchLimits(),
            FetchOutcome.DECOMPRESSION_REJECTED,
            "DECOMPRESSION_INVALID",
        ),
    ),
)
def test_encoding_and_size_limits_fail_closed(
    headers: tuple[tuple[str, str], ...],
    body: bytes,
    limits: FetchLimits,
    outcome: FetchOutcome,
    code: str,
) -> None:
    policy, snapshot = _policy_and_snapshot()
    transport = ScriptedTransport(
        WireResponse(
            status=200,
            headers=headers,
            body=body,
            peer_ip="93.184.216.34",
        )
    )

    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        transport,
        limits,
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert batch.records[0].outcome is outcome
    assert batch.records[0].error_code == code
    assert batch.records[0].body is None


@pytest.mark.parametrize(
    ("encoding", "encoded"),
    (
        ("gzip", gzip.compress(b"<rss><channel /></rss>")),
        ("deflate", zlib.compress(b"<rss><channel /></rss>")),
    ),
)
def test_allowed_compression_returns_only_the_bounded_decoded_body(
    encoding: str,
    encoded: bytes,
) -> None:
    policy, snapshot = _policy_and_snapshot()
    transport = ScriptedTransport(
        WireResponse(
            status=200,
            headers=(
                ("Content-Type", "application/rss+xml"),
                ("Content-Encoding", encoding),
            ),
            body=encoded,
            peer_ip="93.184.216.34",
        )
    )

    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    record = batch.records[0]
    assert record.outcome is FetchOutcome.SUCCESS
    assert record.body == b"<rss><channel /></rss>"
    assert record.content_encoding == encoding
    assert record.encoded_length == len(encoded)
    assert record.encoded_hash != record.body_hash


def test_duplicate_registry_endpoint_is_requested_once_without_losing_rows() -> None:
    policy, snapshot = _policy_and_snapshot(duplicate_count=2)
    body = b"<rss version='2.0'><channel /></rss>"
    transport = SequenceTransport(
        (
            WireResponse(
                status=200,
                headers=(("Content-Type", "application/rss+xml"),),
                body=body,
                peer_ip="93.184.216.34",
            ),
        )
    )

    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert len(transport.requests) == 1
    assert len(batch.records) == 2
    assert len({record.source_id for record in batch.records}) == 2
    assert all(record.body == body for record in batch.records)
    batch.validate(policy, snapshot)


def test_each_unique_endpoint_receives_an_independent_total_deadline() -> None:
    policy, snapshot = _policy_and_snapshot(
        endpoints=(
            "https://feeds.example.test/one",
            "https://feeds.example.test/two",
        )
    )
    body = b"<rss version='2.0'><channel /></rss>"
    transport = SequenceTransport(
        (
            WireResponse(
                status=200,
                headers=(("Content-Type", "application/rss+xml"),),
                body=body,
                peer_ip="93.184.216.34",
            ),
            WireResponse(
                status=200,
                headers=(("Content-Type", "application/rss+xml"),),
                body=body,
                peer_ip="93.184.216.34",
            ),
        )
    )
    monotonic_values = iter((0.0, 0.0, 31.0, 31.0))

    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        transport,
        FetchLimits(total_timeout_seconds=30),
        monotonic=lambda: next(monotonic_values),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert [record.outcome for record in batch.records] == [
        FetchOutcome.SUCCESS,
        FetchOutcome.SUCCESS,
    ]
    assert len(transport.requests) == 2


def test_unsupported_opaque_adapter_is_terminal_without_aborting_batch() -> None:
    policy, snapshot = _policy_and_snapshot_from_document(
        {
            "rss_feeds": [{"url": "https://feeds.example.test/rss"}],
            "ielts_resources": {"books": ["Book title"]},
        }
    )
    body = b"<rss version='2.0'><channel /></rss>"
    resolver = ScriptedResolver(("93.184.216.34",))
    transport = ScriptedTransport(
        WireResponse(
            status=200,
            headers=(("Content-Type", "application/rss+xml"),),
            body=body,
            peer_ip="93.184.216.34",
        )
    )

    batch = FetchService(resolver, transport, FetchLimits()).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert {record.outcome for record in batch.records} == {
        FetchOutcome.SUCCESS,
        FetchOutcome.UNSUPPORTED_ADAPTER,
    }
    unsupported = next(
        record for record in batch.records if record.outcome is FetchOutcome.UNSUPPORTED_ADAPTER
    )
    assert unsupported.adapter == "book"
    assert unsupported.error_code == "UNSUPPORTED_ADAPTER_BOOK"
    assert unsupported.requested_url == "Book title"
    assert len(transport.requests) == 1
    batch.validate(policy, snapshot)


def test_batch_validation_rejects_rehashed_adapter_substitution() -> None:
    policy, snapshot = _policy_and_snapshot()
    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        ScriptedTransport(
            WireResponse(
                status=200,
                headers=(("Content-Type", "application/rss+xml"),),
                body=b"<rss version='2.0'><channel /></rss>",
                peer_ip="93.184.216.34",
            )
        ),
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )
    forged_record = replace(batch.records[0], adapter="youtube", fetch_record_hash="")
    forged_record = replace(
        forged_record,
        fetch_record_hash=(
            "sha256:"
            + hashlib.sha256(canonical_json_bytes(forged_record.manifest_payload())).hexdigest()
        ),
    )
    forged_batch = replace(batch, records=(forged_record,), fetch_batch_hash="")
    forged_batch = replace(
        forged_batch,
        fetch_batch_hash=(
            "sha256:"
            + hashlib.sha256(canonical_json_bytes(forged_batch.manifest_payload())).hexdigest()
        ),
    )

    with pytest.raises(ValueError, match="adapter"):
        forged_batch.validate(policy, snapshot)


def test_batch_validation_rejects_rehashed_source_omission() -> None:
    policy, snapshot = _policy_and_snapshot(duplicate_count=2)
    body = b"<rss version='2.0'><channel /></rss>"
    transport = ScriptedTransport(
        WireResponse(
            status=200,
            headers=(("Content-Type", "application/rss+xml"),),
            body=body,
            peer_ip="93.184.216.34",
        )
    )
    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )
    missing = replace(batch, records=batch.records[:-1])
    missing = replace(
        missing,
        fetch_batch_hash=(
            "sha256:" + hashlib.sha256(canonical_json_bytes(missing.manifest_payload())).hexdigest()
        ),
    )

    with pytest.raises(ValueError, match="exactly match"):
        missing.validate(policy, snapshot)


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://feeds.example.test/rss",
        "file:///feeds.xml",
        "https://user:pass@feeds.example.test/rss",
        "https://feeds.example.test/rss#fragment",
    ),
)
def test_invalid_registry_url_is_rejected_before_dns(
    endpoint: str,
) -> None:
    policy, snapshot = _policy_and_snapshot(endpoint=endpoint)
    resolver = ScriptedResolver(("93.184.216.34",))
    transport = ScriptedTransport(
        WireResponse(status=200, headers=(), body=b"", peer_ip="93.184.216.34")
    )

    with pytest.raises(ValueError, match="credential-free HTTPS"):
        FetchService(resolver, transport, FetchLimits()).fetch(
            policy,
            snapshot,
            observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
        )

    assert resolver.calls == []
    assert transport.requests == []


@pytest.mark.parametrize(
    ("responses", "limits", "code"),
    (
        (
            (
                WireResponse(
                    status=302,
                    headers=(("Location", "/rss"),),
                    body=b"",
                    peer_ip="93.184.216.34",
                ),
            ),
            FetchLimits(),
            "REDIRECT_LOOP",
        ),
        (
            (
                WireResponse(
                    status=302,
                    headers=(),
                    body=b"",
                    peer_ip="93.184.216.34",
                ),
            ),
            FetchLimits(),
            "REDIRECT_LOCATION_INVALID",
        ),
        (
            (
                WireResponse(
                    status=302,
                    headers=(("Location", "/one"),),
                    body=b"",
                    peer_ip="93.184.216.34",
                ),
                WireResponse(
                    status=302,
                    headers=(("Location", "/two"),),
                    body=b"",
                    peer_ip="93.184.216.34",
                ),
            ),
            FetchLimits(max_redirects=1),
            "REDIRECT_LIMIT_EXCEEDED",
        ),
    ),
)
def test_redirect_loop_missing_location_and_limit_are_terminal(
    responses: tuple[WireResponse, ...],
    limits: FetchLimits,
    code: str,
) -> None:
    policy, snapshot = _policy_and_snapshot()
    transport = SequenceTransport(responses)

    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        transport,
        limits,
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert batch.records[0].outcome is FetchOutcome.REDIRECT_REJECTED
    assert batch.records[0].error_code == code


def test_expired_total_deadline_never_reaches_transport() -> None:
    policy, snapshot = _policy_and_snapshot()
    clock = iter((0.0, 31.0)).__next__
    transport = ScriptedTransport(
        WireResponse(status=200, headers=(), body=b"", peer_ip="93.184.216.34")
    )

    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        transport,
        FetchLimits(total_timeout_seconds=30),
        monotonic=clock,
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert transport.requests == []
    assert batch.records[0].outcome is FetchOutcome.TIMEOUT
    assert batch.records[0].error_code == "TOTAL_TIMEOUT"


def test_rehashed_cross_origin_request_url_is_rejected_by_registry_binding() -> None:
    policy, snapshot = _policy_and_snapshot()
    transport = ScriptedTransport(
        WireResponse(
            status=200,
            headers=(("Content-Type", "application/rss+xml"),),
            body=b"<rss><channel /></rss>",
            peer_ip="93.184.216.34",
        )
    )
    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )
    forged_record = replace(
        batch.records[0],
        requested_url="https://evil.example.test/feed",
        fetch_record_hash="",
    )
    forged_record = replace(
        forged_record,
        fetch_record_hash=(
            "sha256:"
            + hashlib.sha256(canonical_json_bytes(forged_record.manifest_payload())).hexdigest()
        ),
    )
    forged_batch = replace(
        batch,
        records=(forged_record,),
        fetch_batch_hash="",
    )
    forged_batch = replace(
        forged_batch,
        fetch_batch_hash=(
            "sha256:"
            + hashlib.sha256(canonical_json_bytes(forged_batch.manifest_payload())).hexdigest()
        ),
    )

    with pytest.raises(ValueError, match="registry URL"):
        forged_batch.validate(policy, snapshot)


def test_system_resolver_returns_every_unique_tcp_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: answers)

    assert SystemResolver().resolve("feeds.example.test", 443) == (
        "93.184.216.34",
        "93.184.216.35",
    )


def test_pinned_transport_bounds_streaming_and_closes_connection() -> None:
    connection = FakePinnedConnection([b"12345678901", b""])
    factory_calls: list[tuple[WireRequest, float]] = []

    def factory(request: WireRequest, timeout: float) -> FakePinnedConnection:
        factory_calls.append((request, timeout))
        return connection

    request = WireRequest(
        url="https://feeds.example.test/rss",
        host="feeds.example.test",
        port=443,
        target="/rss",
        connect_ip="93.184.216.34",
    )
    response = PinnedHttpsTransport(
        monotonic=lambda: 1.0,
        connection_factory=factory,
    ).request(
        request,
        FetchLimits(connect_timeout_seconds=4, max_compressed_bytes=10),
        deadline=11.0,
    )

    assert factory_calls == [(request, 4.0)]
    assert connection.requests[0][0:2] == ("GET", "/rss")
    assert connection.requests[0][2]["Accept-Encoding"] == "gzip, deflate"
    assert response.body == b"12345678901"
    assert response.peer_ip == "93.184.216.34"
    assert connection.sock.timeouts
    assert all(timeout <= 10.0 for timeout in connection.sock.timeouts)
    assert connection.closed is True


def test_pinned_transport_rejects_expired_deadline_without_connection() -> None:
    called = False

    def factory(request: WireRequest, timeout: float) -> FakePinnedConnection:
        nonlocal called
        called = True
        return FakePinnedConnection([])

    with pytest.raises(TimeoutError, match="deadline"):
        PinnedHttpsTransport(
            monotonic=lambda: 10.0,
            connection_factory=factory,
        ).request(
            WireRequest(
                url="https://feeds.example.test/rss",
                host="feeds.example.test",
                port=443,
                target="/rss",
                connect_ip="93.184.216.34",
            ),
            FetchLimits(),
            deadline=10.0,
        )

    assert called is False


@pytest.mark.parametrize("field", tuple(vars(FetchLimits())))
@pytest.mark.parametrize("bad_value", (0, -1, True, 1.5))
def test_fetch_limits_require_positive_plain_integers(
    field: str,
    bad_value: object,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        FetchLimits(**{field: bad_value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("headers", "body", "limits", "outcome", "code"),
    (
        (
            (
                ("Content-Type", "application/rss+xml"),
                ("Content-Length", "1"),
                ("content-length", "1"),
            ),
            b"x",
            FetchLimits(),
            FetchOutcome.SIZE_REJECTED,
            "CONTENT_LENGTH_INVALID",
        ),
        (
            (
                ("Content-Type", "application/rss+xml"),
                ("Content-Length", "NaN"),
            ),
            b"x",
            FetchLimits(),
            FetchOutcome.SIZE_REJECTED,
            "CONTENT_LENGTH_INVALID",
        ),
        (
            (
                ("Content-Type", "application/rss+xml"),
                ("Content-Length", "-1"),
            ),
            b"x",
            FetchLimits(),
            FetchOutcome.SIZE_REJECTED,
            "COMPRESSED_SIZE_LIMIT",
        ),
        (
            (
                ("Content-Type", "application/rss+xml"),
                ("Content-Encoding", "gzip"),
                ("content-encoding", "gzip"),
            ),
            gzip.compress(b"x"),
            FetchLimits(),
            FetchOutcome.ENCODING_REJECTED,
            "CONTENT_ENCODING_NOT_ALLOWED",
        ),
        (
            (("Content-Type", "application/rss+xml"),),
            b"123456",
            FetchLimits(max_compressed_bytes=10, max_decompressed_bytes=5),
            FetchOutcome.DECOMPRESSION_REJECTED,
            "DECOMPRESSED_SIZE_LIMIT",
        ),
        (
            (
                ("Content-Type", "application/rss+xml"),
                ("Content-Encoding", "gzip"),
            ),
            gzip.compress(b"payload")[:-2],
            FetchLimits(),
            FetchOutcome.DECOMPRESSION_REJECTED,
            "DECOMPRESSION_INVALID",
        ),
    ),
)
def test_response_metadata_and_stream_boundaries_fail_closed(
    headers: tuple[tuple[str, str], ...],
    body: bytes,
    limits: FetchLimits,
    outcome: FetchOutcome,
    code: str,
) -> None:
    policy, snapshot = _policy_and_snapshot()
    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        ScriptedTransport(WireResponse(200, headers, body, "93.184.216.34")),
        limits,
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert batch.records[0].outcome is outcome
    assert batch.records[0].error_code == code


def test_empty_dns_invalid_peer_and_naive_observation_fail_closed() -> None:
    policy, snapshot = _policy_and_snapshot()
    transport = ScriptedTransport(
        WireResponse(200, (("Content-Type", "application/rss+xml"),), b"x", "bad")
    )
    empty_dns = FetchService(ScriptedResolver(()), transport, FetchLimits()).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )
    bad_peer = FetchService(ScriptedResolver(("93.184.216.34",)), transport, FetchLimits()).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )

    assert empty_dns.records[0].outcome is FetchOutcome.DNS_REJECTED
    assert bad_peer.records[0].outcome is FetchOutcome.PEER_REJECTED
    with pytest.raises(ValueError, match="timezone-aware"):
        FetchService(ScriptedResolver(()), transport, FetchLimits()).fetch(
            policy,
            snapshot,
            observed_at=datetime(2026, 8, 17),
        )


def test_query_target_and_invalid_redirect_url_are_bounded() -> None:
    policy, snapshot = _policy_and_snapshot(endpoint="https://feeds.example.test/rss?format=xml")
    transport = SequenceTransport(
        (
            WireResponse(
                302,
                (("Location", "file:///etc/passwd"),),
                b"",
                "93.184.216.34",
            ),
        )
    )
    batch = FetchService(ScriptedResolver(("93.184.216.34",)), transport, FetchLimits()).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )

    assert transport.requests[0].target == "/rss?format=xml"
    assert batch.records[0].outcome is FetchOutcome.REDIRECT_REJECTED
    assert batch.records[0].error_code == "REDIRECT_URL_INVALID"


def test_invalid_registry_port_is_rejected_before_resolution() -> None:
    with pytest.raises(ValueError, match="Port could not be cast"):
        _policy_and_snapshot(endpoint="https://feeds.example.test:invalid/rss")


@pytest.mark.parametrize(
    "changes",
    (
        {"final_url": "https://evil.example.test/feed"},
        {"redirect_chain": ("https://evil.example.test/feed",)},
    ),
)
def test_rehashed_success_cannot_claim_cross_origin_navigation(
    changes: dict[str, object],
) -> None:
    policy, snapshot = _policy_and_snapshot()
    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        ScriptedTransport(
            WireResponse(
                200,
                (("Content-Type", "application/rss+xml"),),
                b"<rss><channel /></rss>",
                "93.184.216.34",
            )
        ),
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )
    forged_record = replace(batch.records[0], **changes, fetch_record_hash="")
    forged_record = replace(
        forged_record,
        fetch_record_hash=(
            "sha256:"
            + hashlib.sha256(canonical_json_bytes(forged_record.manifest_payload())).hexdigest()
        ),
    )
    forged_batch = replace(batch, records=(forged_record,), fetch_batch_hash="")
    forged_batch = replace(
        forged_batch,
        fetch_batch_hash=(
            "sha256:"
            + hashlib.sha256(canonical_json_bytes(forged_batch.manifest_payload())).hexdigest()
        ),
    )

    with pytest.raises(ValueError, match="origin"):
        forged_batch.validate(policy, snapshot)


def test_rehashed_batch_cannot_use_naive_observation_time() -> None:
    policy, snapshot = _policy_and_snapshot()
    batch = FetchService(
        ScriptedResolver(("93.184.216.34",)),
        ScriptedTransport(WireResponse(503, (), b"failure", "93.184.216.34")),
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )
    naive = datetime(2026, 8, 17)
    forged_record = replace(batch.records[0], observed_at=naive, fetch_record_hash="")
    forged_record = replace(
        forged_record,
        fetch_record_hash=(
            "sha256:"
            + hashlib.sha256(canonical_json_bytes(forged_record.manifest_payload())).hexdigest()
        ),
    )
    forged_batch = replace(
        batch,
        observed_at=naive,
        records=(forged_record,),
        fetch_batch_hash="",
    )
    forged_batch = replace(
        forged_batch,
        fetch_batch_hash=(
            "sha256:"
            + hashlib.sha256(canonical_json_bytes(forged_batch.manifest_payload())).hexdigest()
        ),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        forged_batch.validate(policy, snapshot)
