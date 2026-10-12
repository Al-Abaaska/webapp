"""Evidence retrieval, normalization, stance labeling, and audit persistence helpers."""
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

from .gemini_service import semantic_match
from search.source_search import (
    search_somali_sources,
    search_web_everywhere_with_fallback,
)
from search.facebook_search import search_facebook_with_fallback
from search.youtube_search import fetch_youtube_metadata, search_youtube_with_fallback
from .somali_sources import identify_trusted_publisher


MIN_EVIDENCE_SIMILARITY = 30


CONTRADICTION_TERMS = (
    'been abuur', 'war been', 'been ah', 'ma jirto', 'ma aha run',
    'beenisay', 'beeniyay', 'beeniyey', 'xaqiiqo ma aha', 'marin habaabin',
    'false', 'fake', 'debunk', 'refute', 'denied', 'not true',
)
SUPPORT_TERMS = (
    'xaqiijiyay', 'xaqiijisay', 'xaqiijiyey', 'la xaqiijiyay',
    'confirmed', 'confirms', 'officially announced', 'verified',
)


def _canonical_url(url):
    if not url:
        return ''
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip('/'), '', ''))
    except Exception:
        return url


def _normalize_candidate(item, evidence_kind):
    first = item.get('first_result') or {}
    url = item.get('url') or item.get('href') or first.get('url') or ''
    platform = item.get('platform') or item.get('type') or 'Web'
    if item.get('is_facebook') or 'facebook.com' in url.lower():
        platform = 'Facebook'
    elif 'youtube.com' in url.lower() or 'youtu.be' in url.lower():
        platform = 'YouTube'
    return {
        'title': item.get('title') or first.get('title') or '',
        'snippet': item.get('snippet') or item.get('body') or first.get('snippet') or '',
        'url': url,
        'source': item.get('source') or item.get('name') or item.get('author') or 'Web',
        'platform': platform,
        'provider': item.get('provider') or '',
        'date': item.get('date') or '',
        'credibility': item.get('credibility') or '',
        'evidence_kind': evidence_kind,
        'is_facebook': platform == 'Facebook',
        'author': item.get('author') or item.get('source') or '',
        'author_url': item.get('author_url') or '',
        'channel_id': item.get('channel_id') or '',
        'likes': item.get('likes', 0),
        'comments': item.get('comments', 0),
        'shares': item.get('shares', 0),
        'content_origin': (
            item.get('content_origin')
            or first.get('content_origin')
            or ('platform' if evidence_kind == 'social' else 'search_snippet')
        ),
    }


def _verify_general_youtube_candidate(item):
    """Replace provider-generated YouTube snippets with canonical video metadata."""
    if not (
        item.get('platform') == 'YouTube'
        and item.get('content_origin') == 'search_snippet'
    ):
        return item
    metadata = fetch_youtube_metadata(item.get('url', ''))
    if not metadata.get('title'):
        return item
    item['search_provider_snippet'] = item.get('snippet', '')
    item['title'] = metadata['title']
    item['snippet'] = ''
    item['source'] = metadata.get('author') or item.get('source', 'YouTube')
    item['author'] = metadata.get('author') or item.get('author', '')
    item['author_url'] = metadata.get('author_url') or item.get('author_url', '')
    item['content_origin'] = 'platform_metadata'
    item['metadata_verified'] = True
    return item


def _apply_trusted_publisher(item):
    """Promote an official publisher page on any supported platform."""
    profile = identify_trusted_publisher(item)
    if not profile:
        return item
    item['source'] = profile['name']
    item['publisher'] = profile['name']
    item['credibility'] = profile['credibility']
    item['source_role'] = profile['type']
    item['identity_method'] = profile['identity_method']
    item['publisher_verified'] = True
    item['evidence_kind'] = 'trusted'
    return item


def _classify_stance(item, predicted_label=''):
    combined = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
    # The UI intentionally uses only Supports, Repeats, and Unclear.  A result
    # containing a denial/debunking cue is therefore left Unclear for human
    # review instead of assigning the removed Refutes category.
    if any(term in combined for term in CONTRADICTION_TERMS):
        return 'Unclear'
    label = (predicted_label or '').strip().casefold()
    if label in ('misinformation', 'misleading'):
        if (
            any(term in combined for term in SUPPORT_TERMS)
            or item.get('platform') in ('Facebook', 'YouTube')
            or item.get('similarity', 0) >= 35
        ):
            return 'Repeats'
        return 'Unclear'
    if any(term in combined for term in SUPPORT_TERMS):
        return 'Supports'
    if item.get('evidence_kind') == 'trusted' and item.get('similarity', 0) >= 60:
        return 'Supports'
    if item.get('platform') in ('Facebook', 'YouTube'):
        return 'Repeats'
    if item.get('similarity', 0) >= 35:
        return 'Repeats'
    return 'Unclear'


def retrieve_evidence(
    original_text, main_claim, search_queries, max_results=24,
    predicted_label='',
):
    """Search trusted, general, Facebook, and YouTube sources for every label."""
    query = main_claim or original_text[:200]
    queries = search_queries or [query]
    with ThreadPoolExecutor(max_workers=4) as pool:
        trusted_future = pool.submit(search_somali_sources, query[:200], 1)
        web_future = pool.submit(
            search_web_everywhere_with_fallback, queries[:3], original_text, 8
        )
        facebook_future = pool.submit(
            search_facebook_with_fallback, queries[:3], original_text, 5
        )
        youtube_future = pool.submit(
            search_youtube_with_fallback, queries[:3], original_text, 5
        )
        result_groups = (
            ('trusted', trusted_future.result() or []),
            ('social', facebook_future.result() or []),
            ('social', youtube_future.result() or []),
            ('general', web_future.result() or []),
        )

    # Keep every retrieval channel represented. Concatenating the groups and
    # then truncating could exclude recent social posts before scoring.
    buckets = []
    seen = set()
    for evidence_kind, results in result_groups:
        bucket = []
        for raw_item in results:
            item = _normalize_candidate(raw_item, evidence_kind)
            key = _canonical_url(item['url']) or (
                re.sub(r'\s+', ' ', item['title'].lower()).strip(),
                re.sub(r'\s+', ' ', item['snippet'].lower()).strip()[:120],
            )
            if not item['url'] or key in seen:
                continue
            seen.add(key)
            bucket.append(item)
        buckets.append(bucket)

    candidates = []
    while len(candidates) < max_results and any(buckets):
        for bucket in buckets:
            if bucket and len(candidates) < max_results:
                candidates.append(bucket.pop(0))

    youtube_indexes = [
        index for index, item in enumerate(candidates)
        if item.get('platform') == 'YouTube'
        and item.get('content_origin') == 'search_snippet'
    ]
    if youtube_indexes:
        with ThreadPoolExecutor(max_workers=min(3, len(youtube_indexes))) as pool:
            futures = {
                pool.submit(_verify_general_youtube_candidate, candidates[index]): index
                for index in youtube_indexes
            }
            for future, index in futures.items():
                try:
                    candidates[index] = future.result()
                except Exception as exc:
                    print(f'[YouTube Metadata] Candidate verification failed: {exc}')

    candidates = [_apply_trusted_publisher(item) for item in candidates]

    scored = semantic_match(original_text, candidates) if candidates else []
    scored = [
        item for item in scored
        if float(item.get('similarity', 0) or 0) >= MIN_EVIDENCE_SIMILARITY
    ]
    for item in scored:
        item['similarity_score'] = item.get('similarity', 0)
        item['explanation'] = item.get('reason', '')
        item['scoring_method'] = item.get('scoring_method', 'semantic_llm')
        item['stance'] = _classify_stance(item, predicted_label)

    scored.sort(key=lambda item: item.get('similarity_score', 0), reverse=True)
    provider_counts = {}
    for item in scored:
        provider = item.get('provider') or item.get('platform') or 'Web'
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
    return scored, provider_counts
