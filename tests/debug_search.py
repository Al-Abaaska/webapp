"""Debug search to see what queries and results we get."""
import sys
sys.path.insert(0, '.')

from search.source_search import search_web_everywhere_with_fallback, search_web_everywhere
from services.gemini_service import generate_search_queries, extract_main_claim

# The original text from user's test
text = "Isbahaysiga Qaran ee Isbeddel doonka oo soo saaray War-Saxaafadeed uu uga falceliyay khudbaddii Madaxweyne Xasan Sheekh uu ka jeediyay Baarlamaanka iyagoo sidoo kale ka hadlay xaaladda Siyaasadeed ee dalka iyo Arrimaha doorashooyinka."

print("=" * 80)
print("ORIGINAL TEXT:")
print(text[:300])
print("=" * 80)

# Step 1: Extract claim
print("\n[STEP 1] Extracting main claim...")
main_claim = extract_main_claim(text)
print(f"Main claim: {main_claim}")

# Step 2: Generate queries
print("\n[STEP 2] Generating search queries...")
search_queries = generate_search_queries(text, main_claim)
print(f"Queries ({len(search_queries)}):")
for i, q in enumerate(search_queries):
    print(f"  {i+1}. {q}")

# Step 3: Test direct search with original text
print("\n[STEP 3] Direct search with FULL original text (300 chars)...")
primary_query = text[:300]
print(f"Query: {primary_query}")
results = search_web_everywhere(primary_query, num_results=5)
print(f"Results: {len(results)}")
for i, r in enumerate(results):
    print(f"  {i+1}. {r.get('title', 'N/A')[:80]}")
    print(f"     URL: {r.get('url', 'N/A')[:80]}")
    print(f"     Snippet: {r.get('snippet', 'N/A')[:100]}...")
    print()

# Step 4: Test search with first 15 words
print("\n[STEP 4] Search with first 15 words...")
words = text.split()[:15]
query_15 = ' '.join(words)
print(f"Query: {query_15}")
results_15 = search_web_everywhere(query_15, num_results=5)
print(f"Results: {len(results_15)}")
for i, r in enumerate(results_15):
    print(f"  {i+1}. {r.get('title', 'N/A')[:80]}")
    print(f"     URL: {r.get('url', 'N/A')[:80]}")
    print()

# Step 5: Test search with first 8 words
print("\n[STEP 5] Search with first 8 words...")
words_8 = text.split()[:8]
query_8 = ' '.join(words_8)
print(f"Query: {query_8}")
results_8 = search_web_everywhere(query_8, num_results=5)
print(f"Results: {len(results_8)}")
for i, r in enumerate(results_8):
    print(f"  {i+1}. {r.get('title', 'N/A')[:80]}")
    print(f"     URL: {r.get('url', 'N/A')[:80]}")
    print()

# Step 6: Test full fallback search
print("\n[STEP 6] Full fallback search...")
all_results = search_web_everywhere_with_fallback(search_queries, text, num_results=5)
print(f"Total results: {len(all_results)}")
for i, r in enumerate(all_results[:10]):
    print(f"  {i+1}. {r.get('title', 'N/A')[:80]}")
    print(f"     URL: {r.get('url', 'N/A')[:80]}")
    print()
