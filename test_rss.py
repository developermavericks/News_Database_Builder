
import asyncio
import httpx

async def test():
    url = 'https://news.google.com/rss/search?q=Baidu%20AI%20when%3A1d&hl=en-IN&gl=IN&ceid=IN:en'
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(url)
        print(f"URL: {url}")
        print(f"Final URL: {resp.url}")
        print(f"Status: {resp.status_code}")
        print(f"History: {[r.status_code for r in resp.history]}")
        # print(f"Content length: {len(resp.text)}")

asyncio.run(test())
