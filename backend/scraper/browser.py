import asyncio
import logging
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from config import CURRENT_PROFILE

logger = logging.getLogger("BROWSER")

class BrowserPool:
    def __init__(self, size: int = 3):
        """
        Hardware-tuned Browser Pool with health management.
        """
        self.size = size
        self._pool: asyncio.Queue = asyncio.Queue()
        self._playwright = None
        self._initialized = False

    async def _launch_browser(self):
        """Internal helper to launch a fresh Chromium instance with stealth flags."""
        return await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--memory-pressure-off",
                "--disable-blink-features=AutomationControlled",
                "--enable-features=NetworkServiceInProcess",
                "--disable-blink-features=ScriptStreaming",
                "--js-flags=--max-old-space-size=256", 
            ],
        )

    async def init(self):
        if self._initialized: return
        logger.info(f"Initializing Stealth Browser Pool with {self.size} instances...")
        self._playwright = await async_playwright().start()
        for i in range(self.size):
            browser = await self._launch_browser()
            await self._pool.put(browser)
        self._initialized = True

    @asynccontextmanager
    async def acquire_page(self, use_proxy: bool = True):
        """
        Yields a page from a healthy browser with stealth evasions applied.
        Allows disabling proxies for sensitive resolution tasks (Selective Resilience).
        """
        if not self._initialized:
            await self.init()
            
        browser = await self._pool.get()
        
        if not browser.is_connected():
            logger.warning("Pooled browser disconnected. Replacing instance...")
            try: await browser.close()
            except: pass
            browser = await self._launch_browser()

        try:
            proxy_config = None
            if use_proxy:
                from scraper.network import load_proxies, ProxyGuard
                proxy_pool = load_proxies()
                proxy_url = ProxyGuard.get_healthy_proxy(proxy_pool)
                if proxy_url:
                    # Explicit parsing for Playwright credentials stability
                    # Expected format: http://user:pass@host:port
                    try:
                        if "@" in proxy_url:
                            auth_part, server_part = proxy_url.split("@")
                            auth_details = auth_part.replace("http://", "").replace("https://", "").split(":")
                            proxy_config = {
                                "server": f"http://{server_part}",
                                "username": auth_details[0],
                                "password": auth_details[1]
                            }
                        else:
                            proxy_config = {"server": proxy_url}
                    except Exception as pe:
                        logger.warning(f"Proxy parse error: {pe}. Falling back to raw string.")
                        proxy_config = {"server": proxy_url}
            
            # Using latest Chrome 121/122 profile for maximum parity
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            
            context = await browser.new_context(
                user_agent=user_agent,
                proxy=proxy_config,
                viewport={"width": 1280, "height": 720},
                device_scale_factor=1,
            )
            page = await context.new_page()
            
            # Apply Advanced Stealth Evasions (v2.x class-based API)
            await Stealth().apply_stealth_async(page)
            
            # Block heavy/unnecessary resources
            await page.route("**/*", lambda route: route.abort() 
                           if route.request.resource_type in ["image", "media", "font"] 
                           else route.continue_())
            
            yield page, proxy_url
        except Exception as e:
            logger.error(f"Error using pooled page: {e}")
            if "Browser closed" in str(e) or "Target closed" in str(e):
                logger.info("Critical browser error. Replacing instance.")
                try: await browser.close()
                except: pass
                browser = await self._launch_browser()
            raise
        finally:
            try:
                await page.close()
                await context.close()
            except:
                pass
            await self._pool.put(browser)

    async def close(self):
        while not self._pool.empty():
            browser = await self._pool.get()
            try: await browser.close()
            except: pass
        if self._playwright:
            await self._playwright.stop()
        self._initialized = False

# Global pool instance
browser_pool = BrowserPool(size=CURRENT_PROFILE["BROWSER_POOL_SIZE"])

async def scrape_url(url: str, timeout: int = 30000, use_proxy: bool = True) -> str | None:
    """
    High-level async scraper using the pool with stealth applied.
    """
    logger.info(f"Stealth Pool Scraper: Navigating to {url} (Proxy: {use_proxy})")
    
    # Selective Resilience: Try with proxy first, fallback to direct ONLY for non-Google links
    is_google = "news.google.com" in url
    attempts = [use_proxy]
    if use_proxy and not is_google:
        attempts.append(False) # Fallback to direct only if not Google
    elif not use_proxy:
        attempts = [False]
    last_proxy = None
    
    for current_use_proxy in attempts:
        try:
            async with browser_pool.acquire_page(use_proxy=current_use_proxy) as (page, actual_proxy):
                # actual_proxy stores whichever healthy proxy was pulled from the pool
                last_proxy = actual_proxy

                # --- Optimized for Deep Content Extraction ---
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                await page.wait_for_timeout(2000) # Wait for initial hydration
                
                # Multi-stage Deep Scroll (Bypasses Lazy Loading)
                # We scroll in 3 increments to trigger hydration of the main body
                for i in range(3):
                    await page.mouse.wheel(0, 1500)
                    await page.wait_for_timeout(800)
                
                # Scroll back up slightly to trigger any "sticky" content blocks
                await page.mouse.wheel(0, -500)
                await page.wait_for_timeout(500)
                
                # Final stability wait
                await page.wait_for_load_state("networkidle", timeout=5000)
                
                return await page.content()
        except Exception as e:
            err_msg = str(e)
            if current_use_proxy and any(p in err_msg for p in ["TUNNEL_CONNECTION_FAILED", "PROXY_CONNECTION_FAILED", "Proxy unavailable"]):
                if last_proxy:
                    from scraper.network import ProxyGuard
                    ProxyGuard.mark_unhealthy(last_proxy, duration=600) # 10 min blacklist
                
                if is_google:
                    logger.error(f"Scrape Proxy Tunnel Failed. ABORTING direct fallback for Google URL: {url}")
                    return None
                
                logger.warning(f"Scrape Proxy Tunnel Failed. Retrying DIRECT for {url}...")
                continue
            logger.error(f"Stealth Pool Scraper error: {err_msg}")
            return None
    return None

async def resolve_url_via_browser(url: str, timeout: int = 30000, use_proxy: bool = True) -> str:
    """
    Navigates to a URL using a stealthy browser to resolve redirects.
    """
    logger.info(f"Stealth Browser Resolution: Navigating to {url} (Proxy: {use_proxy})")
    try:
        async with browser_pool.acquire_page(use_proxy=use_proxy) as (page, _):
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            await page.wait_for_timeout(1000)
            return page.url
    except Exception as e:
        logger.error(f"Stealth Browser resolution failed: {str(e)}")
        return url
