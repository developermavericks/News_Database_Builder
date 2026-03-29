import asyncio
import logging
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright
from config import CURRENT_PROFILE

logger = logging.getLogger("BROWSER")

class BrowserPool:
    def __init__(self, size: int = 3):
        """
        Hardware-tuned Browser Pool.
        - 16GB: 3 browsers
        - 64GB: 10 browsers
        """
        self.size = size
        self._pool: asyncio.Queue = asyncio.Queue()
        self._playwright = None
        self._initialized = False

    async def init(self):
        if self._initialized: return
        logger.info(f"Initializing Browser Pool with {self.size} instances...")
        self._playwright = await async_playwright().start()
        for i in range(self.size):
            browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--memory-pressure-off",
                    "--js-flags=--max-old-space-size=256", # Cap V8 heap per tab for 16GB stability
                ],
            )
            await self._pool.put(browser)
        self._initialized = True

    @asynccontextmanager
    async def acquire_page(self):
        """
        Correct pattern: yields a page, not a browser.
        Page + context are always closed on exit — no leak.
        """
        if not self._initialized:
            await self.init()
            
        browser = await self._pool.get()
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        )
        page = await context.new_page()
        
        # Block heavy/unnecessary resources
        await page.route("**/*", lambda route: route.abort() 
                       if route.request.resource_type in ["image", "media", "font"] 
                       else route.continue_())
        
        try:
            yield page
        finally:
            await page.close()
            await context.close()
            await self._pool.put(browser)  # always returned to pool

    async def close(self):
        while not self._pool.empty():
            browser = await self._pool.get()
            await browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._initialized = False

# Global pool instance
browser_pool = BrowserPool(size=CURRENT_PROFILE["BROWSER_POOL_SIZE"])

async def scrape_url(url: str, timeout: int = 30000) -> str | None:
    """
    High-level async scraper using the pool.
    """
    logger.info(f"Pool-based scraper: Navigating to {url}")
    try:
        async with browser_pool.acquire_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            # Subtle delay to ensure JS heavy sites settle
            await asyncio.sleep(1)
            return await page.content()
    except Exception as e:
        logger.error(f"Pool scraper error for {url}: {str(e)}")
        return None
