"""Test DDGS vs Exa for exact article finding."""
import sys
sys.path.insert(0, '.')

text = 'Isbahaysiga Qaran ee Isbeddel doonka oo soo saaray War-Saxaafadeed'

print("=== DDGS Test ===")
from ddg_search import DDGS
ddgs = DDGS()
results = list(ddgs.text(text, max_results=5))
for i, r in enumerate(results):
    print(str(i+1) + ". " + r.get("title", "N/A")[:80])
    print("   URL: " + r.get("href", "N/A")[:80])
    print("   Body: " + r.get("body", "N/A")[:120])
    print()

print("\n=== Exa Test ===")
from search.multi_search import ExaProvider
provider = ExaProvider()
results = provider.search(text, max_results=5)
for i, r in enumerate(results):
    print(str(i+1) + ". " + r.get("title", "N/A")[:80])
    print("   URL: " + r.get("href", "N/A")[:80])
    print("   Body: " + r.get("body", "N/A")[:120])
    print()

print("\n=== DDGS with shorter query (first 6 words) ===")
short_q = "Isbahaysiga Qaran ee Isbeddel doonka"
ddgs2 = DDGS()
results = list(ddgs2.text(short_q, max_results=5))
for i, r in enumerate(results):
    print(str(i+1) + ". " + r.get("title", "N/A")[:80])
    print("   URL: " + r.get("href", "N/A")[:80])
    print("   Body: " + r.get("body", "N/A")[:120])
    print()
