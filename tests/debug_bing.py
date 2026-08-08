"""Debug Bing HTML response."""
import sys
sys.path.insert(0, '.')
import requests
import re

text = 'Isbahaysiga Qaran ee Isbeddel doonka oo soo saaray War-Saxaafadeed'

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml',
    'Accept-Language': 'en-US,en;q=0.9,so;q=0.8',
})
resp = session.get(
    'https://www.bing.com/search',
    params={'q': text, 'count': 5},
    timeout=15,
)
print("Status:", resp.status_code)
print("Length:", len(resp.text))

# Check for captcha/blocked indicators
if 'captcha' in resp.text.lower():
    print("CAPTCHA detected!")
if 'verify you are human' in resp.text.lower():
    print("Human verification detected!")
if 'blocked' in resp.text.lower():
    print("Blocked detected!")

# Print a portion of the HTML to inspect
print("\n--- HTML snippet (first 2000 chars) ---")
print(resp.text[:2000])
print("\n--- Looking for search result patterns ---")

# Try various patterns
patterns = [
    r'<li class="b_algo">(.*?)</li>',
    r'class="b_algo"',
    r'b_results',
    r'<h2><a',
    r'<cite>',
]
for p in patterns:
    matches = re.findall(p, resp.text, re.DOTALL)
    print(f"Pattern '{p}': {len(matches)} matches")
