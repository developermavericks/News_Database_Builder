import httpx, asyncio, time

async def test_get():
    print("Direct HTTPX GET to Bing...")
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://www.bing.com/news/search?q=AI&format=rss")
            print(f"BING RESP: {resp.status_code} in {time.time()-start:.2f}s")
    except Exception as e:
        print(f"BING ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_get())
