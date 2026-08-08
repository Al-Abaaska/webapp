"""
Multi-Provider Web Search — tries multiple search engines with automatic fallback.
Providers: ddgs (DuckDuckGo API) → Exa → Startpage → SearXNG (with retry logic)
"""
import os
import re
import base64
import html as html_lib
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

EXA_API_KEY = os.getenv('EXA_API_KEY', '')
GOOGLE_CSE_API_KEY = os.getenv('GOOGLE_CSE_API_KEY', '')
GOOGLE_CSE_ID = os.getenv('GOOGLE_CSE_ID', '')


def clean_text(text):
    """Remove CSS, HTML, scripts, and code artifacts from search result text."""
    if not text:
        return ''
    text = re.sub(r'\.[a-zA-Z0-9_-]+\{[^}]*\}', '', text)
    text = re.sub(r'@media[^{]*\{[^}]*\}', '', text)
    text = re.sub(r'\{[^}]*\}', '', text)
    text = re.sub(r'[a-z-]+:[^;{},]+;', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'function\s*\([^)]*\)\s*\{[^}]*\}', '', text)
    text = re.sub(r'var\(--[^)]+\)', '', text)
    text = re.sub(r'\\u[0-9a-fA-F]{4}', '', text)
    text = re.sub(r'https?://[^\s]+\.(css|js|png|jpg|gif|svg)', '', text)
    lines = text.split('\n')
    clean_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.match(r'^[\.\#\@\{\/\*]', line):
            continue
        if 'function(' in line or 'var(' in line or 'margin:' in line:
            continue
        if len(line) < 5:
            continue
        clean_lines.append(line)
    text = ' '.join(clean_lines)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.strip('.,;:!?()[]{}"\' ')
    return text


class SearchProvider:
    name = 'base'
    def search(self, query, max_results=5):
        raise NotImplementedError


class DDGSProvider(SearchProvider):
    """Primary provider — DuckDuckGo via ddgs API package (uses primp for anti-bot)."""
    name = 'DuckDuckGo'
    
    def search(self, query, max_results=5):
        try:
            from ddgs import DDGS
            results = DDGS().text(query, max_results=max_results)
            formatted = []
            for r in results:
                formatted.append({
                    'title': clean_text(r.get('title', '')),
                    'body': clean_text(r.get('body', '')),
                    'href': r.get('href', ''),
                })
            if formatted:
                print(f'[DDG API] Returned {len(formatted)} results')
            return formatted
        except Exception as e:
            print(f'[DDG API] Failed: {e}')
            return []


class ExaProvider(SearchProvider):
    """Second provider — Exa API (fast, AI-native search)."""
    name = 'Exa'
    TIMEOUT = 15
    
    def search(self, query, max_results=5):
        if not EXA_API_KEY:
            return []
        try:
            resp = requests.post(
                'https://api.exa.ai/search',
                headers={
                    'x-api-key': EXA_API_KEY,
                    'Content-Type': 'application/json',
                },
                json={
                    'query': query,
                    'numResults': max_results,
                    'type': 'auto',
                    'contents': {
                        'text': True,
                    },
                },
                timeout=self.TIMEOUT,
            )
            if resp.status_code != 200:
                print(f'[Exa] HTTP {resp.status_code}: {resp.text[:200]}')
                return []
            
            data = resp.json()
            results = []
            for r in data.get('results', []):
                results.append({
                    'title': clean_text(r.get('title', '')),
                    'body': clean_text(r.get('text', '')),
                    'href': r.get('url', ''),
                })
            
            if results:
                print(f'[Exa] Returned {len(results)} results')
            return results
            
        except Exception as e:
            print(f'[Exa] Failed: {e}')
            return []


class GoogleCSEProvider(SearchProvider):
    """Official Google Programmable Search API; enabled only when credentials exist."""
    name = 'Google'
    TIMEOUT = 15

    def search(self, query, max_results=5):
        if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
            return []
        try:
            response = requests.get(
                'https://www.googleapis.com/customsearch/v1',
                params={
                    'key': GOOGLE_CSE_API_KEY,
                    'cx': GOOGLE_CSE_ID,
                    'q': query,
                    'num': min(max_results, 10),
                },
                timeout=self.TIMEOUT,
            )
            if response.status_code != 200:
                print(f'[Google] HTTP {response.status_code}')
                return []
            results = []
            for item in response.json().get('items', []):
                results.append({
                    'title': clean_text(item.get('title', '')),
                    'body': clean_text(item.get('snippet', '')),
                    'href': item.get('link', ''),
                })
            if results:
                print(f'[Google] Returned {len(results)} results')
            return results
        except Exception as exc:
            print(f'[Google] Failed: {exc}')
            return []


class StartpageProvider(SearchProvider):
    """Fallback provider — Startpage HTML scraping."""
    name = 'Startpage'
    TIMEOUT = 25
    MAX_RETRIES = 2
    
    def search(self, query, max_results=5):
        for attempt in range(self.MAX_RETRIES):
            try:
                session = requests.Session()
                session.headers.update({
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml',
                    'Accept-Language': 'en-US,en;q=0.9,so;q=0.8',
                })
                resp = session.get(
                    'https://www.startpage.com/do/dsearch',
                    params={'query': query, 'cat': 'web', 'language': 'english'},
                    timeout=self.TIMEOUT,
                )
                if resp.status_code != 200:
                    if attempt < self.MAX_RETRIES - 1:
                        time.sleep(1)
                        continue
                    return []
                
                html = resp.text
                if 'Startpage Blocked' in html:
                    print('[Startpage] IP blocked')
                    return []
                
                results = []
                link_matches = re.findall(
                    r'<a[^>]+class="[^"]*result-link[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                    html, re.S
                )
                for href, inner in link_matches[:max_results]:
                    title = clean_text(re.sub(r'<[^>]+>', '', inner).strip())
                    if not title:
                        title = clean_text(href.split('/')[-1].replace('-', ' ')[:80])
                    results.append({'title': title, 'body': '', 'href': href})
                
                snippets = re.findall(r'<p class="w-gl__description[^"]*">(.*?)</p>', html, re.S)
                for i, snippet in enumerate(snippets):
                    if i < len(results):
                        results[i]['body'] = clean_text(re.sub(r'<[^>]+>', '', snippet).strip())
                
                if results and not results[0].get('body'):
                    snippets = re.findall(r'<p class="description[^"]*"[^>]*>(.*?)</p>', html, re.S)
                    for i, snippet in enumerate(snippets):
                        if i < len(results):
                            results[i]['body'] = clean_text(re.sub(r'<[^>]+>', '', snippet).strip())
                
                if results:
                    print(f'[Startpage] Returned {len(results)} results')
                    return results
                
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(1)
                    
            except requests.exceptions.Timeout:
                print(f'[Startpage] Timeout (attempt {attempt + 1}/{self.MAX_RETRIES})')
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(1)
            except Exception as e:
                print(f'[Startpage] Failed: {e}')
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(1)
        
        return []


class SearXNGProvider(SearchProvider):
    """Last resort — SearXNG meta-search instances."""
    name = 'SearXNG'
    INSTANCES = ['https://searx.be', 'https://search.sapti.me', 'https://search.ononoki.org']
    TIMEOUT = 12
    MAX_RETRIES = 1
    
    def search(self, query, max_results=5):
        for instance in self.INSTANCES:
            for attempt in range(self.MAX_RETRIES):
                try:
                    resp = requests.get(
                        f'{instance}/search',
                        params={'q': query, 'format': 'json', 'categories': 'general'},
                        timeout=self.TIMEOUT,
                    )
                    if resp.status_code != 200:
                        if attempt < self.MAX_RETRIES - 1:
                            time.sleep(0.5)
                            continue
                        break
                    
                    data = resp.json()
                    results = []
                    for r in data.get('results', [])[:max_results]:
                        results.append({
                            'title': clean_text(r.get('title', '')),
                            'body': clean_text(r.get('content', '')),
                            'href': r.get('url', ''),
                        })
                    
                    if results:
                        print(f'[SearXNG] {instance} returned {len(results)} results')
                        return results
                    
                except requests.exceptions.Timeout:
                    print(f'[SearXNG] {instance} timeout')
                except Exception:
                    break
        
        return []


class BingProvider(SearchProvider):
    """Bing search via HTML scraping — finds Somali content others miss."""
    name = 'Bing'
    TIMEOUT = 15

    @staticmethod
    def _decode_result_url(url):
        """Resolve Bing's base64 redirect URL without following the click tracker."""
        url = html_lib.unescape(url)
        try:
            encoded = parse_qs(urlsplit(url).query).get('u', [''])[0]
            if encoded.startswith('a1'):
                encoded = encoded[2:]
                encoded += '=' * (-len(encoded) % 4)
                decoded = base64.urlsafe_b64decode(encoded).decode('utf-8')
                if decoded.startswith(('http://', 'https://')):
                    return decoded
        except Exception:
            pass
        return url
    
    def search(self, query, max_results=5):
        try:
            session = requests.Session()
            session.headers.update({
                # Bing currently returns its parseable server-rendered results to this UA.
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'text/html,application/xhtml+xml',
                'Accept-Language': 'en-US,en;q=0.9,so;q=0.8',
            })
            resp = session.get(
                'https://www.bing.com/search',
                params={'q': query, 'count': max_results + 2},
                timeout=self.TIMEOUT,
            )
            if resp.status_code != 200:
                print(f'[Bing] HTTP {resp.status_code}')
                return []
            
            html = resp.text
            results = []
            
            # Parse Bing results from HTML
            # Find all result blocks
            result_pattern = re.compile(
                r'<li[^>]*class=["\'][^"\']*\bb_algo\b[^"\']*["\'][^>]*>'
                r'.*?<h2[^>]*>\s*<a[^>]*href=["\'](https?://[^"\']+)["\'][^>]*>'
                r'(.*?)</a>(.*?)</li>',
                re.IGNORECASE | re.DOTALL,
            )

            for match in list(result_pattern.finditer(html))[:max_results]:
                url = self._decode_result_url(match.group(1))
                title = html_lib.unescape(
                    re.sub(r'<[^>]+>', '', match.group(2)).strip()
                )

                snippet_match = re.search(r'<p[^>]*>(.*?)</p>', match.group(3), re.DOTALL)
                snippet = ''
                if snippet_match:
                    snippet = html_lib.unescape(
                        re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()
                    )
                
                if url and title:
                    results.append({
                        'title': clean_text(title),
                        'body': clean_text(snippet),
                        'href': url,
                    })
            
            if results:
                print(f'[Bing] Returned {len(results)} results')
            return results
            
        except Exception as e:
            print(f'[Bing] Failed: {e}')
            return []


PROVIDERS = [
    ExaProvider(),
    BingProvider(),
    GoogleCSEProvider(),
    DDGSProvider(),
]

YOUTUBE_PROVIDERS = [
    ExaProvider(),
    BingProvider(),
    GoogleCSEProvider(),
    DDGSProvider(),
]


def _canonical_url(url):
    """Normalize a result URL so providers do not create duplicate cards."""
    if not url:
        return ''
    try:
        parts = urlsplit(url)
        tracking = {'fbclid', 'gclid', 'ref', 'source'}
        query = urlencode([
            (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith('utm_') and key.lower() not in tracking
        ])
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip('/'), query, ''))
    except Exception:
        return url


def multi_search(query, max_results=5, providers=None):
    """Aggregate several providers instead of stopping after the first success."""
    if providers is None:
        providers = PROVIDERS
    if not query or max_results < 1:
        return []

    per_provider = min(max_results, 3)
    results_by_provider = {}
    with ThreadPoolExecutor(max_workers=min(len(providers), 5)) as executor:
        futures = {
            executor.submit(provider.search, query, per_provider): provider
            for provider in providers
        }
        for future in as_completed(futures):
            provider = futures[future]
            try:
                items = future.result() or []
            except Exception as exc:
                print(f'[Search] {provider.name} failed: {exc}')
                items = []
            for item in items:
                item['provider'] = provider.name
            results_by_provider[provider.name] = items
            print(f'[Search] {provider.name} returned {len(items)} results')

    # Round-robin preserves provider diversity, so a weak Exa result cannot hide Bing.
    merged = []
    seen = set()
    for result_index in range(per_provider):
        for provider in providers:
            items = results_by_provider.get(provider.name, [])
            if result_index >= len(items):
                continue
            item = items[result_index]
            key = _canonical_url(item.get('href', '')) or (
                clean_text(item.get('title', '')).lower(),
                clean_text(item.get('body', '')).lower()[:120],
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= max_results:
                return merged

    if not merged:
        print('[Search] All providers returned no results')
    return merged


def multi_search_all(queries, max_results=5):
    """Search multiple queries and merge results."""
    all_results = []
    seen_urls = set()
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(multi_search, q, max_results): q for q in queries[:3]}
        for future in as_completed(futures):
            results = future.result()
            for r in results:
                url = r.get('href', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
    return all_results


def multi_search_youtube(query, max_results=5):
    """Search YouTube-specific results via site:youtube.com."""
    yt_query = f"site:youtube.com {query}"
    return multi_search(yt_query, max_results, providers=YOUTUBE_PROVIDERS)
