"""Small primary-paper reader: one topical query, then unread dated classics."""
from __future__ import annotations

import hashlib
import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urlencode
from xml.etree import ElementTree

from .v75_collection import Candidate


def research_topic(day):
    # Research is scheduled Mon/Thu: retain an Agent anchor and rotate the second
    # slot, otherwise restricting collection days would silently eliminate RL/DL.
    return ("rl", "deep_learning", "agents")[day.isocalendar().week % 3] if day.weekday() == 3 else "agents"
QUERIES = {
    "agents": 'ti:agentic OR ti:harness OR (ti:agent AND (abs:evaluation OR abs:tool OR abs:reasoning))',
    "rl": 'ti:"reinforcement learning" AND (abs:language OR abs:reasoning OR abs:agent)',
    "deep_learning": 'cat:cs.LG AND (ti:representation OR ti:generalization OR ti:efficient)',
}
CLASSICS = {
    "agents": ("2405.15793", "2303.11366", "2305.16291", "2302.04761"),
    "rl": ("1707.06347", "2305.18290", "1801.01290"),
    "deep_learning": ("1706.03762", "2106.09685", "2010.11929"),
}
ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?$")


def public_text(url):
    import requests
    response = requests.get(url, timeout=15, headers={"User-Agent": "Personal-AI-Daily/1.0"})
    if response.status_code != 200:
        raise ValueError(f"ARXIV_HTTP_{response.status_code}")
    return response.text


def _paper_id(value):
    match = ARXIV_ID.search(value.rstrip("/"))
    return match.group(1) if match else None


class _CitationMeta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta" and values.get("name", "").startswith("citation_"):
            self.values[values["name"]] = values.get("content", "")


def paper_candidates(day, used_urls, *, get_text=public_text, fulltext=None):
    if fulltext is None:
        from fetcher import _fetch_article_fulltext
        def fulltext(url):
            return _fetch_article_fulltext(url, max_chars=120_000)
    topic = research_topic(day)
    used = {_paper_id(url) for url in used_urls}
    audit = {"topic": topic, "discovery_error": None, "fulltext_attempts": 0, "excluded": []}
    rows = []
    query = urlencode({"search_query": QUERIES[topic], "max_results": 8,
                       "sortBy": "submittedDate", "sortOrder": "descending"})
    try:
        root = ElementTree.fromstring(get_text("https://export.arxiv.org/api/query?" + query))
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns):
            identifier = _paper_id(entry.findtext("a:id", "", ns))
            published = entry.findtext("a:published", "", ns)[:10]
            if identifier and 0 <= (day - date.fromisoformat(published)).days <= 30:
                rows.append((identifier, entry.findtext("a:title", "", ns).strip(), published, False))
    except (OSError, ValueError, ElementTree.ParseError) as error:
        audit["discovery_error"] = str(error)[:120]

    def metadata(identifier, classic):
        meta = _CitationMeta()
        meta.feed(get_text(f"https://arxiv.org/abs/{identifier}"))
        published = meta.values["citation_date"].replace("/", "-")[:10]
        age = (day - date.fromisoformat(published)).days
        if age < 0 or (not classic and age > 30):
            return None
        return identifier, meta.values["citation_title"], published, classic

    if not rows:
        # Official RSS is a bounded discovery fallback, never full-paper evidence.
        import feedparser
        category = "cs.AI" if topic == "agents" else "cs.LG"
        pattern = {"agents": r"agent|harness|tool.use", "rl": r"reinforcement|policy optim|reward",
                   "deep_learning": r"representation|generalization|efficient|transformer"}[topic]
        try:
            feed = feedparser.parse(get_text(f"https://rss.arxiv.org/rss/{category}"))
            matched = [entry for entry in feed.entries
                       if re.search(pattern, entry.get("title", ""), re.I)][:4]
            for entry in matched:
                identifier = _paper_id(entry.get("link", ""))
                if identifier and identifier not in used:
                    try:
                        row = metadata(identifier, False)
                        if row:
                            rows.append(row)
                    except (OSError, ValueError, KeyError):
                        continue
        except (OSError, ValueError) as error:
            audit["rss_error"] = str(error)[:120]

    found = []
    attempted = set()
    def consume(identifier, title, published, classic):
        if identifier in used or identifier in attempted or audit["fulltext_attempts"] >= 4:
            return
        attempted.add(identifier)
        audit["fulltext_attempts"] += 1
        try:
            text = fulltext(f"https://arxiv.org/html/{identifier}")
        except (OSError, ValueError):
            text = ""
        # Abstract-only responses and error pages cannot support a research takeaway.
        if len(text.strip()) < 3000 or not re.search(r"method|experiment|results|theorem", text, re.I):
            audit["excluded"].append({"paper_id": identifier, "reason": "PAPER_BODY_UNAVAILABLE"})
            return
        canonical = f"https://arxiv.org/abs/{identifier}"
        found.append(Candidate(
            evidence_id="paper-" + hashlib.sha256(identifier.encode()).hexdigest()[:24],
            source="arXiv", title=title, link=canonical, published=published,
            summary=f"Paper: {title}\nOriginal submission: {published}\nResearch topic: {topic}\n"
                    "Publication status: arXiv manuscript; peer-review/venue not verified.\n"
                    "Primary paper text follows:\n" + text,
            content_type="evergreen" if classic else "paper", fulltext_enriched=True,
            source_id="research_arxiv_cs_ai", canonical_origin_id=identifier,
            evidence_role="PAPER_PRIMARY", pillars=("AGENTIC_RESEARCH",),
            story_type="research", editorial_score=8500,
        ))
    for row in rows:
        consume(*row)
        if len(found) == 2:
            break
        if not found and audit["fulltext_attempts"] >= 2:
            # Keep the other two attempts for classics when fresh HTML is missing.
            break
    for identifier in CLASSICS[topic]:
        if len(found) == 2 or audit["fulltext_attempts"] >= 4:
            break
        if identifier in used or identifier in attempted:
            continue
        try:
            row = metadata(identifier, True)
            if row:
                consume(*row)
        except (OSError, ValueError, KeyError):
            audit["excluded"].append({"paper_id": identifier, "reason": "PAPER_METADATA_UNAVAILABLE"})
    return found, audit
