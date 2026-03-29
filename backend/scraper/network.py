import time
import random
import logging
import httpx
import hashlib
import random
import os
from typing import Optional, List
from gevent.lock import BoundedSemaphore
from scraper.llm import get_redis_sync
from scraper.config import USER_AGENTS

logger = logging.getLogger(__name__)

# --- Proxy Management ---
class ProxyGuard:
    _unhealthy = {} 
    
    @classmethod
    def mark_unhealthy(cls, proxy_url: str, duration: int = 300):
        if not proxy_url: return
        cls._unhealthy[proxy_url] = time.time() + duration
        logger.info(f"PROXY-GUARD: Blacklisted {proxy_url[:30]}... for {duration}s")
        
    @classmethod
    def is_healthy(cls, proxy_url: str) -> bool:
        if not proxy_url: return True
        expiry = cls._unhealthy.get(proxy_url, 0)
        if time.time() > expiry:
            if proxy_url in cls._unhealthy: del cls._unhealthy[proxy_url]
            return True
        return False

    @classmethod
    def get_healthy_proxy(cls, pool: List[str]) -> Optional[str]:
        healthy = [p for p in pool if cls.is_healthy(p)]
        return random.choice(healthy) if healthy else (random.choice(pool) if pool else None)

def load_proxies():
    proxies = []
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for fname in ["Webshare 10 proxies.txt", "webshare_proxies.txt"]:
        fpath = os.path.join(base_dir, fname)
        if os.path.exists(fpath):
            with open(fpath, "r") as f:
                for line in f:
                    parts = line.strip().split(":")
                    if len(parts) == 4: proxies.append(f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}")
    
    # Secure credential loading from .env
    user_base = os.getenv("WEBSHARE_PROXY_USER")
    pw = os.getenv("WEBSHARE_PROXY_PASS")
    host = os.getenv("WEBSHARE_PROXY_HOST", "p.webshare.io")
    
    if user_base and pw:
        # If the user provides a single host, we assume it's the webshare revolving proxy
        if "webshare.io" in host:
            for i in range(1, 11): proxies.append(f"http://{user_base}-{i}:{pw}@{host}:80")
        else:
            proxies.append(f"http://{user_base}:{pw}@{host}")
            
    proxies = list(dict.fromkeys(proxies))
    return proxies

import asyncio
import random
import logging
import httpx
import hashlib
import os
from typing import Optional, List
from scraper.llm import get_redis_sync
from scraper.config import USER_AGENTS

logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self, max_concurrent: int = 3):
        # 3 concurrent requests to Google News — conservative for one IP
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def __aenter__(self):
        await self.semaphore.acquire()
        # Jitter AFTER acquiring the slot, so it staggers requests
        # without holding up other waiters during the sleep
        await asyncio.sleep(random.uniform(0.5, 2.0))
        return self

    async def __aexit__(self, *args):
        self.semaphore.release()

rate_limiter = RateLimiter(max_concurrent=3)

class NetworkHandler:
    @staticmethod
    async def get_google_rss(url: str, proxy: Optional[str] = None, use_cache: bool = True) -> Optional[str]:
        """
        Refactored Google News RSS fetcher using the new hardware-tuned RateLimiter.
        """
        redis = get_redis_sync()
        cache_key = f"nexus:rss_cache:{hashlib.md5(url.encode()).hexdigest()}"
        
        if use_cache:
            cached = redis.get(cache_key)
            if cached:
                return cached if isinstance(cached, str) else cached.decode('utf-8')

        # Global Throttle Check
        throttle_count = int(redis.get("nexus:global_503_count") or 0)
        if throttle_count > 5:
            await asyncio.sleep(60)
            redis.delete("nexus:global_503_count")

        async with rate_limiter:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept-Language": "en-US,en;q=0.9",
            }
            
            limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
            async with httpx.AsyncClient(timeout=30, follow_redirects=True, limits=limits, proxy=proxy) as client:
                attempts = 3
                for i in range(attempts):
                    try:
                        resp = await client.get(url, headers=headers)
                        
                        if resp.status_code == 200:
                            content = resp.text
                            redis.setex(cache_key, 3600, content)
                            return content
                        
                        if resp.status_code == 503:
                            logger.warning(f"Google 503 detected. Attempt {i+1}/{attempts}")
                            redis.incrby("nexus:global_503_count", 1)
                            await asyncio.sleep(5 * (i + 1))
                            continue
                            
                        resp.raise_for_status()
                    except Exception as e:
                        if i == attempts - 1:
                            logger.error(f"Failed RSS fetch: {e}")
                        await asyncio.sleep(2)
        return None
