"""
skill: web/search

Two capabilities, selected by the 'mode' param:

  mode=search — query DuckDuckGo HTML endpoint, return titles/URLs/snippets.
                No API key required.

  mode=fetch  — fetch a URL and return extracted plain text.
                Gated by whitelist.json in this directory.
                Whitelist is user-maintained; this skill cannot modify it.
"""

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import certifi

WHITELIST_PATH = Path(__file__).parent / "whitelist.json"
_USER_AGENT    = "Mozilla/5.0 (compatible; Echo/1.0)"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(params: dict) -> dict:
    mode = params.get("mode")
    ##!! FUTURE: Cache the search result and fetched pages in the scratchpad, with a TTL. This would speed up repeated queries and allow Echo to "remember" what it found.
    #with sp.capture("result") as captured:
    if mode == "search":
        result = _search(params)
    elif mode == "fetch":
        result = _fetch(params)
    else:
        raise ValueError(f"Unknown mode: '{mode}'. Expected 'search' or 'fetch'.")
    return result


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _search(params: dict) -> dict:
    query = params.get("query", "").strip()
    if not query:
        raise ValueError("'query' param is required for mode=search")
    max_results = int(params.get("max_results", 5))

    encoded = urllib.parse.quote_plus(query)
    url     = f"https://html.duckduckgo.com/html/?q={encoded}"
    req     = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

    # Use certifi's CA bundle
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())

    try:
        with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"DuckDuckGo request failed: {exc}") from exc


    results = _parse_ddg_html(html, max_results)
    return {"results": results}


def _parse_ddg_html(html: str, max_results: int) -> list[dict]:
    """
    Extract result blocks from DuckDuckGo's HTML response.
    DDG's HTML layout is stable and doesn't require JS, which is why
    we use the html. endpoint instead of the API.
    """
    results = []

    # Each result is in a <div class="result ..."> block.
    # Title link: <a class="result__a" href="...">title</a>
    # Snippet:    <a class="result__snippet" ...>snippet</a>
    block_re   = re.compile(
        r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
        r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    for m in block_re.finditer(html):
        href, title_raw, snippet_raw = m.groups()
        title   = _strip_tags(title_raw).strip()
        snippet = _strip_tags(snippet_raw).strip()
        if title and href:
            results.append({"url": href, "title": title, "snippet": snippet})
        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch(params: dict) -> dict:
    url = params.get("url", "").strip()
    if not url:
        raise ValueError("'url' param is required for mode=fetch")
    max_chars = int(params.get("max_chars", 8000))

    whitelist = _load_whitelist()
    if not _is_allowed(url, whitelist):
        raise PermissionError(
            f"Domain not in fetch whitelist: {url}. "
            "Edit ~/.echospace/skills/web/search/whitelist.json to allow it."
        )

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Fetch failed: {exc}") from exc

    text = _extract_text(raw)
    return {"url": url, "text": text[:max_chars]}


def _load_whitelist() -> list[str]:
    if not WHITELIST_PATH.exists():
        return []
    try:
        data = json.loads(WHITELIST_PATH.read_text(encoding="utf-8"))
        return [str(d).lower() for d in data] if isinstance(data, list) else []
    except Exception:
        return []


def _is_allowed(url: str, whitelist: list[str]) -> bool:
    """
    Empty whitelist = all fetches permitted.
    Match rules:
      "wikipedia.org" → matches wikipedia.org and *.wikipedia.org
      "www.github.com" → matches github.com and www.github.com
    """
    if not whitelist:
        return True

    parsed = urllib.parse.urlparse(url)
    domain = parsed.netloc.lower().lstrip("www.")

    for entry in whitelist:
        entry = entry.lstrip("www.")
        if domain == entry or domain.endswith("." + entry):
            return True
    return False


# ---------------------------------------------------------------------------
# HTML → plain text
# ---------------------------------------------------------------------------

def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def _extract_text(html: str) -> str:
    """Strip scripts, styles, then all tags, then collapse whitespace."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>",  " ", text, flags=re.DOTALL)
    text = _strip_tags(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
