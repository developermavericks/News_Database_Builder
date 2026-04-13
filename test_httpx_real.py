
import httpx

async def check():
    async with httpx.AsyncClient() as client:
        # We use a URL that we know returns a 301 if we don't follow redirects
        url = "http://google.com" # Redirects to http://www.google.com
        resp = await client.get(url, follow_redirects=False)
        print(f"Status: {resp.status_code}")
        try:
            resp.raise_for_status()
            print("raise_for_status: No exception for 301")
        except httpx.HTTPStatusError as e:
            print(f"raise_for_status: Raised {e}")
        except Exception as e:
            print(f"raise_for_status: Raised OTHER {type(e).__name__}: {e}")

import asyncio
asyncio.run(check())
