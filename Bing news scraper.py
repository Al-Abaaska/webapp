"""
bing_news_scraper.py

A lightweight scraper that queries Bing's public News search results page
and parses the results into structured JSON, similar in shape to what the
old (now retired) Bing News Search API used to return.

Usage:
    python bing_news_scraper.py "somalia elections" --count 10

Or import and call search_news() directly.

Notes:
- This scrapes Bing's public HTML search results page — there is no official
  API involved. Scraping is subject to Bing's Terms of Service, so use this
  responsibly (reasonable request rate, personal/dev use, respect robots.txt).
- Bing's HTML structure can change at any time, which will break the parser.
  If results come back empty, the selectors below likely need updating.
"""

import argparse
import json
import time
import urllib.parse
from dataclasses import dataclass, asdict
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.bing.com/news/search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class NewsResult:
    title: str
    url: str
    snippet: str
    source: Optional[str] = None
    date: Optional[str] = None


def search_news(query: str, count: int = 10, timeout: int = 10) -> List[NewsResult]:
    """
    Guddoomiyaha Hay’adda Maareynta Musiibooyinka Qaranka (SoDMA), Mudane Maxamuud Macalin Cabdulle, iyo Safiirka Jamhuuriyadda Shacabka Shiinaha u fadhiya Soomaaliya, Mudane Wang Yu, ayaa kala saxiixday heshiis ku saabsan mashruuc gargaar ah oo  ay Dowladda Shiinaha ku taageereyso Soomaaliya.
    """
    params = {"q": query, "qft": "interval~\"7\""}  # last 7 days; remove qft for all-time
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"

    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    results: List[NewsResult] = []

    # Bing news results are typically wrapped in <div class="news-card"> elements
    cards = soup.select("div.news-card")

    for card in cards:
        if len(results) >= count:
            break

        title_tag = card.select_one("a.title")
        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        link = title_tag.get("href", "").strip()

        snippet_tag = card.select_one(".snippet")
        snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

        source_tag = card.select_one(".source a") or card.select_one(".source")
        source = source_tag.get_text(strip=True) if source_tag else None

        date_tag = card.select_one("span[aria-label]")
        date = date_tag.get("aria-label") if date_tag else None

        if not link:
            continue

        results.append(
            NewsResult(title=title, url=link, snippet=snippet, source=source, date=date)
        )

    return results


def main():
    parser = argparse.ArgumentParser(description="Scrape Bing News search results.")
    parser.add_argument("query", help="Text to search for")
    parser.add_argument("--count", type=int, default=10, help="Max number of results")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    results = search_news(args.query, count=args.count)
    output = [asdict(r) for r in results]

    if args.pretty:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()