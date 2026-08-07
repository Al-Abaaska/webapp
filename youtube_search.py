"""
YouTube Search — scrapes YouTube search page directly.
Falls back to YouTube Data API if scraping fails.
"""
import os
import re
import json
import requests
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor


YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY', '')


@lru_cache(maxsize=128)
def fetch_youtube_metadata(url):
    """Return canonical public video metadata without trusting search snippets."""
    if not url or ('youtube.com' not in url and 'youtu.be' not in url):
        return {}
    try:
        response = requests.get(
            'https://www.youtube.com/oembed',
            params={'url': url, 'format': 'json'},
            timeout=10,
        )
        if response.status_code != 200:
            return {}
        data = response.json()
        return {
            'title': data.get('title', ''),
            'author': data.get('author_name', ''),
            'author_url': data.get('author_url', ''),
        }
    except Exception as exc:
        print(f'[YouTube Metadata] Failed: {exc}')
        return {}


def _runs_text(value):
    return ' '.join(
        run.get('text', '') for run in (value or {}).get('runs', [])
    ).strip()


def search_youtube_scrape(query, max_results=5):
    """Search YouTube by scraping search page HTML. Returns video results."""
    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9,so;q=0.8',
        })
        resp = session.get(
            'https://www.youtube.com/results',
            params={'search_query': query, 'sp': 'EgIQAQ%3D%3D'},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f'[YouTube Scrape] Status {resp.status_code}')
            return []

        match = re.search(r'var ytInitialData = ({.*?});', resp.text)
        if not match:
            print('[YouTube Scrape] No ytInitialData found')
            return []

        data = json.loads(match.group(1))
        contents = (data.get('contents', {})
                    .get('twoColumnSearchResultsRenderer', {})
                    .get('primaryContents', {})
                    .get('sectionListRenderer', {})
                    .get('contents', []))

        results = []
        for section in contents:
            items = section.get('itemSectionRenderer', {}).get('contents', [])
            for item in items:
                vid = item.get('videoRenderer', {})
                if not vid:
                    continue
                title = vid.get('title', {}).get('runs', [{}])[0].get('text', '')
                video_id = vid.get('videoId', '')
                channel = vid.get('ownerText', {}).get('runs', [{}])[0].get('text', '')
                owner_run = (vid.get('ownerText', {}).get('runs') or [{}])[0]
                owner_browse = owner_run.get('navigationEndpoint', {}).get('browseEndpoint', {})
                views = vid.get('viewCountText', {}).get('simpleText', '')
                description = _runs_text(vid.get('descriptionSnippet'))
                if not description:
                    detailed = vid.get('detailedMetadataSnippets') or []
                    if detailed:
                        description = _runs_text(detailed[0].get('snippetText'))
                url = f'https://www.youtube.com/watch?v={video_id}'
                results.append({
                    'title': title,
                    'body': description or f'{channel} - {views}',
                    'href': url,
                    'platform': 'YouTube',
                    'author': channel,
                    'channel_id': owner_browse.get('browseId', ''),
                    'author_url': owner_browse.get('canonicalBaseUrl', ''),
                    'date': '',
                    'thumbnail': vid.get('thumbnail', {}).get('thumbnails', [{}])[-1].get('url', ''),
                    'video_id': video_id,
                    'content_origin': 'platform',
                })
                if len(results) >= max_results:
                    return results
        return results
    except Exception as e:
        print(f'[YouTube Scrape] Failed: {e}')
        return []


def search_youtube_api(query, max_results=5):
    """Search YouTube via official API."""
    if not YOUTUBE_API_KEY:
        return []
    try:
        resp = requests.get(
            'https://www.googleapis.com/youtube/v3/search',
            params={
                'part': 'snippet',
                'q': query,
                'type': 'video',
                'order': 'date',
                'maxResults': max_results,
                'key': YOUTUBE_API_KEY,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            print(f'[YouTube API] Status {resp.status_code}')
            return []
        data = resp.json()
        results = []
        for item in data.get('items', []):
            snippet = item.get('snippet', {})
            video_id = item.get('id', {}).get('videoId', '')
            url = f'https://www.youtube.com/watch?v={video_id}' if video_id else ''
            results.append({
                'title': snippet.get('title', ''),
                'body': snippet.get('description', ''),
                'href': url,
                'platform': 'YouTube',
                'author': snippet.get('channelTitle', ''),
                'channel_id': snippet.get('channelId', ''),
                'date': snippet.get('publishedAt', '')[:10],
                'thumbnail': snippet.get('thumbnails', {}).get('default', {}).get('url', ''),
                'video_id': video_id,
                'content_origin': 'platform',
            })
        return results
    except Exception as e:
        print(f'[YouTube API] Failed: {e}')
        return []


def search_youtube(query, max_results=5):
    """Combine relevance results with recent videos from the official API."""
    with ThreadPoolExecutor(max_workers=2) as executor:
        scrape_future = executor.submit(search_youtube_scrape, query, max_results)
        api_future = executor.submit(search_youtube_api, query, max_results)
        groups = [scrape_future.result() or [], api_future.result() or []]

    results = []
    seen = set()
    for group in groups:
        for item in group:
            key = item.get('video_id') or item.get('href', '')
            if not key or key in seen:
                continue
            seen.add(key)
            results.append(item)
    return results[:max_results]


def search_youtube_with_fallback(claim_queries, original_text, max_results=5):
    """Search with claim queries AND original text."""
    all_results = []
    seen_urls = set()
    queries = (claim_queries if isinstance(claim_queries, list) else [claim_queries])[:3]
    if original_text:
        exact = ' '.join(original_text.split()[:12])
        queries.insert(0, f'"{exact}"')
    for q in queries:
        for r in search_youtube(q, max_results):
            url = r.get('href', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(r)
    return all_results[:max_results]
