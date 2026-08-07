"""
Source-specific search — Search credible Somali sources using site: filter.
Used when ML predicts Reliable to confirm claim is reported by trusted sources.
"""
import os
import re
from datetime import datetime

from ddg_search import DDGS
from multi_search import clean_text, multi_search
from concurrent.futures import ThreadPoolExecutor, as_completed
from somali_sources import SOMALI_SOURCES, SOMALI_SOURCE_MAP


def search_somali_sources(query, max_per_source=2):
    """Search each Somali source using site: filter. Returns confirmed sources with article URLs."""
    confirmed = []

    def _search_source(source):
        site_query = f"site:{source.get('search_site', source['domain'])} {query}"
        try:
            # Use multi_search with retry logic instead of DDGS directly
            results = multi_search(site_query, max_results=max_per_source)
            if results:
                first = results[0]
                article_url = first.get('href', source['url'])
                return {
                    'name': source['name'],
                    'domain': source['domain'],
                    'url': article_url,
                    'source_homepage': source['url'],
                    'type': source['type'],
                    'credibility': source['credibility'],
                    'provider': first.get('provider', ''),
                    'found': True,
                    'result_count': len(results),
                    'first_result': {
                        'title': clean_text(first.get('title', '')),
                        'snippet': clean_text(first.get('body', '')),
                        'url': article_url,
                    },
                }
        except Exception as e:
            print(f'[SomaliSource] {source["name"]} failed: {e}')
        return None

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_search_source, s): s for s in SOMALI_SOURCES}
        for future in as_completed(futures):
            result = future.result()
            if result:
                confirmed.append(result)

    confirmed.sort(key=lambda x: x['result_count'], reverse=True)
    return confirmed


def search_somali_sources_with_fallback(claim_query, original_text, max_per_source=2):
    """Search both Somali sources AND web. Original text first for best matches."""
    # Search Somali sources with original text FIRST (exact match most likely)
    results = search_somali_sources(original_text[:200], max_per_source)
    
    # Also search with claim query
    results2 = search_somali_sources(claim_query, max_per_source)
    seen = {r.get('domain', '') for r in results}
    for r in results2:
        if r.get('domain', '') not in seen:
            results.append(r)
            seen.add(r.get('domain', ''))

    # Search general web with original text FIRST (highest priority)
    words = original_text.split()[:15]
    primary_query = ' '.join(words)
    web_results = search_web_everywhere(primary_query, num_results=5)
    web_results2 = search_web_everywhere(claim_query[:200], num_results=5)
    
    # Also search YouTube
    try:
        from youtube_search import search_youtube_with_fallback
        yt_results = search_youtube_with_fallback([claim_query], original_text, max_results=3)
        for r in yt_results:
            web_results.append({
                'title': r.get('title', ''),
                'snippet': r.get('body', ''),
                'url': r.get('href', ''),
                'source': 'YouTube',
                'platform': 'YouTube',
                'date': r.get('date', ''),
            })
    except Exception as e:
        print(f'[YouTube] Search failed: {e}')
    
    # Deduplicate by URL
    seen_urls = set()
    for r in web_results + web_results2:
        url = r.get('url', '')
        if url in seen_urls:
            continue
        seen_urls.add(url)
        results.append({
            'name': r.get('source', 'Unknown'),
            'domain': '',
            'url': r.get('url', ''),
            'source_homepage': '',
            'type': r.get('platform', 'Website'),
            'credibility': 'Web Source',
            'found': True,
            'result_count': 1,
            'title': clean_text(r.get('title', '')),
            'snippet': clean_text(r.get('snippet', '')),
            'first_result': {
                'title': clean_text(r.get('title', '')),
                'snippet': clean_text(r.get('snippet', '')),
                'url': r.get('url', ''),
            },
        })

    return results


def search_web_everywhere(query, num_results=5):
    """General web search using multi-provider chain."""
    try:
        results = multi_search(query, max_results=num_results)
        formatted = []
        for r in results:
            link = r.get('href', '')
            from google_search import _detect_source, _detect_platform, _extract_date_from_url
            formatted.append({
                'title': r.get('title', ''),
                'snippet': r.get('body', ''),
                'url': link,
                'source': _detect_source(link),
                'platform': _detect_platform(link),
                'provider': r.get('provider', ''),
                'date': _extract_date_from_url(link),
                'content_origin': 'search_snippet',
            })

        urls_needing = (
            [item for item in formatted if not item['date']]
            if os.getenv('FETCH_SOURCE_DATES', '').lower() in ('1', 'true', 'yes')
            else []
        )
        if urls_needing:
            import google_search
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(google_search._extract_date_from_page, item['url']): item
                          for item in urls_needing}
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
        print(f'[WebSearch] Search failed: {e}')
        return []


def _extract_keywords(text):
    """Extract key search keywords from Somali text — places, events, entities."""
    import re
    text_lower = text.lower()
    
    # Key Somali political/event keywords
    event_words = [
        'iska horimaad', 'dagaal', 'dhacdo', 'shir', 'heshiis', 'go\'aan',
        'warbixin', 'qarax', 'isku qarxinta', 'hub', 'maleeshiyaad',
        'ciidamada', 'amniga', 'doorasho', 'banaanbax', 'socdaal',
    ]
    
    # Extract event type
    event_type = ''
    for ew in event_words:
        if ew in text_lower:
            event_type = ew
            break
    
    # Extract capitalized words (names, places) — simple heuristic
    words = text.split()
    entities = []
    for w in words:
        w_clean = w.strip('.,;:!?()[]{}"\'')
        if len(w_clean) > 3 and w_clean[0].isupper():
            entities.append(w_clean)
    
    keywords = []
    if event_type:
        keywords.append(event_type)
    if entities:
        keywords.append(' '.join(entities[:3]))
    
    return keywords


def _build_search_plan(claim_queries, original_text, max_queries=7):
    """Build a small, diverse plan for exact, broad, and recent retrieval."""
    original_words = re.sub(r'\s+', ' ', original_text or '').strip().split()
    supplied = claim_queries if isinstance(claim_queries, list) else [claim_queries]
    candidates = []

    if original_words:
        exact_phrase = ' '.join(original_words[:12]).strip('.,;:!?()[]{}"\'')
        if exact_phrase:
            candidates.append(f'"{exact_phrase}"')
        candidates.append(' '.join(original_words[:18]))

    candidates.extend(_extract_keywords(original_text or ''))
    first_short_query = next((q for q in supplied if q and len(q.split()) <= 8), '')
    if first_short_query:
        candidates.append(first_short_query)
        candidates.append(f'{first_short_query} {datetime.utcnow().year}')
        candidates.append(f'{first_short_query} maanta')
    candidates.extend(supplied)

    plan = []
    seen = set()
    for query in candidates:
        query = re.sub(r'\s+', ' ', str(query or '')).strip()
        key = query.casefold()
        if len(query) < 4 or key in seen:
            continue
        seen.add(key)
        plan.append(query[:250])
        if len(plan) >= max_queries:
            break
    return plan


def search_web_everywhere_with_fallback(claim_queries, original_text, num_results=5):
    """Search exact, broad, and recent query variants and merge their results."""
    all_results = []
    seen_urls = set()
    
    query_plan = _build_search_plan(claim_queries, original_text)
    results_by_index = {}
    with ThreadPoolExecutor(max_workers=min(4, len(query_plan) or 1)) as executor:
        futures = {
            executor.submit(search_web_everywhere, query, num_results): index
            for index, query in enumerate(query_plan)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results_by_index[index] = future.result() or []
            except Exception as exc:
                print(f'[WebSearch] Query failed: {exc}')
                results_by_index[index] = []

    for index in range(len(query_plan)):
        for result in results_by_index.get(index, []):
            url = (result.get('url') or '').rstrip('/')
            key = url.casefold()
            if not url or key in seen_urls:
                continue
            seen_urls.add(key)
            all_results.append(result)
    
    return all_results
