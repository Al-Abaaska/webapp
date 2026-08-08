"""
DuckDuckGo Search + Date Extraction.
Free, no API key needed.
"""
import re
import requests
from .ddg_search import DDGS
from .multi_search import clean_text
from concurrent.futures import ThreadPoolExecutor, as_completed


def google_search(query, num_results=10):
    """Search DuckDuckGo and extract dates from result pages."""
    try:
        results = DDGS().text(query, max_results=min(num_results, 10))
        formatted = []
        for r in results:
            link = r.get('href', '')
            formatted.append({
                'title': clean_text(r.get('title', '')),
                'snippet': clean_text(r.get('body', '')),
                'url': link,
                'source': _detect_source(link),
                'platform': _detect_platform(link),
                'date': _extract_date_from_url(link),
            })

        urls_needing_fetch = [item for item in formatted if not item['date']]
        if urls_needing_fetch:
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(_extract_date_from_page, item['url']): item
                          for item in urls_needing_fetch}
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        d = future.result()
                        if d:
                            item['date'] = d
                    except Exception:
                        pass

        return formatted
    except Exception as e:
        print(f'[DuckDuckGo] Search failed: {e}')
        return []


def _extract_date_from_url(url):
    """Extract date from URL path like /2026/07/17/."""
    m = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', url)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    return ''


def _extract_date_from_page(url):
    """Fetch page and extract date from meta tags."""
    try:
        resp = requests.get(url, timeout=4, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }, allow_redirects=True)
        if resp.status_code != 200:
            return ''

        html = resp.text[:30000]

        for attr in ['article:published_time', 'datePublished', 'publish_date',
                      'sailthru.date', 'Date', 'parsely-pub-date']:
            m = re.search(rf'<meta[^>]+(?:property|name)=["\']?{attr}["\']?[^>]+content=["\']([^"\']+)["\']', html, re.I)
            if not m:
                m = re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']?{attr}', html, re.I)
            if m:
                return _parse_date(m.group(1))

        m = re.search(r'<time[^>]+datetime=["\']([^"\']+)', html)
        if m:
            return _parse_date(m.group(1))

        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
        if m:
            return _parse_date(m.group(1))

        return ''
    except Exception:
        return ''


def _parse_date(raw):
    """Normalize date string to YYYY-MM-DD format."""
    if not raw:
        return ''
    raw = raw.strip()[:30]
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', raw)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    m = re.match(r'(\d{4})/(\d{2})/(\d{2})', raw)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    return raw[:10] if len(raw) >= 10 else raw


def _detect_platform(url):
    """Detect the platform from URL."""
    url_lower = url.lower()
    if 'facebook.com' in url_lower:
        return 'Facebook'
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'YouTube'
    if 'twitter.com' in url_lower or 'x.com' in url_lower:
        return 'X/Twitter'
    if 'tiktok.com' in url_lower:
        return 'TikTok'
    return 'Website'


def _detect_source(url):
    """Extract source name from URL."""
    from urllib.parse import urlparse
    try:
        domain = urlparse(url).netloc.replace('www.', '')
        parts = domain.split('.')
        if len(parts) >= 2:
            return parts[-2].capitalize()
        return domain
    except Exception:
        return 'Unknown'
