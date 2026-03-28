import formatter


def test_slugify_returns_fallback_for_symbol_only_titles():
    assert formatter.slugify("!!!@@@###") == "untitled"


def test_slugify_preserves_text_and_trims():
    slug = formatter.slugify("  My Title: GPT / LLM  ")
    assert slug == "My-Title-GPT-LLM"


def test_format_daily_digest_raw_only_contains_sections():
    path, content = formatter.format_daily_digest(
        {
            "HackerNews": [
                {
                    "title": "Post",
                    "link": "https://example.com/post",
                    "summary": "hello",
                }
            ]
        },
        raw_only=True,
    )

    assert path.startswith("00-Inbox/Raw-Feeds/Raw-Daily-Feeds-")
    assert path.endswith(".md")
    assert "# Raw Daily Feeds -" in content
    assert "## HackerNews" in content
    assert "**URL**: https://example.com/post" in content


def test_format_video_note_builds_expected_path_and_body():
    path, content = formatter.format_video_note(
        {
            "channel_name": "Demo Channel",
            "title": "My Video: Intro",
            "published": "2026-03-26",
            "folder": "20-Sources/Videos",
            "domain": "AI-Stack",
            "link": "https://youtube.com/watch?v=1",
            "summary": "Summary",
        }
    )

    assert path == "20-Sources/Videos/2026-03-26-Demo-Channel-My-Video-Intro.md"
    assert "My Video: Intro" in content
    assert "https://youtube.com/watch?v=1" in content
