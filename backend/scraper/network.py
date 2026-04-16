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

# --- Singleton Cache for Proxy Pool (Speed Optimization for 4k+ proxies) ---
_GLOBAL_PROXY_POOL = None
class ProxyGuard:
    """
    Global Proxy Guard using Redis for distributed state consistency.
    Ensures all worker processes share the same proxy blacklist.
    """
    REDIS_KEY = "nexus:proxy_blacklist"
    
    @classmethod
    def mark_unhealthy(cls, proxy_url: str, duration: int = 1200): # Increased to 20m for reputation recovery
        if not proxy_url: return
        try:
            r = get_redis_sync()
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
        """Strictly returns a healthy proxy from the pool or None. No fallbacks."""
        if not pool: return None
        
        healthy = [p for p in pool if cls.is_healthy(p)]
        
        # Log health stats for large pools
        total = len(pool)
        living = len(healthy)
        if total > 50:
             logger.info(f"PROXY-GUARD: {living}/{total} proxies are currently healthy.")

        if not healthy:
            logger.error("PROXY-GUARD: All available proxies are blacklisted. Hard-blocking request.")
            return None
            
        # Add selection jitter for high-concurrency requests hitting the same endpoint
        random.shuffle(healthy)
        return healthy[0]

def load_proxies(force_reload: bool = False):
    global _GLOBAL_PROXY_POOL
    
    if _GLOBAL_PROXY_POOL is not None and not force_reload:
        return _GLOBAL_PROXY_POOL

    proxies = []
    logger.info("NETWORK: Initializing Proxy Pool from configuration...")
    
    # 1. Primary: Secure credential loading from .env (Backbone/Residential)
    user_base = os.getenv("WEBSHARE_PROXY_USER")
    pw = os.getenv("WEBSHARE_PROXY_PASS")
    host = os.getenv("WEBSHARE_PROXY_HOST", "p.webshare.io")
    use_ip_auth = os.getenv("WEBSHARE_IP_AUTH", "false").lower() == "true"
    proxy_geo = os.getenv("PROXY_GEO", "US") # Default to US to bypass IN blocks
    
    if user_base and pw:
        # Support geo-targeting via username suffix if provided
        targeted_user = f"{user_base}-country-{proxy_geo}" if proxy_geo and "webshare.io" in host else user_base
        
        # Optimized for "Rotating Residential" proxies.
        if "webshare.io" in host:
            logger.info(f"NETWORK: Loading Webshare Rotating {proxy_geo} Residential (IP Auth: {use_ip_auth})")
            if use_ip_auth:
                proxies.append(f"http://{host}:80") # IP-based auth doesn't need creds in URL
            else:
                proxies.append(f"http://{targeted_user}:{pw}@{host}:80")
        else:
            if use_ip_auth:
                proxies.append(f"http://{host}")
            else:
                proxies.append(f"http://{user_base}:{pw}@{host}")
            
    # 2. Secondary/Fallback: Load from local text files
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for fname in ["Webshare 10 proxies.txt", "webshare_proxies.txt"]:
        fpath = os.path.join(base_dir, fname)
        if os.path.exists(fpath):
            logger.info(f"NETWORK: Loading proxy file: {fname}")
            with open(fpath, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    parts = line.split(":")
                    if len(parts) == 4:
                        if use_ip_auth:
                            proxies.append(f"http://{parts[0]}:{parts[1]}")
                        else:
                            proxies.append(f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}")
                    elif len(parts) == 2:
                        proxies.append(f"http://{parts[0]}:{parts[1]}")
    
    proxies = list(dict.fromkeys(proxies))
    if not proxies:
        logger.warning("NETWORK: No proxies loaded. Pipeline will run on server IP (UNSAFE).")
    
    _GLOBAL_PROXY_POOL = proxies
    logger.info(f"NETWORK: Proxy Pool energized with {len(proxies)} unique endpoints.")
    return _GLOBAL_PROXY_POOL


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

        # Discovery Strategy: Multi-Proxy Retry with Exponential Backoff
        max_retries = int(os.getenv("MAX_PROXY_RETRIES", 3))
        proxy_pool = load_proxies()
        
        for attempt in range(max_retries + 1):
            # Pick a fresh healthy proxy or fallback to direct on last attempt
            current_proxy = ProxyGuard.get_healthy_proxy(proxy_pool) if attempt < max_retries else None
            
            async with rate_limiter:
                headers = {
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Cookie": "CONSENT=YES+cb.20230531-17-p0.en+FX+908",
                }
                
                try:
                    client = await NetworkHandler.get_async_client(proxy=current_proxy)
                    # Follow redirects to handle Google News regional/HTTPS jumps
                    resp = await client.get(url, headers=headers, follow_redirects=True, timeout=15)
                    
                    if resp.status_code == 200:
                        content = resp.text
                        if "<rss" in content.lower() or "<feed" in content.lower():
                            await redis.setex(cache_key, 3600, content)
                            return content
                    
                    if resp.status_code == 503:
                        await redis.incrby("nexus:global_503_count", 1)
                        await redis.expire("nexus:global_503_count", 300)
                    
                    if resp.status_code in [403, 429, 503] and current_proxy:
                        ProxyGuard.mark_unhealthy(current_proxy, duration=1200)
                
                except Exception as e:
                    if current_proxy:
                        ProxyGuard.mark_unhealthy(current_proxy, duration=1200)
                        wait_time = (2 ** attempt) + random.uniform(0, 1)
                        logger.warning(f"RSS Discovery Failure (Attempt {attempt+1}/{max_retries}): {e}. Backing off {wait_time:.1f}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    logger.error(f"RSS Discovery: Final direct fetch failed: {e}")
        
        return None
        
        return None
