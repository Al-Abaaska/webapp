from services.gemini_service import extract_main_claim, generate_search_queries, semantic_match
from search.source_search import search_somali_sources_with_fallback

text = 'Isbahaysiga Qaran ee Isbeddel doonka oo soo saaray War-Saxaafadeed uu uga falceliyay khudbaddii Madaxweyne Xasan Sheekh uu ka jeediyay Baarlamaanka iyagoo sidoo kale ka hadlay xaaladda Siyaasadeed ee dalka iyo Arrimaha doorashooyinka'

claim = extract_main_claim(text)
print('CLAIM:', claim[:100])

src = search_somali_sources_with_fallback(text[:200], text)
print('SOURCES:', len(src))

if src:
    items = []
    for s in src[:10]:
        fr = s.get('first_result', {})
        items.append({
            'title': fr.get('title', s.get('title', '')),
            'snippet': fr.get('snippet', s.get('snippet', '')),
            'url': fr.get('url', s.get('url', '')),
            'source': s.get('name', 'Web'),
            'platform': '',
        })
    scored = semantic_match(claim, items)
    print('SCORED:', len(scored))
    for s in scored[:5]:
        sim = s.get('similarity', 0)
        title = s.get('title', '')[:60]
        print(f'  {sim}% | {title}')
else:
    print('NO SOURCES FOUND')
