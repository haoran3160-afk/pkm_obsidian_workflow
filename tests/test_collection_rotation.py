import base64
from datetime import date

import pytest

from pkm_workflow.module_collection import _due_for_reading_day, collect_modules
from pkm_workflow.source_catalog import SourceCatalog, SourceDefinition
from pkm_workflow.user_context_v75 import Pillar, UserContextBundle
from pkm_workflow.v75_collection import CollectionPorts, MetadataItem, MetadataObservation


@pytest.mark.parametrize("published_url", ["https://example.com/read/", "https://example.com/read/#section",
                                          "https://example.com/read/?utm_source=daily&utm_campaign=ai"])
def test_only_selected_modules_fetch_and_published_slash_variant_is_not_recommended(published_url):
    def source(identifier, pillar):
        return SourceDefinition(identifier, identifier, identifier, "RSS_ATOM", ("https://example.com/feed",),
                                "ACTIVE", "COGNITIVE_WEEKLY", False, "DAILY", (pillar,), "EXPERT",
                                "FREE_ENTRY_REQUIRED", (), (), identifier, "KEEP", "APPROVED")
    sources = (source("cognition", "COGNITION"), source("ai", "AI_MASTERY"))
    catalog = SourceCatalog("test", sources, {"source_quality_scores": {"EXPERT": 2000}}, {s.source_id: s for s in sources})
    context = UserContextBundle("hash", {}, (Pillar("COGNITION", 1000), Pillar("AI_MASTERY", 2500)), (), ())
    seen = []
    def metadata(item, day):
        seen.append(item.source_id)
        return MetadataObservation(True, False, "OK", (MetadataItem(
            "read", "Thinking frameworks assumptions incentives", "https://example.com/read/", str(day),
            "Decision framework and mental model assumptions. " * 20, "essay"),))
    def github(endpoint):
        if endpoint.endswith("/readme"):
            return {"content": base64.b64encode(b"Agent evaluation and tool usage. " * 30).decode()}
        repo = endpoint.removeprefix("repos/")
        return {"html_url": f"https://github.com/{repo}", "full_name": repo, "private": False,
                "archived": False, "license": {"spdx_id": "MIT"}, "pushed_at": "2026-09-08"}
    result = collect_modules(date(2026, 9, 9), catalog=catalog, user_context=context,
                             used_urls={published_url},
                             ports=CollectionPorts(metadata, lambda _: "Decision framework assumptions. " * 50),
                             get_github=github, requested_sections=("cognition", "github"))
    assert seen == ["cognition"]
    assert not any(item.story_type == "cognition" for item in result.candidates)
    assert "cognition" in result.audit["missing_modules"]


def test_weekly_sources_are_available_on_both_module_reading_days():
    source = SourceDefinition("slow", "Slow essay", "slow", "RSS_ATOM", ("https://example.com/rss",),
                              "ACTIVE", "COGNITIVE_WEEKLY", False, "WEEKLY", ("COGNITION",),
                              "EXPERT", "FREE_ENTRY_REQUIRED", (), (), "slow", "KEEP", "APPROVED")
    assert _due_for_reading_day(source, date(2026, 9, 9), ("cognition", "github"))
    assert _due_for_reading_day(source, date(2026, 9, 12), ("cognition", "github"))
    assert not _due_for_reading_day(source, date(2026, 9, 8), ("builder", "vc"))


@pytest.mark.parametrize("day,pillar,section,pair", [
    (date(2026, 9, 11), "VC", "vc", ("builder", "vc")),
    (date(2026, 9, 12), "COGNITION", "cognition", ("cognition", "github")),
])
def test_second_reading_day_fetches_weekly_source_instead_of_inventing_shortage(day, pillar, section, pair):
    source = SourceDefinition("slow", "Slow source", "slow", "RSS_ATOM", ("https://example.com/feed",),
                              "ACTIVE", "VC_WEEKLY" if section == "vc" else "COGNITIVE_WEEKLY",
                              False, "WEEKLY", (pillar,), "EXPERT", "FREE_ENTRY_REQUIRED", (), (),
                              "slow", "KEEP", "APPROVED")
    catalog = SourceCatalog("test", (source,), {"source_quality_scores": {"EXPERT": 2000}}, {"slow": source})
    context = UserContextBundle("hash", {}, (Pillar(pillar, 1500),), (), ())
    calls = []
    def metadata(item, content_date):
        calls.append(item.source_id)
        title = "AI venture market investment economics" if section == "vc" else "Thinking framework incentives decisions"
        return MetadataObservation(True, False, "OK", (MetadataItem(
            "item", title, "https://example.com/unused", str(content_date), (title + ". ") * 20, "essay"),))
    def no_github(_endpoint):
        raise ValueError("OFFLINE_TEST")
    result = collect_modules(day, catalog=catalog, user_context=context, used_urls=set(),
                             ports=CollectionPorts(metadata, lambda _: "Detailed framework, investment economics and limitations. " * 30),
                             get_github=no_github, requested_sections=pair)
    assert calls == ["slow"]
    assert any(candidate.story_type == section for candidate in result.candidates)


def test_url_dedup_keeps_semantic_query_parameters():
    from pkm_workflow.v75_collection import _normalize_url
    assert _normalize_url("https://example.com/article?id=1") != _normalize_url("https://example.com/article?id=2")
