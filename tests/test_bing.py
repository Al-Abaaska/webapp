"""Test Bing provider for exact article finding."""
import sys
sys.path.insert(0, '.')

text = 'Isbahaysiga Qaran ee Isbeddel doonka oo soo saaray War-Saxaafadeed'

print("=== Bing Test ===")
from search.multi_search import BingProvider
provider = BingProvider()
results = provider.search(text, max_results=5)
for i, r in enumerate(results):
    print(str(i+1) + ". " + r.get("title", "N/A")[:80])
    print("   URL: " + r.get("href", "N/A")[:80])
    print("   Body: " + r.get("body", "N/A")[:120])
    print()

print("\n=== Bing with shorter query ===")
results2 = provider.search("Isbahaysiga Qaran ee Isbeddel doonka", max_results=5)
for i, r in enumerate(results2):
    print(str(i+1) + ". " + r.get("title", "N/A")[:80])
    print("   URL: " + r.get("href", "N/A")[:80])
    print("   Body: " + r.get("body", "N/A")[:120])
    print()
