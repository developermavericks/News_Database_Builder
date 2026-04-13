import asyncio
import httpx
import sys
import os

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.network import NetworkHandler, load_proxies
from scraper.tasks import ScraperPersistence
from scraper.browser import scrape_url

async def test_async_proxy():
    print("\n--- Testing Async Proxy (Discovery Phase) ---")
    proxy_pool = load_proxies()
    if not proxy_pool:
        print("FAILED: No proxies loaded.")
        return
    
    proxy = proxy_pool[0]
    print(f"Using proxy: {proxy[:30]}...")
    client = await NetworkHandler.get_async_client(proxy=proxy)
    try:
        resp = await client.get("https://ipv4.webshare.io/", timeout=10)
        print(f"REPORTED IP: {resp.text.strip()}")
    except Exception as e:
        print(f"ERROR: {e}")

def test_sync_proxy():
    print("\n--- Testing Sync Proxy (Scraping Phase) ---")
    proxy_pool = load_proxies()
    if not proxy_pool:
        print("FAILED: No proxies loaded.")
        return
    
    proxy = proxy_pool[1] if len(proxy_pool) > 1 else proxy_pool[0]
    print(f"Using proxy: {proxy[:30]}...")
    client = ScraperPersistence.get_sync_client(proxy=proxy, timeout=10)
    try:
        resp = client.get("https://ipv4.webshare.io/")
        print(f"REPORTED IP: {resp.text.strip()}")
    except Exception as e:
        print(f"ERROR: {e}")

async def test_browser_proxy():
    print("\n--- Testing Browser Proxy (Rendering Phase) ---")
    try:
        html = await scrape_url("https://ipv4.webshare.io/")
        if html:
            # Extract IP from body
            import re
            match = re.search(r'<body>(.*?)</body>', html, re.S)
            ip = match.group(1).strip() if match else "Could not parse"
            print(f"REPORTED IP: {ip}")
        else:
            print("FAILED: No HTML returned.")
    except Exception as e:
        print(f"ERROR: {e}")

async def main():
    print("NEXUS PROXY CHAIN VERIFICATION")
    print("="*30)
    await test_async_proxy()
    test_sync_proxy()
    await test_browser_proxy()

if __name__ == "__main__":
    asyncio.run(main())
