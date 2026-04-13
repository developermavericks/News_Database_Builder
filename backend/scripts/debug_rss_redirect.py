import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

async def test_redirect():
    url = "https://news.google.com/rss/search?q=Reliance&hl=en-IN&gl=IN&ceid=IN:en"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    }
    
    # Try without proxy
    print(f"\nTesting WITHOUT Proxy:")
    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
        try:
            resp = await client.get(url, headers=headers)
            print(f"Final URL: {resp.url}")
            print(f"Status Code: {resp.status_code}")
            print(f"History: {[r.status_code for r in resp.history]}")
        except Exception as e:
            print(f"Request failed: {e}")

    # Try with proxy
    print(f"\nTesting WITH Proxy:")
    user_base = os.getenv("WEBSHARE_PROXY_USER")
    pw = os.getenv("WEBSHARE_PROXY_PASS")
    host = os.getenv("WEBSHARE_PROXY_HOST", "p.webshare.io")
    proxy = f"http://{user_base}-1:{pw}@{host}:80"
    
    async with httpx.AsyncClient(proxy=proxy, follow_redirects=True, timeout=10) as client:
        try:
            resp = await client.get(url, headers=headers)
            print(f"Final URL: {resp.url}")
            print(f"Status Code: {resp.status_code}")
            print(f"History: {[r.status_code for r in resp.history]}")
            if resp.status_code != 200:
                print(f"Response Header Location: {resp.headers.get('Location')}")
                print(f"Response Body Snippet: {resp.text[:500]}")
        except Exception as e:
            print(f"Request failed: {e}")

    # Try with proxy for httpbin
    print(f"\nTesting WITH Proxy (httpbin.org):")
    target_bin = "https://httpbin.org/ip"
    async with httpx.AsyncClient(proxy=proxy, follow_redirects=True, timeout=10) as client:
        try:
            resp = await client.get(target_bin, headers=headers)
            print(f"Final URL: {resp.url}")
            print(f"Status Code: {resp.status_code}")
            print(f"Body: {resp.text}")
        except Exception as e:
            print(f"Request failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_redirect())
