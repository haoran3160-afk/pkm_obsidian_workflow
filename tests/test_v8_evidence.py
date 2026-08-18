from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from pkm_workflow.evidence import EvidenceLimits, EvidenceParser, FeedFormat
from pkm_workflow.fetch_service import (
    FetchLimits,
    FetchService,
    WireRequest,
    WireResponse,
)
from pkm_workflow.provenance import (
    ObservationOutcome,
    PolicyState,
    SourceLane,
    SourcePolicyEntry,
    SourcePolicyGeneration,
    SourceSnapshot,
    Workflow,
    canonical_json_bytes,
)
from pkm_workflow.source_import import build_source_baseline

FIXTURE = Path(__file__).parent / "fixtures" / "v8_fetch_manifest.json"


class PublicResolver:
    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)


class ManifestTransport:
    def __init__(self, responses: dict[str, WireResponse]) -> None:
        self.responses = responses
        self.requests: list[WireRequest] = []

    def request(
        self,
        request: WireRequest,
        limits: FetchLimits,
        deadline: float,
    ) -> WireResponse:
        self.requests.append(request)
        return self.responses[request.url]


def _fixture(name: str) -> dict[str, str]:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return document[name]


def _policy_snapshot_and_transport(
    names: tuple[str, ...],
) -> tuple[SourcePolicyGeneration, SourceSnapshot, ManifestTransport]:
    fixtures = [_fixture(name) for name in names]
    source_document = {
        "rss_feeds": [
            {"name": name, "url": fixture["endpoint"]}
            for name, fixture in zip(names, fixtures, strict=True)
        ]
    }
    baseline = build_source_baseline(
        (
            (
                "synthetic",
                source_document,
                hashlib.sha256(json.dumps(source_document, sort_keys=True).encode()).hexdigest(),
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
                origin_group=f"fixture-{index}",
                weight_bps=1_000,
                endpoint_generation="endpoint-gen-1",
                evidence_role="primary",
                state=PolicyState.ACTIVE,
            )
            for index, record in enumerate(baseline.records)
        ),
        approval_generation="approval-1",
    )
    snapshot = SourceSnapshot.create(
        policy,
        workflow=Workflow.AI,
        content_date=date(2026, 8, 17),
    )
    transport = ManifestTransport(
        {
            fixture["endpoint"]: WireResponse(
                status=200,
                headers=(("Content-Type", fixture["mime"]),),
                body=fixture["body"].encode(),
                peer_ip="93.184.216.34",
            )
            for fixture in fixtures
        }
    )
    return policy, snapshot, transport


def test_rss_is_normalized_without_following_feed_provided_urls() -> None:
    fixture = _fixture("rss")
    policy, snapshot, transport = _policy_snapshot_and_transport(("rss",))
    batch = FetchService(
        PublicResolver(),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    package = EvidenceParser().parse(policy, snapshot, batch)

    assert len(transport.requests) == 1
    assert len(package.items) == 1
    item = package.items[0]
    assert item.title == fixture["expected_title"]
    assert item.item_url == fixture["expected_url"]
    assert "IGNORE PREVIOUS INSTRUCTIONS" in item.content_text
    assert "<script>" not in item.content_text
    assert item.evidence_id.startswith("evidence-")
    assert package.observation_set.observations[0].outcome is (
        ObservationOutcome.COMPLETE_WITH_ITEMS
    )
    assert package.fetch_batch_hash == batch.fetch_batch_hash
    assert package.evidence_registry_hash.startswith("sha256:")


def test_atom_and_json_feed_share_the_offline_evidence_contract() -> None:
    policy, snapshot, transport = _policy_snapshot_and_transport(("atom", "json_feed"))
    batch = FetchService(
        PublicResolver(),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    package = EvidenceParser().parse(policy, snapshot, batch)

    assert tuple(inspect.signature(EvidenceParser.parse).parameters) == (
        "self",
        "policy",
        "snapshot",
        "batch",
    )
    assert {item.title for item in package.items} == {
        _fixture("atom")["expected_title"],
        _fixture("json_feed")["expected_title"],
    }
    assert {item.feed_format for item in package.items} == {
        FeedFormat.ATOM,
        FeedFormat.JSON_FEED,
    }
    assert all(
        observation.outcome is ObservationOutcome.COMPLETE_WITH_ITEMS
        for observation in package.observation_set.observations
    )


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    (
        (503, b"upstream unavailable", ObservationOutcome.FETCH_ERROR),
        (200, b"<rss><channel /></rss>", ObservationOutcome.COMPLETE_ZERO),
        (200, b"<rss>", ObservationOutcome.PARSE_ERROR),
        (
            200,
            b"<!DOCTYPE rss [<!ENTITY x 'boom'>]><rss><channel /></rss>",
            ObservationOutcome.PARSE_ERROR,
        ),
    ),
)
def test_fetch_zero_and_parse_failures_have_distinct_terminal_outcomes(
    status: int,
    body: bytes,
    expected: ObservationOutcome,
) -> None:
    policy, snapshot, transport = _policy_snapshot_and_transport(("rss",))
    transport.responses[_fixture("rss")["endpoint"]] = WireResponse(
        status=status,
        headers=((("Content-Type", "application/rss+xml"),) if status == 200 else ()),
        body=body,
        peer_ip="93.184.216.34",
    )
    batch = FetchService(
        PublicResolver(),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    package = EvidenceParser().parse(policy, snapshot, batch)

    assert package.items == ()
    assert package.observation_set.observations[0].outcome is expected


@pytest.mark.parametrize(
    "item_url",
    (
        "http://news.example.test/item",
        "file:///secret",
        "https://user:pass@news.example.test/item",
        "https://127.0.0.1/item",
        "https://news.example.test/item#fragment",
    ),
)
def test_one_bad_item_url_makes_the_whole_endpoint_parse_error(
    item_url: str,
) -> None:
    policy, snapshot, transport = _policy_snapshot_and_transport(("rss",))
    body = (
        "<rss><channel>"
        "<item><guid>good</guid><title>Good</title>"
        "<link>https://news.example.test/good</link></item>"
        "<item><guid>bad</guid><title>Bad</title>"
        f"<link>{item_url}</link></item>"
        "</channel></rss>"
    ).encode()
    transport.responses[_fixture("rss")["endpoint"]] = WireResponse(
        status=200,
        headers=(("Content-Type", "application/rss+xml"),),
        body=body,
        peer_ip="93.184.216.34",
    )
    batch = FetchService(
        PublicResolver(),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    package = EvidenceParser().parse(policy, snapshot, batch)

    assert package.items == ()
    assert package.observation_set.observations[0].outcome is (ObservationOutcome.PARSE_ERROR)


def test_character_limit_failure_does_not_leak_partial_evidence() -> None:
    policy, snapshot, transport = _policy_snapshot_and_transport(("rss",))
    body = (
        "<rss><channel><item><guid>large</guid><title>Title</title>"
        "<link>https://news.example.test/large</link>"
        f"<description>{'x' * 101}</description>"
        "</item></channel></rss>"
    ).encode()
    transport.responses[_fixture("rss")["endpoint"]] = WireResponse(
        status=200,
        headers=(("Content-Type", "application/rss+xml"),),
        body=body,
        peer_ip="93.184.216.34",
    )
    batch = FetchService(
        PublicResolver(),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )

    package = EvidenceParser(EvidenceLimits(max_item_characters=100)).parse(policy, snapshot, batch)

    assert package.items == ()
    assert package.observation_set.observations[0].outcome is (ObservationOutcome.PARSE_ERROR)


def test_evidence_package_rejects_rehashed_item_omission() -> None:
    policy, snapshot, transport = _policy_snapshot_and_transport(("atom", "json_feed"))
    batch = FetchService(
        PublicResolver(),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )
    package = EvidenceParser().parse(policy, snapshot, batch)
    package.validate(policy, snapshot, batch)
    missing = replace(package, items=package.items[:-1])
    missing = replace(
        missing,
        evidence_registry_hash=(
            "sha256:" + hashlib.sha256(canonical_json_bytes(missing.payload())).hexdigest()
        ),
    )

    with pytest.raises(ValueError, match="item count"):
        missing.validate(policy, snapshot, batch)


def test_evidence_package_rejects_rehashed_fetch_error_as_complete_zero() -> None:
    policy, snapshot, transport = _policy_snapshot_and_transport(("rss",))
    transport.responses[_fixture("rss")["endpoint"]] = WireResponse(
        status=503,
        headers=(),
        body=b"upstream unavailable",
        peer_ip="93.184.216.34",
    )
    batch = FetchService(PublicResolver(), transport, FetchLimits()).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )
    package = EvidenceParser().parse(policy, snapshot, batch)
    forged_observation = replace(
        package.observation_set.observations[0],
        outcome=ObservationOutcome.COMPLETE_ZERO,
        item_count=0,
    )
    forged = replace(
        package,
        observation_set=type(package.observation_set).create(snapshot, (forged_observation,)),
    )
    forged = replace(
        forged,
        evidence_registry_hash=(
            "sha256:" + hashlib.sha256(canonical_json_bytes(forged.payload())).hexdigest()
        ),
    )

    with pytest.raises(ValueError, match="fetch outcome"):
        forged.validate(policy, snapshot, batch)


def test_evidence_package_reparses_success_body_before_accepting_zero_items() -> None:
    policy, snapshot, transport = _policy_snapshot_and_transport(("rss",))
    batch = FetchService(PublicResolver(), transport, FetchLimits()).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )
    package = EvidenceParser().parse(policy, snapshot, batch)
    forged_observation = replace(
        package.observation_set.observations[0],
        outcome=ObservationOutcome.COMPLETE_ZERO,
        item_count=0,
    )
    forged = replace(
        package,
        observation_set=type(package.observation_set).create(snapshot, (forged_observation,)),
        items=(),
    )
    forged = replace(
        forged,
        evidence_registry_hash=(
            "sha256:" + hashlib.sha256(canonical_json_bytes(forged.payload())).hexdigest()
        ),
    )

    with pytest.raises(ValueError, match="successful fetch body"):
        forged.validate(policy, snapshot, batch)


def test_item_content_mutation_breaks_evidence_identity_chain() -> None:
    policy, snapshot, transport = _policy_snapshot_and_transport(("rss",))
    batch = FetchService(
        PublicResolver(),
        transport,
        FetchLimits(),
    ).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc),
    )
    package = EvidenceParser().parse(policy, snapshot, batch)
    mutated_item = replace(package.items[0], content_text="changed")
    mutated = replace(package, items=(mutated_item,))

    with pytest.raises(ValueError, match="content hash"):
        mutated.validate(policy, snapshot, batch)


@pytest.mark.parametrize("field", tuple(vars(EvidenceLimits())))
@pytest.mark.parametrize("bad_value", (0, -1, True, 1.5))
def test_evidence_limits_require_positive_plain_integers(
    field: str,
    bad_value: object,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        EvidenceLimits(**{field: bad_value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("mime", "body", "limits"),
    (
        ("application/xml", b"<other />", EvidenceLimits()),
        ("application/rss+xml", b"<rss />", EvidenceLimits()),
        (
            "application/rss+xml",
            (
                b"<rss><channel><item><guid>x</guid>"
                b"<link>https://news.example.test/x</link></item></channel></rss>"
            ),
            EvidenceLimits(),
        ),
        (
            "application/rss+xml",
            (
                b"<rss><channel>"
                b"<item><guid>1</guid><title>1</title><link>https://news.example.test/1</link></item>"
                b"<item><guid>2</guid><title>2</title><link>https://news.example.test/2</link></item>"
                b"</channel></rss>"
            ),
            EvidenceLimits(max_items_per_endpoint=1),
        ),
        (
            "application/atom+xml",
            b"<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>x</id><title>X</title></entry></feed>",
            EvidenceLimits(),
        ),
        (
            "application/atom+xml",
            (
                b"<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>x</id>"
                b"<link href='https://news.example.test/x'/></entry></feed>"
            ),
            EvidenceLimits(),
        ),
        ("application/feed+json", b"[]", EvidenceLimits()),
        (
            "application/feed+json",
            b'{"version":"bad","items":[]}',
            EvidenceLimits(),
        ),
        (
            "application/feed+json",
            b'{"version":"https://jsonfeed.org/version/1.1","items":{}}',
            EvidenceLimits(),
        ),
        (
            "application/feed+json",
            b'{"version":"https://jsonfeed.org/version/1.1","items":["bad"]}',
            EvidenceLimits(),
        ),
        (
            "application/feed+json",
            b'{"version":"https://jsonfeed.org/version/1.1","items":[{"url":"https://news.example.test/x","title":"X"}]}',
            EvidenceLimits(),
        ),
        (
            "application/feed+json",
            b'{"version":"https://jsonfeed.org/version/1.1","items":[{"id":"x","title":"X"}]}',
            EvidenceLimits(),
        ),
        (
            "application/feed+json",
            b'{"version":"https://jsonfeed.org/version/1.1","items":[{"id":"x","url":"https://news.example.test/x"}]}',
            EvidenceLimits(),
        ),
        (
            "application/feed+json",
            b'{"version":"https://jsonfeed.org/version/1.1","items":[{"id":"x","url":"https://news.example.test/x","title":"X","content_text":7}]}',
            EvidenceLimits(),
        ),
        (
            "application/feed+json",
            b'{"version":"https://jsonfeed.org/version/1.1","items":[{"id":"x","url":"https://news.example.test/x","title":"X","date_published":7}]}',
            EvidenceLimits(),
        ),
        (
            "application/feed+json",
            b'{"version":"https://jsonfeed.org/version/1.1","items":[{"id":"x","url":"https://news.example.test/x","title":"X","date_modified":7}]}',
            EvidenceLimits(),
        ),
        (
            "application/feed+json",
            b'{"version":"https://jsonfeed.org/version/1.1","items":[{"id":"x","url":"https://news.example.test/x","title":"X","date_published":"2026-08-17T01:00:00"}]}',
            EvidenceLimits(),
        ),
    ),
)
def test_malformed_feed_shapes_fail_as_one_terminal_parse_error(
    mime: str,
    body: bytes,
    limits: EvidenceLimits,
) -> None:
    policy, snapshot, transport = _policy_snapshot_and_transport(("rss",))
    transport.responses[_fixture("rss")["endpoint"]] = WireResponse(
        200,
        (("Content-Type", mime),),
        body,
        "93.184.216.34",
    )
    batch = FetchService(PublicResolver(), transport, FetchLimits()).fetch(
        policy,
        snapshot,
        observed_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )

    package = EvidenceParser(limits).parse(policy, snapshot, batch)

    assert package.items == ()
    assert package.observation_set.observations[0].outcome is (ObservationOutcome.PARSE_ERROR)


def test_total_character_limit_and_duplicate_identity_never_leak_items() -> None:
    policy, snapshot, transport = _policy_snapshot_and_transport(("rss",))
    endpoint = _fixture("rss")["endpoint"]
    one_item = (
        b"<rss><channel><item><guid>x</guid><title>Title</title>"
        b"<link>https://news.example.test/x</link><description>Body</description>"
        b"</item></channel></rss>"
    )
    transport.responses[endpoint] = WireResponse(
        200, (("Content-Type", "application/rss+xml"),), one_item, "93.184.216.34"
    )
    batch = FetchService(PublicResolver(), transport, FetchLimits()).fetch(
        policy, snapshot, datetime(2026, 8, 17, tzinfo=timezone.utc)
    )
    limited = EvidenceParser(EvidenceLimits(max_item_characters=100, max_total_characters=1)).parse(
        policy, snapshot, batch
    )

    assert limited.items == ()
    assert limited.observation_set.observations[0].outcome is (ObservationOutcome.PARSE_ERROR)

    duplicate_body = one_item.replace(
        b"</channel>", one_item.split(b"<channel>", 1)[1].split(b"</channel>", 1)[0] + b"</channel>"
    )
    transport.responses[endpoint] = WireResponse(
        200,
        (("Content-Type", "application/rss+xml"),),
        duplicate_body,
        "93.184.216.34",
    )
    duplicate_batch = FetchService(PublicResolver(), transport, FetchLimits()).fetch(
        policy, snapshot, datetime(2026, 8, 17, tzinfo=timezone.utc)
    )
    with pytest.raises(ValueError, match="IDs must be unique"):
        EvidenceParser().parse(policy, snapshot, duplicate_batch)
