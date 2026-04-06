import httpx
import logging
import base64
import re
import hashlib
from typing import Optional
from scraper.llm import get_redis_sync

logger = logging.getLogger("GOOGLE_NEWS")


def decode_google_news_url(url: str) -> Optional[str]:
    """
    Decodes the base64 encoded part of a Google News redirect URL.
    This is much faster than using a browser.
    """
    try:
        if "/articles/" not in url:
            return None
        
        # Extract the base64 part
        encoded = url.split("/articles/")[1].split("?")[0]
        
        # Add padding if needed
        padded = encoded + "=="
        
        # Decode base64
        decoded = base64.urlsafe_b64decode(padded)
        
        # Google News encodes the URL in a binary format. 
        # We search for the first occurrence of 'http'
        match = re.search(rb"https?://[^\x00-\x1F\x7F]+", decoded)
        if match:
            return match.group(0).decode("utf-8", errors="ignore")
    except Exception:
        pass
    return None

def resolve_google_news_url_sync(url: str) -> str:
    """Synchronous version of resolve_google_news_url with Redis caching."""
    if not url: return ""
    
    # 1. Check Redis Cache First
    try:
        redis = get_redis_sync()
        cache_key = f"nexus:url_resolve:{hashlib.md5(url.encode()).hexdigest()}"
        cached = redis.get(cache_key)
        if cached: 
            return cached if isinstance(cached, str) else cached.decode("utf-8")
    except: 
        redis = None
        cache_key = None

    # 2. Try decoding (Instant)
    if "news.google.com" in url:
        decoded = decode_google_news_url(url)
        if decoded:
            if redis and cache_key:
                try: redis.setex(cache_key, 86400 * 7, decoded)
                except: pass
            return decoded
    
    # 3. HTTP redirect resolution
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        }
        with httpx.Client(follow_redirects=True, timeout=8) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code in (403, 503) or "google.com/images/errors/robot.png" in resp.text:
                try:
                    from scraper.browser import scrape_url
                    from config import run_async
                    html = run_async(scrape_url(url))
                    if html:
                        import re as _re
                        meta_refresh = _re.search(r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\'][^;]+;\s*url=([^"\'>\s]+)', html, _re.I)
                        if meta_refresh:
                            final_url = meta_refresh.group(1)
                            if redis and cache_key:
                                try: redis.setex(cache_key, 86400 * 3, final_url)
                                except: pass
                            return final_url
                except: pass
                
            final_result = str(resp.url)
            if "news.google.com" not in final_result:
                if redis and cache_key:
                    try: redis.setex(cache_key, 86400 * 7, final_result)
                    except: pass
            return final_result
    except Exception:
        return url
