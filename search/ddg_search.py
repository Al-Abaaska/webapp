"""
Multi-provider text search — drop-in replacement for ddgs.DDGS().
Tries Startpage → DuckDuckGo → SearXNG with automatic fallback and retry logic.
"""
from .multi_search import multi_search


def ddg_text_search(query, max_results=8):
    """Search using multiple providers. Returns list of dicts: {title, body, href}"""
    results = multi_search(query, max_results)
    return [{'title': r['title'], 'body': r['body'], 'href': r['href']} for r in results]


class DDGS:
    """Drop-in replacement for ddgs.DDGS().text()"""
    def text(self, query, max_results=8):
        return ddg_text_search(query, max_results)
