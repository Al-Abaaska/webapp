from gemini_service import extract_main_claim, generate_search_queries, semantic_match
from youtube_search import search_youtube_with_fallback

text = 'Deg Deg Maraykanka Oo Cirro Bahdil Ku Sameeyay, Xamse Oo Afka Furtay'
claim = extract_main_claim(text)
queries = generate_search_queries(text, claim)
print('Queries:', queries)

yt = search_youtube_with_fallback(queries, text, max_results=5)
print('YouTube results:', len(yt))
for r in yt:
    title = r.get('title', '')[:60]
    href = r.get('href', '')[:50]
    print('  YT:', title, '|', href)

# Now test semantic match with YouTube
if yt:
    match_items = []
    for r in yt:
        match_items.append({
            'title': r.get('title', ''),
            'snippet': r.get('body', ''),
            'url': r.get('href', ''),
            'source': r.get('author', 'YouTube'),
            'platform': 'YouTube',
        })
    scored = semantic_match(claim, match_items)
    print('Scored:', len(scored))
    for s in scored:
        print('  Score:', s.get('similarity'), s.get('title', '')[:50])
