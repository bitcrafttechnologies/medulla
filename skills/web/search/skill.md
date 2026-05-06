# skill: web/search

version:     1.0.0
description: Web search via DuckDuckGo and direct URL fetch with domain whitelist.
timeout_ms:  15000

params:
  mode:        string — "search" or "fetch"
  query:       string — search query (required if mode=search)
  max_results: integer — max results to return (optional, default 5, mode=search only)
  url:         string — URL to fetch (required if mode=fetch)
  max_chars:   integer — max characters of extracted text (optional, default 8000, mode=fetch only)

returns (mode=search):
  results: list of { url, title, snippet }

returns (mode=fetch):
  url:  string — the fetched URL
  text: string — extracted plain text (stripped of HTML)

example response (mode=search):
  {
    "results": [
      { "url": "https://example.com", "title": "Example", "snippet": "An example page." }
    ]
  }

example response (mode=fetch):
  { "url": "https://docs.python.org/3/", "text": "Python 3 documentation..." }

notes:
  - search uses DuckDuckGo HTML endpoint — no API key required
  - fetch is gated by skills/web/search/whitelist.json
  - whitelist is user-maintained; Echo Core cannot write it
  - an empty or missing whitelist.json allows all fetches
