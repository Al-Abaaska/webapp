"""
Facebook Posts Search — tries Apify first, falls back to Startpage search.
"""
import re
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit, urlunsplit


APIFY_TOKEN = os.getenv('APIFY_TOKEN', '')


def search_facebook_posts(queries, max_posts=10, original_text=''):
    """Combine direct Facebook retrieval with indexed-web Facebook results."""
    with ThreadPoolExecutor(max_workers=2) as executor:
        direct_future = executor.submit(
            _search_via_apify, queries, max_posts, original_text
        )
        indexed_future = executor.submit(
            _search_via_startpage, queries, original_text, max_posts
        )
        groups = [direct_future.result() or [], indexed_future.result() or []]

    posts = []
    seen = set()
    for group in groups:
        for post in group:
            key = _canonical_url(post.get('url', ''))
            if not key or key in seen:
                continue
            seen.add(key)
            posts.append(post)
    return posts[:max_posts]


def _canonical_url(url):
    try:
        parts = urlsplit(url or '')
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip('/'), '', ''))
    except Exception:
        return url or ''


def _query_plan(queries, original_text=''):
    supplied = queries if isinstance(queries, list) else [queries]
    values = []
    original_words = re.sub(r'\s+', ' ', original_text or '').strip().split()
    if original_words:
        values.append(' '.join(original_words[:14]))
    values.extend(supplied[:4])
    unique = []
    seen = set()
    for value in values:
        value = re.sub(r'\s+', ' ', str(value or '')).strip()
        key = value.casefold()
        if len(value) >= 4 and key not in seen:
            seen.add(key)
            unique.append(value[:220])
    return unique[:4]


def _search_via_apify(queries, max_posts=10, original_text=''):
    """Search Facebook via Apify actor."""
    if not APIFY_TOKEN:
        return []
    search_queries = _query_plan(queries, original_text)[:3]
    all_posts = []

    def _run_query(query):
        try:
            resp = requests.post(
                'https://api.apify.com/v2/acts/scraper_one~facebook-posts-search/run-sync-get-dataset-items',
                params={'token': APIFY_TOKEN, 'format': 'json'},
                json={'query': query, 'resultsCount': min(max_posts, 10), 'searchType': 'latest'},
                timeout=120,
            )
            if resp.status_code not in (200, 201):
                return []
            items = resp.json() if isinstance(resp.json(), list) else []
            query_posts = []
            for item in items:
                author = item.get('author') or item.get('user') or {}
                if not isinstance(author, dict):
                    author = {'name': str(author)}
                query_posts.append({
                    'title': f"Facebook post by {author.get('name', 'Unknown')}",
                    'snippet': item.get('postText') or item.get('text') or item.get('message') or '',
                    'url': item.get('url') or item.get('postUrl') or item.get('facebookUrl') or '',
                    'source': author.get('name', 'Facebook'),
                    'platform': 'Facebook',
                    'date': item.get('timestamp') or item.get('time') or item.get('date') or '',
                    'likes': item.get('reactionsCount', 0),
                    'comments': item.get('commentsCount', 0),
                    'shares': item.get('sharesCount', 0),
                    'content_origin': 'platform',
                })
            return query_posts
        except Exception as e:
            print(f'[Apify] Failed: {e}')
            return []

    with ThreadPoolExecutor(max_workers=min(3, len(search_queries) or 1)) as executor:
        futures = [executor.submit(_run_query, query) for query in search_queries]
        for future in as_completed(futures):
            all_posts.extend(future.result() or [])

    unique = []
    seen = set()
    for post in all_posts:
        key = _canonical_url(post.get('url', ''))
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(post)
    return unique[:max_posts]


def _search_via_startpage(queries, original_text='', max_posts=10):
    """Fallback: search Startpage with original text, filter Facebook URLs."""
    from multi_search import StartpageProvider
    provider = StartpageProvider()
    search_queries = _query_plan(queries, original_text)
    all_posts = []

    # Search with original text (most relevant) + LLM queries
    search_list = []
    if original_text:
        search_list.append(' '.join(original_text.split()[:14]))
    for q in search_queries[:4]:
        if q not in search_list:
            search_list.append(q)

    for query in search_list:
        try:
            results = provider.search(f"site:facebook.com {query}", max_posts + 5)
            for r in results:
                href = r.get('href', '')
                if 'facebook.com' not in href.lower():
                    continue
                if '/posts/' not in href and '/reel/' not in href and '/videos/' not in href and '/photos/' not in href and '/groups/' not in href:
                    continue
                author = _extract_author(href, r.get('title', ''))
                snippet = r.get('body', '') or ''
                if not snippet:
                    snippet = r.get('title', '')
                all_posts.append({
                    'title': f"Facebook post by {author}",
                    'snippet': snippet,
                    'url': href,
                    'source': author,
                    'platform': 'Facebook',
                    'date': '',
                    'likes': 0,
                    'comments': 0,
                    'shares': 0,
                    'content_origin': 'search_snippet',
                })
            if len(all_posts) >= max_posts:
                break
        except Exception as e:
            print(f'[Startpage Facebook] Failed: {e}')

    seen = set()
    unique = []
    for p in all_posts:
        if p['url'] not in seen:
            seen.add(p['url'])
            unique.append(p)
    return unique[:max_posts]


def _extract_author(url, title):
    """Extract author from Facebook URL."""
    for pattern in [r'facebook\.com/([^/]+)/posts/', r'facebook\.com/([^/]+)/reel/',
                    r'facebook\.com/([^/]+)/videos/', r'facebook\.com/([^/]+)/photos/']:
        m = re.search(pattern, url)
        if m:
            return m.group(1).replace('.', ' ').replace('-', ' ').title()
    m = re.search(r'facebook\.com/groups/([^/]+)', url)
    if m:
        return m.group(1).replace('.', ' ').replace('-', ' ').title() + ' (Group)'
    if title:
        return title.split(':')[0].strip()[:50]
    return 'Facebook'


def search_facebook_with_fallback(claim_queries, original_text, max_posts=10):
    """Search direct and indexed Facebook sources with one diverse query plan."""
    return search_facebook_posts(
        claim_queries, max_posts=max_posts, original_text=original_text
    )
