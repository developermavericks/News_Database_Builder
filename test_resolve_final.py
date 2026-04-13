
import sys
import os
import logging

# Set up logging to see what's happening
logging.basicConfig(level=logging.INFO)

# Ensure backend is in path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from scraper.google_news import resolve_google_news_url_sync

def test_resolve():
    url = "https://news.google.com/rss/articles/CBMiakFVX3lxTE96SXR2cGI3dHBReDYxbUw5bWZlUnhSTHY0Z3ZoYXlYVGRId0ZUQ0hrdWtNQlRtQTVEVlNmME1CZ0QtNWN5SHNqb0F4eHUtbVExcG5wNWZoaUdDcUNsRlFWeklSREFDNEl0UGc?oc=5"
    print(f"Testing Resolution for: {url}")
    resolved = resolve_google_news_url_sync(url)
    print(f"Resolved URL: {resolved}")
    if resolved and "google.com" not in resolved:
         print("SUCCESS: Resolved to external site.")
    elif resolved == url:
         print("FAILED: Returned original URL.")
    else:
         print(f"MIXED: Resolved to {resolved}")

if __name__ == "__main__":
    test_resolve()
