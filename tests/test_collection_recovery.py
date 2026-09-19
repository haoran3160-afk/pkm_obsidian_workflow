"""Exercise recovery through real collection, with only network ports replaced."""
from datetime import date
from pathlib import Path

import pytest

from pkm_workflow.ai_daily_luna import run_luna_stage
from pkm_workflow.module_collection import collect_modules
from pkm_workflow.source_catalog import SourceCatalog, SourceDefinition
from pkm_workflow.user_context_v75 import Pillar, UserContextBundle
from pkm_workflow.v75_collection import CollectionPorts, MetadataItem, MetadataObservation


@pytest.mark.parametrize("failure", ["timeout", "network_observation", "fulltext_timeout"])
@pytest.mark.parametrize("recover", [True, False])
def test_real_collection_transient_failure_can_resume_once(tmp_path, failure, recover):
    source = SourceDefinition(
        "vc", "Investment research", "vc", "RSS_ATOM", ("https://example.com/feed",),
        "ACTIVE", "VC_WEEKLY", False, "WEEKLY", ("VC",), "EXPERT",
        "FREE_ENTRY_REQUIRED", (), (), "vc", "KEEP", "APPROVED",
    )
    catalog = SourceCatalog("test", (source,), {"source_quality_scores": {"EXPERT": 2000}}, {"vc": source})
    context = UserContextBundle("hash", {}, (Pillar("VC", 1500),), (), ())
    healthy = False
    calls = []

    def metadata(_source, day):
        calls.append(day)
        if not healthy and failure == "timeout":
            raise TimeoutError("source temporarily unavailable")
        if not healthy and failure == "network_observation":
            return MetadataObservation(False, False, "NETWORK_ERROR", ())
        return MetadataObservation(True, False, "OK", (MetadataItem(
            "article", "AI venture market investment economics", "https://example.com/article",
            str(day), "AI venture investment economics and market framework. " * 20, "essay",
        ),))

    def fulltext(_url):
        if not healthy and failure == "fulltext_timeout":
            raise TimeoutError("body temporarily unavailable")
        return "Detailed AI investment economics, market framework and limitations. " * 40

    def collect(day):
        return collect_modules(day, catalog=catalog, user_context=context, used_urls=set(),
                               ports=CollectionPorts(metadata, fulltext), requested_sections=("vc",))

    def prepare(**kwargs):
        return run_luna_stage(
            "prepare", runtime_root=tmp_path, vault_daily_dir=tmp_path, today=date(2026, 9, 8),
            collect=collect, context_loader=lambda: {"fields": {}, "user_context_hash": "sha256:" + "a" * 64},
            **kwargs,
        )

    first = prepare()
    assert first["status"] == "COLLECTION_FAILED"
    assert first["retryable"] is True
    assert not list(tmp_path.glob("scratch/runs/*/generator-input.json"))
    assert prepare()["status"] == "COLLECTION_FAILED"
    assert len(calls) == 1
    healthy = recover
    second = prepare(run_id=first["run_id"])
    # Only VC was supplied; missing Builder is a terminal shortage, not a network retry.
    assert second["status"] == ("MODULE_SOURCES_INCOMPLETE" if recover else "COLLECTION_RETRY_EXHAUSTED")
    assert second["run_id"] == first["run_id"]
    assert prepare(run_id=first["run_id"])["status"] == second["status"]
    assert len(calls) == 2
    assert not list(tmp_path.glob("*.md"))
    assert len(list(tmp_path.glob("scratch/runs/*"))) == 1
    assert not (Path(second["report_path"]).parent / "repair.json").exists()


@pytest.mark.parametrize("reason", ["ZERO_YIELD", "HTTP_404", "SSL_ERROR"])
def test_nontransient_shortage_does_not_recollect(tmp_path, reason):
    source = SourceDefinition("vc", "VC", "vc", "RSS_ATOM", ("https://example.com/feed",),
                              "ACTIVE", "VC_WEEKLY", False, "DAILY", ("VC",), "EXPERT",
                              "FREE_ENTRY_REQUIRED", (), (), "vc", "KEEP", "APPROVED")
    catalog = SourceCatalog("test", (source,), {"source_quality_scores": {}}, {"vc": source})
    context = UserContextBundle("hash", {}, (Pillar("VC", 1500),), (), ())
    calls = []
    def metadata(*_args):
        calls.append(1)
        return MetadataObservation(reason == "ZERO_YIELD", reason == "ZERO_YIELD", reason, ())
    def collect(day):
        return collect_modules(day, catalog=catalog, user_context=context, used_urls=set(),
                               ports=CollectionPorts(metadata, lambda _: ""), requested_sections=("vc",))
    def prepare(**kwargs):
        return run_luna_stage("prepare", runtime_root=tmp_path, vault_daily_dir=tmp_path,
                              today=date(2026, 9, 8), collect=collect,
                              context_loader=lambda: {"fields": {}, "user_context_hash": "sha256:" + "a" * 64},
                              **kwargs)
    result = prepare()
    assert result["status"] in {"COVERAGE_INSUFFICIENT", "EVIDENCE_INSUFFICIENT"}
    assert prepare(run_id=result["run_id"])["status"] == result["status"]
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["TIMEOUT", "CHUNKED", "HTTP_503", "HTTP_404", "SSL"])
def test_fulltext_adapter_preserves_only_transient_transport_errors(monkeypatch, failure):
    import requests

    import fetcher
    from pkm_workflow.v75_collection import default_collection_ports

    def get(*args, **kwargs):
        if failure == "TIMEOUT":
            raise requests.Timeout("temporary outage")
        if failure == "CHUNKED":
            raise requests.exceptions.ChunkedEncodingError("incomplete response")
        if failure == "SSL":
            raise requests.exceptions.SSLError("certificate invalid")
        response = requests.Response()
        response.status_code = int(failure.split("_")[1])
        return response

    monkeypatch.setattr(fetcher.HTTP_SESSION, "get", get)
    catalog = SourceCatalog("test", (), {"limits": {
        "metadata_summary_chars": 1000, "fulltext_chars": 12000, "access_probe_chars": 24000,
    }}, {})
    port = default_collection_ports(catalog).fetch_fulltext
    if failure in {"TIMEOUT", "CHUNKED", "HTTP_503"}:
        with pytest.raises(OSError):
            port("https://8.8.8.8/article")
    else:
        assert port("https://8.8.8.8/article") == ""


def test_github_transport_failure_is_retryable_not_an_empty_module():
    context = UserContextBundle("hash", {}, (), (), ())
    def github(_endpoint):
        raise TimeoutError("temporary GitHub outage")
    result = collect_modules(
        date(2026, 9, 9), catalog=SourceCatalog("test", (), {"source_quality_scores": {}}, {}), user_context=context,
        used_urls=set(), ports=CollectionPorts(lambda *_: None, lambda _: ""),
        requested_sections=("github",), get_github=github,
    )
    assert result.audit.get("retryable_collection_failure") is True


def test_paper_transport_failure_remains_distinct_from_missing_html():
    from pkm_workflow.paper_collection import paper_candidates
    def offline(_url):
        raise TimeoutError("arXiv unavailable")
    candidates, audit = paper_candidates(date(2026, 9, 7), set(), get_text=offline)
    assert candidates == []
    assert audit.get("retryable") is True


@pytest.mark.parametrize("status", [429, 503])
def test_rss_server_failure_is_not_healthy_zero_yield(monkeypatch, status):
    from types import SimpleNamespace

    import fetcher

    monkeypatch.setattr(fetcher.feedparser, "parse", lambda *_: SimpleNamespace(entries=[], bozo=False))
    monkeypatch.setattr(fetcher.HTTP_SESSION, "get", lambda *a, **k: SimpleNamespace(
        status_code=status, headers={"content-type": "application/xml"}))
    _, observation = fetcher.fetch_rss_feed(
        {"name": "Example", "url": "https://example.com/rss", "note_folder": "unused", "domain": "ai-news"},
        {}, "2026-09-08", raw_only=True, return_meta=True,
    )
    assert observation["reason_code"] == "NETWORK_ERROR"


def test_paper_tls_error_does_not_retry(monkeypatch):
    import requests

    from pkm_workflow.paper_collection import paper_candidates

    def invalid_certificate(*args, **kwargs):
        raise requests.exceptions.SSLError("certificate invalid")
    monkeypatch.setattr(requests, "get", invalid_certificate)
    candidates, audit = paper_candidates(date(2026, 9, 7), set())
    assert candidates == []
    assert audit["retryable"] is False


def test_fulltext_temporary_dns_error_remains_retryable(monkeypatch):
    import socket

    from pkm_workflow.v75_collection import default_collection_ports

    def temporary_dns(*args, **kwargs):
        raise socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure")
    monkeypatch.setattr(socket, "getaddrinfo", temporary_dns)
    catalog = SourceCatalog("test", (), {"limits": {
        "metadata_summary_chars": 1000, "fulltext_chars": 12000, "access_probe_chars": 24000,
    }}, {})
    with pytest.raises(OSError):
        default_collection_ports(catalog).fetch_fulltext("https://example.com/article")


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError])
def test_missing_or_blocked_github_cli_is_not_a_network_retry(monkeypatch, error):
    import subprocess

    from pkm_workflow.module_collection import github_candidates

    def unavailable(*args, **kwargs):
        raise error("local executable unavailable")
    monkeypatch.setattr(subprocess, "run", unavailable)
    candidates, failures = github_candidates(date(2026, 9, 9), set())
    assert candidates == []
    assert all(row["reason"] != "GITHUB_NETWORK_ERROR" for row in failures)
