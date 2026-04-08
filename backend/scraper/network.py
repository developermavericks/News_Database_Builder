import time
import random
import logging
import httpx
import hashlib
import random
import os
from typing import Optional, List
import threading
import urllib.parse
import requests
from scraper.llm import get_redis_sync
from scraper.config import USER_AGENTS

logger = logging.getLogger(__name__)

class ProxyGuard:
    @classmethod
    def mark_unhealthy(cls, proxy_url: str, duration: int = 300):
        if not proxy_url: return
        try:
            get_redis_sync().setex(f"nexus:proxy_bad:{hashlib.md5(proxy_url.encode()).hexdigest()}", duration, "1")
            logger.info(f"PROXY-GUARD: Blacklisted {proxy_url[:30]}... for {duration}s")
        except: pass
        
    @classmethod
    def is_healthy(cls, proxy_url: str) -> bool:
        if not proxy_url: return True
        try:
            return not bool(get_redis_sync().exists(f"nexus:proxy_bad:{hashlib.md5(proxy_url.encode()).hexdigest()}"))
        except: return True

    @classmethod
    def get_healthy_proxy(cls, pool: List[str]) -> Optional[str]:
        healthy = [p for p in pool if cls.is_healthy(p)]
        return random.choice(healthy) if healthy else (random.choice(pool) if pool else None)

_PROXY_CACHE = []
_LAST_LOAD_TIME = 0

def load_proxies():
    """Returns the singular Webshare Rotating Proxy endpoint."""
    rotating_proxy = os.getenv("WEBSHARE_PROXY_URL")
    if rotating_proxy:
        logger.info("NETWORK: Using Webshare Rotating Proxy Endpoint.")
        return [rotating_proxy]
        
    global _PROXY_CACHE, _LAST_LOAD_TIME
    # ... legacy fallback ...

import threading

class RateLimiter:
    def __init__(self, max_concurrent: int = 10):
        self._semaphore = threading.BoundedSemaphore(max_concurrent)

    def __enter__(self):
        self._semaphore.acquire()
        time.sleep(random.uniform(0.01, 0.05)) # Reduced delay for high-speed Discovery
        return self

    def __exit__(self, *args):
        self._semaphore.release()

# Optimized concurrency for Rotating Proxy (limit 500 concurrent connections)
# We use 480 to leave a small buffer for OS/Redis metadata overhead.
rate_limiter = RateLimiter(max_concurrent=480)

class NetworkHandler:
    @staticmethod
    def _sync_fetch(url, headers, proxy, timeout):
        proxies = {"http": proxy, "https": proxy} if proxy else None
        return requests.get(url, headers=headers, proxies=proxies, timeout=timeout)

    @staticmethod
    def get_google_rss(url: str, proxy: Optional[str] = None) -> Optional[str]:
        """
        Fetches RSS content. Always fetches LIVE data (no caching) to prevent stale 0-results.
        Forces Discovery through the proxy backbone to bypass local IP blocks.
        """
        # Discovery standard headers
        headers = {
            "User-Agent": random.choice(USER_AGENTS), 
            "Accept": "application/rss+xml,text/xml,*/*",
            "Connection": "close"
        }
        
        # DISCOVERY REDIRECTION: Force all RSS traffic through the proxy backbone.
        active_proxy = proxy or load_proxies()[0] 
        
        for i in range(3): 
            try:
                proxies = {"http": active_proxy, "https": active_proxy} if active_proxy else None
                resp = requests.get(url, headers=headers, proxies=proxies, timeout=15, follow_redirects=True)
                
                if resp.status_code == 200:
                    content = resp.text
                    if len(content) > 500: 
                        return content
                
                logger.warning(f"Discovery proxy attempt {i+1} status: {resp.status_code}")
                time.sleep(1)
                time.sleep(0.5)
            except Exception as e:
                if i == 1: logger.debug(f"RSS fetch failed ({url[:30]}): {e}")
                time.sleep(0.5)
        return None
