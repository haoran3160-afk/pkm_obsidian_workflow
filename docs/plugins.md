# Writing Custom Source Plugins

The **Source Plugin Registry** (`fetcher_registry.py`) lets you add new data sources — like Reddit, Twitter, or any web API — without modifying any core files.

## How It Works

All fetchers are registered with the `@register_fetcher("type")` decorator. At runtime, the orchestrator dispatches to the correct function based on the `"type"` field in your config.

Built-in types: `rss`, `youtube`, `youtube_raw`.

## Creating a Plugin

### 1. Create your plugin file

```python
# plugins/my_reddit_fetcher.py
from fetcher_registry import register_fetcher

@register_fetcher("reddit")
def fetch_reddit(config: dict, cache: dict, today: str, **kwargs) -> list[dict]:
    """
    Fetch posts from a Reddit community.
    
    Returns a list of dicts with keys: title, link, guid, summary, folder.
    """
    import requests

    subreddit = config.get("subreddit", "MachineLearning")
    folder = config.get("note_folder", "30-Daily/AI-News")
    url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=10"
    headers = {"User-Agent": "pkm-workflow/2.1"}

    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    posts = resp.json()["data"]["children"]

    results = []
    for post in posts:
        d = post["data"]
        guid = d["id"]
        if guid in cache:
            continue
        cache[guid] = today
        results.append({
            "title": d["title"],
            "link": f"https://reddit.com{d['permalink']}",
            "guid": guid,
            "summary": d.get("selftext", "")[:300],
            "folder": folder,
        })
    return results
```

### 2. Register it in your config

```json
{
  "rss_feeds": [
    {
      "type": "reddit",
      "name": "r/MachineLearning",
      "subreddit": "MachineLearning",
      "note_folder": "30-Daily/AI-News"
    }
  ]
}
```

!!! note
    The `type` field tells the dispatcher which fetcher to call. If omitted, it defaults to `"rss"`.

### 3. Import your plugin before running

```python
# In your entry point or a local `user_plugins.py`:
import plugins.my_reddit_fetcher  # registers the decorator
```

## Plugin Contract

Your fetcher function **must**:

- Accept `(config: dict, cache: dict, today: str, **kwargs)` signature
- Return `list[dict]` where each dict has at minimum: `title`, `link`, `guid`, `summary`, `folder`
- Update `cache[guid] = today` for items that should be deduplicated on future runs
- **Not** perform any file I/O or Markdown formatting (that's the Transform layer)
- Use `tenacity` for retry logic on any network calls

## Listing Registered Fetchers

```python
from fetcher_registry import list_registered
print(list_registered())  # ['reddit', 'rss', 'youtube', 'youtube_raw']
```
