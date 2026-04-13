
import asyncio
import httpx
import os
import sys

# Ensure backend is in path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

async def diag():
    try:
        from scraper.network import load_proxies
        from scraper.config import USER_AGENTS
        import random
        
        proxies = load_proxies()
        proxy = proxies[0] if proxies else None
        
        url = 'https://news.google.com/rss/search?q=AI%20breakthroughs&hl=en-IN&gl=IN&ceid=IN:en'
        ua = random.choice(USER_AGENTS)
        
        print(f"--- Diagnostic Run ---")
        print(f"URL: {url}")
        print(f"Proxy: {proxy}")
        print(f"User-Agent: {ua}")
        print("-" * 20)
        
        async with httpx.AsyncClient(proxy=proxy, follow_redirects=True, timeout=30) as client:
            resp = await client.get(url, headers={"User-Agent": ua})
            print(f"Final Status: {resp.status_code}")
            print(f"Final URL: {resp.url}")
            print(f"Redirect History:")
            for r in resp.history:
                print(f"  - {r.status_code} -> {r.headers.get('Location')}")
            
            if resp.status_code == 200:
                print(f"Content Length: {len(resp.text)}")
                print(f"Snippet: {resp.text[:200]}")
            else:
                print(f"Failed to get 200 OK. Response Head:\n{resp.headers}")
                
    except Exception as e:
        print(f"Diagnostic Error: {e}")

if __name__ == "__main__":
    asyncio.run(diag())
