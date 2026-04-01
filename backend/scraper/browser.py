import asyncio
import logging
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright
from config import CURRENT_PROFILE
import httpx
import urllib.parse

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

async def _fetch_url_safe(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(
                url, 
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121.0.0.0"}
            )
            if resp.status_code == 200 and len(resp.text) > 1000:
                return resp.text
    except Exception as e:
        logger.debug(f"Fetch failed for {url}: {e}")
        return None

async def scrape_url(url: str, timeout: int = 30000) -> str | None:
    """
    5-Layer Paywall Bypass Waterfall (Production Grade)
    HTTP Fast-Track happens before this. This function handles Layers 2-4.
    """
    logger.info(f"Waterfall started for {url}")
    
    clean_url = urllib.parse.quote_plus(url)
    
    # Layer 2: Google Web Cache
    if "google.com" not in url:
        logger.debug(f"Trying Google Cache for {url}")
        html = await _fetch_url_safe(f"https://webcache.googleusercontent.com/search?q=cache:{clean_url}")
        if html:
            logger.info(f"Waterfall Layer 2 (Google Cache) hit for {url}")
            return html

    # Layer 3: Archive.is (Historical Bypass)
    logger.debug(f"Trying Archive.is for {url}")
    html = await _fetch_url_safe(f"https://archive.is/latest/{clean_url}")
    if html and "No results" not in html and "CAPTCHA" not in html:
        logger.info(f"Waterfall Layer 3 (Archive.is) hit for {url}")
        return html

    # Layer 4: Headless Playwright (JS Execution)
    logger.info(f"Waterfall Layer 4 (Playwright) navigating to {url}")
    try:
        async with browser_pool.acquire_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            # Wait for content to stabilize
            await page.wait_for_timeout(1000)
            
            # --- High-Yield Tuning: Automated Scroll ---
            # Triggers "Lazy Loading" on many modern news sites
            await page.mouse.wheel(0, 500)
            await page.wait_for_timeout(500)
            
            return await page.content()
    except Exception as e:
        logger.error(f"Pool scraper error for {url}: {str(e)}")
        # Layer 5: Readability parsing is handled by parser.extract_body via trafilatura/lxml downstream
        return None
