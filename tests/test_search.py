from search.source_search import search_somali_sources_with_fallback, search_web_everywhere_with_fallback
from search.facebook_search import search_facebook_with_fallback
from search.youtube_search import search_youtube_with_fallback

text = 'Deg Deg Maraykanka Oo Cirro Bahdil Ku Sameeyay, Xamse Oo Afka Furtay'

print('=== SOMALI SOURCES ===')
src = search_somali_sources_with_fallback(text[:200], text)
for i, r in enumerate(src[:5]):
    name = r.get('name', 'Unknown')
    fr = r.get('first_result', {})
    title = fr.get('title', r.get('title', ''))[:100]
    url = r.get('url', fr.get('url', ''))
    print(f'{i+1}. [{name}] {title}')
    print(f'   {url}')

print('\n=== WEB SEARCH ===')
web = search_web_everywhere_with_fallback([text[:200]], text, num_results=5)
for i, r in enumerate(web[:5]):
    print(f'{i+1}. {r.get("title", "")[:100]}')
    print(f'   {r.get("url", "")}')

print('\n=== FACEBOOK ===')
fb = search_facebook_with_fallback([text[:200]], text, max_posts=3)
for i, r in enumerate(fb[:3]):
    print(f'{i+1}. {r.get("title", "")[:100]}')
    print(f'   {r.get("url", "")}')

print('\n=== YOUTUBE ===')
yt = search_youtube_with_fallback([text[:200]], text, max_results=3)
for i, r in enumerate(yt[:3]):
    print(f'{i+1}. {r.get("title", "")[:100]}')
    print(f'   {r.get("href", "")}')
