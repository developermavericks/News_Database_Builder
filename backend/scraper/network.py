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

def load_proxies():
    # TEMPORARILY DISABLED TO STABILIZE PIPELINE - PROXIES ARE CURRENTLY SLOW/BLOCKED
    return [] 

import threading

class RateLimiter:
    def __init__(self, max_concurrent: int = 10):
        self._semaphore = threading.BoundedSemaphore(max_concurrent)

    def __enter__(self):
        self._semaphore.acquire()
        time.sleep(random.uniform(0.2, 0.5)) # Optimized delay for stability
        return self

    def __exit__(self, *args):
        self._semaphore.release()

rate_limiter = RateLimiter(max_concurrent=10)

class NetworkHandler:
    @staticmethod
    def _sync_fetch(url, headers, proxy, timeout):
        proxies = {"http": proxy, "https": proxy} if proxy else None
        return requests.get(url, headers=headers, proxies=proxies, timeout=timeout)

    @staticmethod
    def get_google_rss(url: str, proxy: Optional[str] = None, use_cache: bool = True) -> Optional[str]:
        redis = get_redis_sync()
        cache_key = f"nexus:rss_cache:{hashlib.md5(url.encode()).hexdigest()}"
        
        if use_cache:
            try:
                cached = redis.get(cache_key)
                if cached: return cached if isinstance(cached, str) else cached.decode('utf-8')
            except: pass

        headers = {"User-Agent": random.choice(USER_AGENTS), "Accept": "application/rss+xml,text/xml,*/*", "Accept-Language": "en-US,en;q=0.9"}
        for i in range(2):
            try:
                # Offload sync request to a thread pool to avoid blocking the event loop
                resp = NetworkHandler._sync_fetch(url, headers, proxy, 8)
                if resp.status_code == 200:
                    content = resp.text
                    try: redis.setex(cache_key, 600, content) 
                    except: pass
                    return content
                if resp.status_code == 503:
                    try: redis.incrby("nexus:global_503_count", 1)
                    except: pass
                    time.sleep(0.5)
                    continue
                resp.raise_for_status()
            except Exception as e:
                if i == 1: logger.debug(f"RSS fetch failed ({url[:30]}): {e}")
                time.sleep(0.5)
        return None
