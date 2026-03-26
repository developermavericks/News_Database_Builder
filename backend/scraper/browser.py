import logging
import json
from playwright.sync_api import sync_playwright

logger = logging.getLogger("BROWSER")


def scrape_url(url: str, timeout: int = 45000) -> str | None:
    """
    Fetches HTML using synchronous Playwright to avoid gevent/asyncio deadlocks.
    """
    logger.info(f"Sync-based scraper: Navigating to {url}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
            page = context.new_page()
            
            # Block ads/media to speed up
            page.route("**/*", lambda route: route.abort() 
                           if route.request.resource_type in ["image", "media", "font"] 
                           else route.continue_())
            
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            page.wait_for_timeout(1000)
            content = page.content()
            browser.close()
            return content
            
    except Exception as e:
        logger.error(f"Sync scraper error for {url}: {str(e)}")
        return None
