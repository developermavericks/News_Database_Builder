import asyncio
import httpx
import feedparser
from urllib.parse import quote

async def test_rss():
    q = "Google"
    full_q = f"{q} when:1d"
    hl = "en-IN"
    gl = "IN"
    ceid = "IN:en"
    domain = "google.com"
    
    rss_url = f"https://news.{domain}/rss/search?q={quote(full_q)}&hl={hl}&gl={gl}&ceid={ceid}"
    print(f"Testing RSS URL: {rss_url}")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    }
    
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(rss_url, headers=headers)
        print(f"Status Code: {resp.status_code}")
        
        if resp.status_code == 200:
            feed = feedparser.parse(resp.text)
            print(f"Found {len(feed.entries)} entries.")
            for entry in feed.entries[:3]:
                print(f"- {entry.title} ({entry.link[:50]}...)")
        else:
            print(f"Response Body (Start): {resp.text[:200]}")

if __name__ == "__main__":
    asyncio.run(test_rss())
