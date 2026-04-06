import asyncio, os, sys, logging, time
from datetime import date, timedelta
from dotenv import load_dotenv

# Ensure backend is in path
sys.path.insert(0, os.path.join(os.getcwd(), "backend"))

# Load env
load_dotenv("backend/.env")
if os.path.exists("backend/.env.local"):
    load_dotenv("backend/.env.local", override=True)

logging.basicConfig(level=logging.INFO)

from scraper.engine import discover_articles

async def test_minimal():
    print("Testing Minimal Discovery (1 Keyword, Bing Only)...")
    start_time = time.time()
    # Use a sector name that's likely in WatchedBrand to skip modifiers
    results = await discover_articles(["AI"], date.today() - timedelta(days=1), "IN", "india", "TEST_MIN")
    duration = time.time() - start_time
    print(f"DONE! Found {len(results)} items in {duration:.2f} seconds.")

if __name__ == "__main__":
    asyncio.run(test_minimal())
