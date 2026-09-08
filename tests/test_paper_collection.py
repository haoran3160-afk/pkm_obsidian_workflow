from datetime import date

from pkm_workflow.paper_collection import paper_candidates


def test_rate_limited_discovery_uses_dated_unread_primary_paper():
    calls = []
    def get(url):
        calls.append(url)
        if "api/query" in url:
            raise ValueError("ARXIV_HTTP_429")
        return '<meta name="citation_title" content="Agent interfaces"><meta name="citation_date" content="2024/05/06">'
    text = "Introduction: agent interface design.\nMethod: tools and feedback.\n" + "Experiment and limitations of the benchmark. " * 100
    candidates, audit = paper_candidates(date(2026, 9, 8), set(), get_text=get, fulltext=lambda _: text)
    assert candidates
    assert all(c.evidence_role == "PAPER_PRIMARY" for c in candidates)
    assert all(c.content_type == "evergreen" and c.published == "2024-05-06" for c in candidates)
    assert "ARXIV_HTTP_429" in audit["discovery_error"]
    assert audit["fulltext_attempts"] <= 4
    assert sum("api/query" in url for url in calls) == 1


def test_abstract_only_is_not_full_paper_evidence():
    def get(url):
        if "api/query" in url:
            return '<feed xmlns="http://www.w3.org/2005/Atom" />'
        return '<meta name="citation_title" content="Agent"><meta name="citation_date" content="2024/05/06">'
    candidates, audit = paper_candidates(date(2026, 9, 8), set(), get_text=get, fulltext=lambda _: "Abstract only")
    assert candidates == []
    assert audit["fulltext_attempts"] == 4


def test_recent_paper_keeps_original_date_and_deduplicates_versions():
    xml = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <id>http://arxiv.org/abs/2609.01234v2</id><title>Agent harness learning</title>
    <published>2026-09-03T00:00:00Z</published><summary>Agent evaluation</summary>
    </entry></feed>'''
    def get(url):
        if "api/query" in url:
            return xml
        raise ValueError("UNAVAILABLE")
    body = "Method and related work. Results and limitations. " * 100
    candidates, _ = paper_candidates(date(2026, 9, 8), set(), get_text=get, fulltext=lambda _: body)
    assert candidates[0].link == "https://arxiv.org/abs/2609.01234"
    assert candidates[0].published == "2026-09-03"
    assert candidates[0].content_type == "paper"
    used, _ = paper_candidates(date(2026, 9, 8), {"https://arxiv.org/abs/2609.01234v1"}, get_text=get, fulltext=lambda _: body)
    assert used == []


def test_official_rss_fallback_verifies_original_submission_not_feed_date():
    def get(url):
        if "api/query" in url:
            raise ValueError("ARXIV_HTTP_429")
        if "rss.arxiv.org" in url:
            return '<rss version="2.0"><channel><item><title>Agent harness learning</title><link>https://arxiv.org/abs/2609.01234</link><pubDate>Tue, 08 Sep 2026 00:00:00 GMT</pubDate></item></channel></rss>'
        if url.endswith("2609.01234"):
            return '<meta name="citation_title" content="Agent harness learning"><meta name="citation_date" content="2026/09/03">'
        raise ValueError("UNAVAILABLE")
    candidates, audit = paper_candidates(date(2026, 9, 8), set(), get_text=get,
                                         fulltext=lambda _: "Method and results with limitations. " * 100)
    assert candidates[0].published == "2026-09-03"
    assert candidates[0].content_type == "paper"
    assert audit["discovery_error"] == "ARXIV_HTTP_429"


def test_recent_html_failures_leave_budget_for_unread_classics():
    xml = '<feed xmlns="http://www.w3.org/2005/Atom">' + ''.join(
        f'<entry><id>http://arxiv.org/abs/2609.0123{i}</id><title>Agent</title><published>2026-09-03T00:00:00Z</published></entry>'
        for i in range(4)
    ) + '</feed>'
    def get(url):
        if "api/query" in url:
            return xml
        return '<meta name="citation_title" content="Classic agent"><meta name="citation_date" content="2024/05/06">'
    calls = []
    def body(url):
        calls.append(url)
        return "" if "2609." in url else "Method and results with limitations. " * 100
    candidates, audit = paper_candidates(date(2026, 9, 8), set(), get_text=get, fulltext=body)
    assert candidates and all(c.content_type == "evergreen" for c in candidates)
    assert audit["fulltext_attempts"] <= 4
    assert any("2609." not in url for url in calls)
