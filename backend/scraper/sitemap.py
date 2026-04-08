import httpx
import asyncio
import logging
import hashlib
import re
import random
from typing import List, Dict, Set, Optional
from lxml import etree
from datetime import datetime, timedelta
from scraper.config import SECTOR_KEYWORDS, USER_AGENTS
from scraper.llm import get_redis_sync
from scraper.network import load_proxies, ProxyGuard

logger = logging.getLogger("SITEMAP")

class SitemapManager:
    """
    SitemapManager: Handles parallel discovery of news articles via Sitemaps.
    Optimized for 100k+ articles/day scale.
    """
    def __init__(self, target_sectors: Optional[List[str]] = None):
        self.target_sectors = [s.lower() for s in target_sectors] if target_sectors else [
            "artificial intelligence", "sports", "business", "education", "politics"
        ]
        self.redis = get_redis_sync()
        self.proxy_pool = load_proxies() or []
        # High-concurrency client for discovery phase
        self.limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
        self.timeout = httpx.Timeout(15.0)

    def _get_headers(self):
        ua = random.choice(USER_AGENTS)
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
            "Referer": "https://www.google.com/"
        }

    async def _fetch_xml(self, client: httpx.AsyncClient, url: str) -> Optional[bytes]:
        # Retry with different proxies if forbidden
        max_retries = 3
        for attempt in range(max_retries):
            proxy = ProxyGuard.get_healthy_proxy(self.proxy_pool)
            logger.info(f"FETCH START (Attempt {attempt+1}): {url} | Proxy: {proxy[:20] if proxy else 'None'}")
            
            try:
                # Use a fresh client per proxy to avoid session/cookie leaking issues during discovery
                async with httpx.AsyncClient(proxy=proxy, timeout=10.0, follow_redirects=True) as disc_client:
                    resp = await disc_client.get(url, headers=self._get_headers())
                    logger.info(f"FETCH DONE: {url} | Status: {resp.status_code}")
                    
                    if resp.status_code == 200:
                        return resp.content
                    
                    if resp.status_code in [403, 401, 407, 429]:
                        logger.warning(f"SITEMAP BLOCK ({resp.status_code}) on {url} using {proxy[:20]}...")
                        if proxy:
                            ProxyGuard.mark_unhealthy(proxy, duration=600)
                        await asyncio.sleep(2) # Wait before retry
                        continue
                    
                    # Other errors (404, 500) don't merit a retry with different proxy usually
                    break
            except Exception as e:
                logger.error(f"FETCH ERROR: {url} | {type(e).__name__}")
                if proxy:
                    ProxyGuard.mark_unhealthy(proxy, duration=300)
                await asyncio.sleep(1)
                
        return None

    async def parse_sitemap(self, client: httpx.AsyncClient, url: str, depth: int = 0) -> List[Dict]:
        """Recursively parses sitemaps and sitemap indexes using streaming iterparse."""
        if depth > 3: return []
        
        content = await self._fetch_xml(client, url)
        if not content: return []

        articles = []
        try:
            from io import BytesIO
            # Use iterparse for memory-efficient streaming
            # We wrap content in BytesIO for iterparse
            context = etree.iterparse(BytesIO(content), events=('end',), tag=('{*}sitemap', '{*}url'))
            
            for event, elem in context:
                # 1. Sitemap Index Handle
                if elem.tag.endswith('sitemap'):
                    loc = elem.findtext('{*}loc')
                    if loc and any(x in loc.lower() for x in ["news", "article", "2026", "2025"]):
                        # Process nested sitemaps recursively
                        nested = await self.parse_sitemap(client, loc, depth + 1)
                        articles.extend(nested)
                
                # 2. Leaf Sitemap Handle
                elif elem.tag.endswith('url'):
                    loc = elem.findtext('{*}loc')
                    if loc:
                        sector = self._is_sector_match(loc)
                        if sector:
                            lastmod = elem.findtext('{*}lastmod')
                            articles.append({
                                "url": loc,
                                "sector": sector,
                                "published_at": lastmod if lastmod else datetime.now().isoformat()
                            })
                
                # CRITICAL: Clear element to free memory immediately
                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
                    
        except Exception as e:
            logger.error(f"Error streaming sitemap {url}: {e}")
        
        return articles

    async def discover_all(self, sitemap_urls: List[str]) -> List[Dict]:
        """Parallel discovery across multiple sitemap entry points."""
        logger.info(f"Starting discovery across {len(sitemap_urls)} entry points...")
        async with httpx.AsyncClient(limits=self.limits, timeout=self.timeout, follow_redirects=True) as client:
            tasks = [self.parse_sitemap(client, url) for url in sitemap_urls]
            results = await asyncio.gather(*tasks)
            
            all_articles = []
            for res in results: all_articles.extend(res)
            
            logger.info(f"Discovery complete. Found {len(all_articles)} potential new articles.")
            return all_articles

# Default Indian News Sitemaps for Scaling
DEFAULT_INDIA_SITEMAPS = [
    "https://timesofindia.indiatimes.com/sitemapindex.xml",
    "https://www.ndtv.com/sitemap_index.xml",
    "https://www.thehindu.com/sitemap/sitemap-index.xml",
    "https://indianexpress.com/sitemap.xml",
    "https://www.business-standard.com/sitemaps/sitemap-index.xml"
]
