import httpx
import logging
import base64
import re
from typing import Optional

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
    """
    Hybrid resolution:
    1. Fast Base64 decode (fails on some modern CBM tokens)
    2. Stealth HTTP resolution with httpx
    3. Browser-based resolution (fallback for the 'Google Jail')
    """
    if not url:
        return ""
        
    # 1. Try decoding (Instant, Google specific)
    if "news.google.com" in url:
        decoded = decode_google_news_url(url)
        if decoded:
            return decoded
    
    # 2. Selective Resilience Resolution System (Split-Tunnel)
    try:
        from scraper.network import load_proxies, ProxyGuard
        from config import run_async
        from scraper.browser import resolve_url_via_browser
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        # --- LAYER 1: Direct HTTP (Best for Redirections) ---
        # Goal: Escape the proxy's TLS sniffing to get a clean handshake with Google
        try:
            with httpx.Client(follow_redirects=True, timeout=8) as client:
                resp = client.get(url, headers=headers)
                if "google.com" not in str(resp.url) and resp.status_code == 200:
                    return str(resp.url)
                if "google.com/images/errors/robot.png" in resp.text:
                    logger.info(f"Direct resolution blocked by robot page for {url}. Escalating...")
        except Exception as e:
            logger.warning(f"Layer 1 (Direct HTTP) failed for {url}: {e}. Trying Layer 2...")

        # --- LAYER 2: Direct Browser resolution (Handles JS-based redirects) ---
        try:
            # use_proxy=False bypasses the Webshare SSL interference
            return run_async(resolve_url_via_browser(url, use_proxy=False))
        except Exception as e:
            logger.warning(f"Layer 2 (Direct Browser) failed for {url}: {e}. Trying Layer 3...")

        # --- LAYER 3: Proxy Fallback (Final choice if Direct is blocked) ---
        try:
            proxies = load_proxies()
            proxy = ProxyGuard.get_healthy_proxy(proxies)
            with httpx.Client(proxy=proxy, follow_redirects=True, timeout=8) as client:
                resp = client.get(url, headers=headers)
                if "google.com" not in str(resp.url) and resp.status_code == 200:
                    return str(resp.url)
        except Exception as e:
            logger.error(f"Layer 3 (Proxy) failed for {url}: {e}")

        return url
            
    except Exception as e:
        logger.error(f"Resolution system-level failure for {url}: {e}")
        return url
