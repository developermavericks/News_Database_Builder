import asyncio
import time
import random
import logging
import httpx
import hashlib
import os
from typing import Optional, List, Dict, Any
from gevent.lock import BoundedSemaphore
from scraper.llm import get_redis_sync
from scraper.config import USER_AGENTS

logger = logging.getLogger(__name__)

# --- Proxy Management ---
class ProxyGuard:
    """
    Global Proxy Guard using Redis for distributed state consistency.
    Ensures all worker processes share the same proxy blacklist.
    """
    REDIS_KEY = "nexus:proxy_blacklist"
    
    @classmethod
    def mark_unhealthy(cls, proxy_url: str, duration: int = 300):
        if not proxy_url: return
        try:
            r = get_redis_sync()
            # Store in Redis with an expiration
            r.setex(f"{cls.REDIS_KEY}:{proxy_url}", duration, "unhealthy")
            logger.warning(f"PROXY-GUARD: Blacklisted {proxy_url[:30]}... for {duration}s across cluster.")
        except Exception as e:
            logger.error(f"ProxyGuard Redis error: {e}")
        
    @classmethod
    def is_healthy(cls, proxy_url: str) -> bool:
        if not proxy_url: return True
        try:
            r = get_redis_sync()
            return not r.exists(f"{cls.REDIS_KEY}:{proxy_url}")
        except:
            return True

    @classmethod
    def get_healthy_proxy(cls, pool: List[str]) -> Optional[str]:
        healthy = [p for p in pool if cls.is_healthy(p)]
        if not healthy:
            return random.choice(pool) if pool else None
        return random.choice(healthy)

def load_proxies():
    proxies = []
    
    # 1. Primary: Secure credential loading from .env (Backbone Connection)
    user_base = os.getenv("WEBSHARE_PROXY_USER")
    pw = os.getenv("WEBSHARE_PROXY_PASS")
    host = os.getenv("WEBSHARE_PROXY_HOST", "p.webshare.io")
    
    if user_base and pw:
        # Optimized for "Rotating Residential" proxies as seen in USER dashboard.
        # These proxies usually rotate on the server-side, so we don't need the -i suffixes 
        # which are for Backbone static slots and were causing 301 redirects.
        if "webshare.io" in host:
            logger.info(f"NETWORK: Loading Webshare Rotating Residential endpoint for {user_base}")
            # Port 80 is the standard entry point for Rotating Residential
            proxies.append(f"http://{user_base}:{pw}@{host}:80")
        else:
            proxies.append(f"http://{user_base}:{pw}@{host}")
            
    # 2. Secondary/Fallback: Load from local text files
    if not proxies:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for fname in ["Webshare 10 proxies.txt", "webshare_proxies.txt"]:
            fpath = os.path.join(base_dir, fname)
            if os.path.exists(fpath):
                logger.info(f"NETWORK: Falling back to proxy file: {fname}")
                with open(fpath, "r") as f:
                    for line in f:
                        parts = line.strip().split(":")
                        if len(parts) == 4: proxies.append(f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}")
    
    proxies = list(dict.fromkeys(proxies))
    if not proxies:
        logger.warning("NETWORK: No proxies loaded. Pipeline will run on server IP (UNSAFE).")
    return proxies


class RedisRateLimiter:
    """
    Global Rate Limiter using Redis INCR for multi-process coordination.
    Replaces the local asyncio.Semaphore.
    """
    def __init__(self, key: str, limit: int = 3, window: int = 2):
        self.key = f"nexus:ratelimit:{key}"
        self.limit = limit
        self.window = window

    async def __aenter__(self):
        from scraper.llm import get_redis
        r = await get_redis()
        while True:
            # Atomic increment
            count = await r.incr(self.key)
            if count == 1:
                await r.expire(self.key, self.window)
            
            if count <= self.limit:
                # Slot acquired, add small jitter to prevent thundering herd
                await asyncio.sleep(random.uniform(0.1, 0.4))
                return self
            
            # Limit reached, backoff slightly and retry
            await asyncio.sleep(0.5)

    async def __aexit__(self, *args):
        pass

rate_limiter = RedisRateLimiter("google_rss", limit=3, window=2)

class NetworkHandler:
    """
    Handles network I/O with persistent connection pooling and global throttling.
    """
    _clients: Dict[Optional[str], httpx.AsyncClient] = {}
    _client_lock = asyncio.Lock()

    @classmethod
    async def get_async_client(cls, proxy: Optional[str] = None) -> httpx.AsyncClient:
        """Returns a cached AsyncClient for the given proxy to enable pooling."""
        async with cls._client_lock:
            if proxy not in cls._clients or cls._clients[proxy].is_closed:
                # Optimized for 2000 concurrency across 100 backbone slots
                limits = httpx.Limits(max_connections=500, max_keepalive_connections=100)
                cls._clients[proxy] = httpx.AsyncClient(
                    timeout=30, 
                    follow_redirects=True, 
                    limits=limits,
                    proxy=proxy
                )
            return cls._clients[proxy]

    @staticmethod
    async def get_google_rss(url: str, proxy: Optional[str] = None, use_cache: bool = True) -> Optional[str]:
        """
        Refactored Google News RSS fetcher with Discovery-First Resilience.
        If proxy redirects (301/302) or fails, it falls back to a direct connection.
        """
        from scraper.llm import get_redis
        redis = await get_redis()
        cache_key = f"nexus:rss_cache:{hashlib.md5(url.encode()).hexdigest()}"
        
        if use_cache:
            cached = await redis.get(cache_key)
            if cached:
                return cached if isinstance(cached, str) else cached.decode('utf-8')

        # Global Throttle Check
        throttle_count = int(await redis.get("nexus:global_503_count") or 0)
        if throttle_count >= 30:
            await asyncio.sleep(5)
            return None

        # Discovery Strategy: Proxy -> Direct Fallback
        attempts = [proxy, None] if proxy else [None]
        
        for current_proxy in attempts:
            async with rate_limiter:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                }
                
                try:
                    client = await NetworkHandler.get_async_client(proxy=current_proxy)
                    # We EXPLICITLY do NOT follow redirects for some proxy auth pages.
                    # If it's a 301/302, we treat it as a proxy block and fallback.
                    resp = await client.get(url, headers=headers, follow_redirects=False, timeout=10)
                    
                    if resp.status_code == 200:
                        content = resp.text
                        if "<rss" in content.lower() or "<feed" in content.lower():
                            await redis.setex(cache_key, 3600, content)
                            return content
                    
                    if resp.status_code in [301, 302, 307, 308]:
                        if current_proxy:
                            logger.warning(f"RSS Discovery: Proxy Redirect ({resp.status_code}). Triggering DIRECT fallback.")
                            continue # Try next attempt (None/Direct)
                    
                    if resp.status_code == 503:
                        await redis.incrby("nexus:global_503_count", 1)
                        await redis.expire("nexus:global_503_count", 300)
                
                except Exception as e:
                    if current_proxy:
                        logger.warning(f"RSS Discovery: Proxy Error ({e}). Triggering DIRECT fallback.")
                        continue
                    logger.error(f"RSS Discovery: Direct fetch failed: {e}")
        
        return None
