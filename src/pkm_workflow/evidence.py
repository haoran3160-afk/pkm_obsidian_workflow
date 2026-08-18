"""Offline feed parsing and deterministic evidence registry construction."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from html.parser import HTMLParser
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .fetch_service import FetchBatch, FetchOutcome, FetchRecord
from .provenance import (
    ObservationOutcome,
    ObservationScope,
    ObservationSet,
    SourcePolicyGeneration,
    SourceSnapshot,
    TerminalObservation,
    canonical_json_bytes,
)


class FeedFormat(str, Enum):
    RSS = "RSS"
    ATOM = "ATOM"
    JSON_FEED = "JSON_FEED"


@dataclass(frozen=True)
class EvidenceLimits:
    max_items_per_endpoint: int = 1_000
    max_item_characters: int = 200_000
    max_total_characters: int = 2_000_000

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    source_id: str
    source_generation: str
    endpoint_id: str
    endpoint_generation: str
    source_snapshot_hash: str
    fetch_batch_hash: str
    fetch_record_hash: str
    feed_format: FeedFormat
    item_key: str
    item_url: str
    title: str
    published_at: datetime | None
    updated_at: datetime | None
    content_text: str
    content_hash: str
    evidence_item_hash: str

    def payload(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "source_id": self.source_id,
            "source_generation": self.source_generation,
            "endpoint_id": self.endpoint_id,
            "endpoint_generation": self.endpoint_generation,
            "source_snapshot_hash": self.source_snapshot_hash,
            "fetch_batch_hash": self.fetch_batch_hash,
            "fetch_record_hash": self.fetch_record_hash,
            "feed_format": self.feed_format.value,
            "item_key": self.item_key,
            "item_url": self.item_url,
            "title": self.title,
            "published_at": _datetime_value(self.published_at),
            "updated_at": _datetime_value(self.updated_at),
            "content_text": self.content_text,
            "content_hash": self.content_hash,
        }

    def validate(self) -> None:
        if self.content_hash != _text_hash(self.content_text):
            raise ValueError("evidence content hash is invalid")
        identity_payload = {
            "source_id": self.source_id,
            "endpoint_id": self.endpoint_id,
            "item_key": self.item_key,
            "item_url": self.item_url,
            "content_hash": self.content_hash,
        }
        expected_id = (
            "evidence-" + hashlib.sha256(canonical_json_bytes(identity_payload)).hexdigest()[:32]
        )
        if self.evidence_id != expected_id:
            raise ValueError("evidence identity is invalid")
        if _validated_item_url(self.item_url) != self.item_url:
            raise ValueError("evidence URL is not canonical")
        for timestamp in (self.published_at, self.updated_at):
            if timestamp is not None and timestamp.tzinfo is None:
                raise ValueError("evidence timestamps must be timezone-aware")
        if _hash(self.payload()) != self.evidence_item_hash:
            raise ValueError("evidence item hash is invalid")


@dataclass(frozen=True)
class EvidencePackage:
    source_registry_hash: str
    source_policy_hash: str
    source_snapshot_hash: str
    fetch_batch_hash: str
    observation_set: ObservationSet
    items: tuple[EvidenceItem, ...]
    evidence_registry_hash: str

    def payload(self) -> dict[str, object]:
        return {
            "source_registry_hash": self.source_registry_hash,
            "source_policy_hash": self.source_policy_hash,
            "source_snapshot_hash": self.source_snapshot_hash,
            "fetch_batch_hash": self.fetch_batch_hash,
            "observation_set_hash": self.observation_set.observation_set_hash,
            "items": [
                item.payload() | {"evidence_item_hash": item.evidence_item_hash}
                for item in self.items
            ],
        }

    def validate(
        self,
        policy: SourcePolicyGeneration,
        snapshot: SourceSnapshot,
        batch: FetchBatch,
    ) -> None:
        batch.validate(policy, snapshot)
        recreated_observations = ObservationSet.create(
            snapshot,
            self.observation_set.observations,
        )
        if recreated_observations != self.observation_set:
            raise ValueError("evidence observation set is invalid")
        if (
            self.source_registry_hash != policy.source_registry_hash
            or self.source_policy_hash != policy.source_policy_hash
            or self.source_snapshot_hash != snapshot.source_snapshot_hash
            or self.fetch_batch_hash != batch.fetch_batch_hash
        ):
            raise ValueError("evidence package proof bindings are inconsistent")
        if tuple(sorted(self.items, key=lambda item: item.evidence_id)) != self.items:
            raise ValueError("evidence items are not in canonical order")
        if len({item.evidence_id for item in self.items}) != len(self.items):
            raise ValueError("evidence IDs must be unique")
        records = {(record.source_id, record.endpoint_id): record for record in batch.records}
        item_counts: Counter[tuple[str, str]] = Counter()
        for item in self.items:
            item.validate()
            key = (item.source_id, item.endpoint_id)
            record = records.get(key)
            if record is None:
                raise ValueError("evidence item references an unknown endpoint")
            if (
                item.source_generation != record.source_generation
                or item.endpoint_generation != record.endpoint_generation
                or item.source_snapshot_hash != snapshot.source_snapshot_hash
                or item.fetch_batch_hash != batch.fetch_batch_hash
                or item.fetch_record_hash != record.fetch_record_hash
            ):
                raise ValueError("evidence item proof binding is inconsistent")
            item_counts[key] += 1
        for observation in self.observation_set.observations:
            key = (observation.source_id, observation.endpoint_id)
            record = records[key]
            if (
                record.outcome is not FetchOutcome.SUCCESS
                and observation.outcome is not ObservationOutcome.FETCH_ERROR
            ):
                raise ValueError("evidence observation does not match fetch outcome")
            expected_count = (
                observation.item_count
                if observation.outcome is ObservationOutcome.COMPLETE_WITH_ITEMS
                else 0
            )
            if item_counts[key] != expected_count:
                raise ValueError("evidence item count does not match observation")
        if _hash(self.payload()) != self.evidence_registry_hash:
            raise ValueError("evidence registry hash is invalid")
        reparsed = EvidenceParser().parse(policy, snapshot, batch)
        if reparsed.observation_set != self.observation_set or reparsed.items != self.items:
            raise ValueError("evidence does not match successful fetch body")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() in {"script", "style", "template"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "template"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


class EvidenceParser:
    """Parse already-fetched bytes; this class has no network collaborator."""

    def __init__(self, limits: EvidenceLimits | None = None) -> None:
        self._limits = limits or EvidenceLimits()

    def parse(
        self,
        policy: SourcePolicyGeneration,
        snapshot: SourceSnapshot,
        batch: FetchBatch,
    ) -> EvidencePackage:
        batch.validate(policy, snapshot)
        items: list[EvidenceItem] = []
        observations: list[TerminalObservation] = []
        total_characters = 0
        for record in batch.records:
            record_items: tuple[EvidenceItem, ...] = ()
            if record.outcome is not FetchOutcome.SUCCESS:
                outcome = ObservationOutcome.FETCH_ERROR
            else:
                try:
                    record_items = self._parse_success(snapshot, batch, record)
                    item_characters = sum(
                        len(item.title) + len(item.content_text) for item in record_items
                    )
                    if total_characters + item_characters > self._limits.max_total_characters:
                        raise ValueError("evidence package character limit exceeded")
                    total_characters += item_characters
                    items.extend(record_items)
                    outcome = (
                        ObservationOutcome.COMPLETE_WITH_ITEMS
                        if record_items
                        else ObservationOutcome.COMPLETE_ZERO
                    )
                except (ElementTree.ParseError, UnicodeError, ValueError):
                    outcome = ObservationOutcome.PARSE_ERROR
                    record_items = ()
            observations.append(
                TerminalObservation(
                    source_id=record.source_id,
                    source_generation=record.source_generation,
                    endpoint_id=record.endpoint_id,
                    endpoint_generation=record.endpoint_generation,
                    window_start=snapshot.window_start,
                    cutoff_at=snapshot.cutoff_at,
                    scope=ObservationScope.PRODUCTION,
                    outcome=outcome,
                    item_count=(
                        len(record_items)
                        if outcome is ObservationOutcome.COMPLETE_WITH_ITEMS
                        else 0
                    ),
                )
            )
        ordered_items = tuple(sorted(items, key=lambda item: item.evidence_id))
        if len({item.evidence_id for item in ordered_items}) != len(ordered_items):
            raise ValueError("evidence IDs must be unique")
        observation_set = ObservationSet.create(snapshot, tuple(observations))
        payload = {
            "source_registry_hash": policy.source_registry_hash,
            "source_policy_hash": policy.source_policy_hash,
            "source_snapshot_hash": snapshot.source_snapshot_hash,
            "fetch_batch_hash": batch.fetch_batch_hash,
            "observation_set_hash": observation_set.observation_set_hash,
            "items": [
                item.payload() | {"evidence_item_hash": item.evidence_item_hash}
                for item in ordered_items
            ],
        }
        return EvidencePackage(
            source_registry_hash=policy.source_registry_hash,
            source_policy_hash=policy.source_policy_hash,
            source_snapshot_hash=snapshot.source_snapshot_hash,
            fetch_batch_hash=batch.fetch_batch_hash,
            observation_set=observation_set,
            items=ordered_items,
            evidence_registry_hash=_hash(payload),
        )

    def _parse_success(
        self,
        snapshot: SourceSnapshot,
        batch: FetchBatch,
        record: FetchRecord,
    ) -> tuple[EvidenceItem, ...]:
        if record.body is None or record.content_type is None:
            raise ValueError("successful fetch is missing its body")
        if record.content_type == "application/feed+json":
            return self._json_feed_items(snapshot, batch, record)
        if b"<!doctype" in record.body.lower() or b"<!entity" in record.body.lower():
            raise ValueError("XML declarations with entities are forbidden")
        root = ElementTree.fromstring(record.body)
        root_name = _local_name(root.tag).lower()
        if root_name == "feed":
            return self._atom_items(snapshot, batch, record, root)
        if root_name != "rss":
            raise ValueError("unsupported feed root")
        channel = next(
            (child for child in root if _local_name(child.tag).lower() == "channel"),
            None,
        )
        if channel is None:
            raise ValueError("RSS channel is required")
        raw_items = [child for child in channel if _local_name(child.tag).lower() == "item"]
        if len(raw_items) > self._limits.max_items_per_endpoint:
            raise ValueError("feed item limit exceeded")
        return tuple(self._rss_item(snapshot, batch, record, raw_item) for raw_item in raw_items)

    def _atom_items(
        self,
        snapshot: SourceSnapshot,
        batch: FetchBatch,
        record: FetchRecord,
        root: ElementTree.Element,
    ) -> tuple[EvidenceItem, ...]:
        raw_items = [child for child in root if _local_name(child.tag).lower() == "entry"]
        if len(raw_items) > self._limits.max_items_per_endpoint:
            raise ValueError("feed item limit exceeded")
        return tuple(self._atom_item(snapshot, batch, record, raw_item) for raw_item in raw_items)

    def _json_feed_items(
        self,
        snapshot: SourceSnapshot,
        batch: FetchBatch,
        record: FetchRecord,
    ) -> tuple[EvidenceItem, ...]:
        if record.body is None:
            raise ValueError("JSON Feed body is required")
        document = json.loads(record.body.decode("utf-8"))
        if not isinstance(document, dict):
            raise ValueError("JSON Feed document must be an object")
        version = document.get("version")
        if version not in {
            "https://jsonfeed.org/version/1",
            "https://jsonfeed.org/version/1.1",
        }:
            raise ValueError("unsupported JSON Feed version")
        raw_items = document.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("JSON Feed items must be an array")
        if len(raw_items) > self._limits.max_items_per_endpoint:
            raise ValueError("feed item limit exceeded")
        return tuple(self._json_item(snapshot, batch, record, raw_item) for raw_item in raw_items)

    def _rss_item(
        self,
        snapshot: SourceSnapshot,
        batch: FetchBatch,
        record: FetchRecord,
        element: ElementTree.Element,
    ) -> EvidenceItem:
        values = {
            _local_name(child.tag).lower(): "".join(child.itertext()).strip() for child in element
        }
        title = _plain_text(values.get("title", ""))
        item_url = _validated_item_url(values.get("link", ""))
        item_key = values.get("guid") or item_url
        content_text = _plain_text(values.get("encoded") or values.get("description") or "")
        if not title or not item_key:
            raise ValueError("RSS item title and identity are required")
        if len(title) + len(content_text) > self._limits.max_item_characters:
            raise ValueError("evidence item character limit exceeded")
        published_at = _parse_datetime(values.get("pubdate"))
        return _build_item(
            snapshot=snapshot,
            batch=batch,
            record=record,
            feed_format=FeedFormat.RSS,
            item_key=item_key,
            item_url=item_url,
            title=title,
            published_at=published_at,
            updated_at=None,
            content_text=content_text,
        )

    def _atom_item(
        self,
        snapshot: SourceSnapshot,
        batch: FetchBatch,
        record: FetchRecord,
        element: ElementTree.Element,
    ) -> EvidenceItem:
        values = {
            _local_name(child.tag).lower(): "".join(child.itertext()).strip()
            for child in element
            if _local_name(child.tag).lower() != "link"
        }
        links = [
            child
            for child in element
            if _local_name(child.tag).lower() == "link"
            and child.attrib.get("href")
            and child.attrib.get("rel", "alternate") == "alternate"
        ]
        if len(links) != 1:
            raise ValueError("Atom entry requires exactly one alternate link")
        item_url = _validated_item_url(links[0].attrib["href"])
        item_key = values.get("id", "")
        title = _plain_text(values.get("title", ""))
        content_text = _plain_text(values.get("content") or values.get("summary") or "")
        if not item_key or not title:
            raise ValueError("Atom entry ID and title are required")
        if len(title) + len(content_text) > self._limits.max_item_characters:
            raise ValueError("evidence item character limit exceeded")
        return _build_item(
            snapshot=snapshot,
            batch=batch,
            record=record,
            feed_format=FeedFormat.ATOM,
            item_key=item_key,
            item_url=item_url,
            title=title,
            published_at=_parse_datetime(values.get("published")),
            updated_at=_parse_datetime(values.get("updated")),
            content_text=content_text,
        )

    def _json_item(
        self,
        snapshot: SourceSnapshot,
        batch: FetchBatch,
        record: FetchRecord,
        raw_item: object,
    ) -> EvidenceItem:
        if not isinstance(raw_item, dict):
            raise ValueError("JSON Feed item must be an object")
        item_key = raw_item.get("id")
        item_url_value = raw_item.get("url")
        title_value = raw_item.get("title")
        if not isinstance(item_key, str) or not item_key.strip():
            raise ValueError("JSON Feed item ID is required")
        if not isinstance(item_url_value, str) or not item_url_value.strip():
            raise ValueError("JSON Feed item URL is required")
        if not isinstance(title_value, str) or not title_value.strip():
            raise ValueError("JSON Feed item ID, URL, and title are required")
        item_url = _validated_item_url(item_url_value)
        title = _plain_text(title_value)
        content_value = raw_item.get("content_text")
        if content_value is None:
            content_value = raw_item.get("content_html", "")
        if not isinstance(content_value, str):
            raise ValueError("JSON Feed content must be text")
        content_text = _plain_text(content_value)
        if len(title) + len(content_text) > self._limits.max_item_characters:
            raise ValueError("evidence item character limit exceeded")
        published_value = raw_item.get("date_published")
        updated_value = raw_item.get("date_modified")
        if published_value is not None and not isinstance(published_value, str):
            raise ValueError("JSON Feed published time must be text")
        if updated_value is not None and not isinstance(updated_value, str):
            raise ValueError("JSON Feed updated time must be text")
        return _build_item(
            snapshot=snapshot,
            batch=batch,
            record=record,
            feed_format=FeedFormat.JSON_FEED,
            item_key=item_key,
            item_url=item_url,
            title=title,
            published_at=_parse_datetime(published_value),
            updated_at=_parse_datetime(updated_value),
            content_text=content_text,
        )


def _build_item(
    *,
    snapshot: SourceSnapshot,
    batch: FetchBatch,
    record: FetchRecord,
    feed_format: FeedFormat,
    item_key: str,
    item_url: str,
    title: str,
    published_at: datetime | None,
    updated_at: datetime | None,
    content_text: str,
) -> EvidenceItem:
    content_hash = _text_hash(content_text)
    identity_payload = {
        "source_id": record.source_id,
        "endpoint_id": record.endpoint_id,
        "item_key": item_key,
        "item_url": item_url,
        "content_hash": content_hash,
    }
    evidence_id = (
        "evidence-" + hashlib.sha256(canonical_json_bytes(identity_payload)).hexdigest()[:32]
    )
    payload = {
        "evidence_id": evidence_id,
        "source_id": record.source_id,
        "source_generation": record.source_generation,
        "endpoint_id": record.endpoint_id,
        "endpoint_generation": record.endpoint_generation,
        "source_snapshot_hash": snapshot.source_snapshot_hash,
        "fetch_batch_hash": batch.fetch_batch_hash,
        "fetch_record_hash": record.fetch_record_hash,
        "feed_format": feed_format.value,
        "item_key": item_key,
        "item_url": item_url,
        "title": title,
        "published_at": _datetime_value(published_at),
        "updated_at": _datetime_value(updated_at),
        "content_text": content_text,
        "content_hash": content_hash,
    }
    return EvidenceItem(
        evidence_id=evidence_id,
        source_id=record.source_id,
        source_generation=record.source_generation,
        endpoint_id=record.endpoint_id,
        endpoint_generation=record.endpoint_generation,
        source_snapshot_hash=snapshot.source_snapshot_hash,
        fetch_batch_hash=batch.fetch_batch_hash,
        fetch_record_hash=record.fetch_record_hash,
        feed_format=feed_format,
        item_key=item_key,
        item_url=item_url,
        title=title,
        published_at=published_at,
        updated_at=updated_at,
        content_text=content_text,
        content_hash=content_hash,
        evidence_item_hash=_hash(payload),
    )


def _plain_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def _validated_item_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("evidence item URL must be credential-free HTTPS")
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return value.strip()
    if not literal.is_global:
        raise ValueError("evidence item URL cannot use a non-public IP literal")
    return value.strip()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        raise ValueError("feed timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _datetime_value(value: datetime | None) -> str | None:
    return value.isoformat(timespec="microseconds") if value is not None else None


def _text_hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()
