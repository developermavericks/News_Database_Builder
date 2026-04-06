import os, sys, time, json
import asyncio
from datetime import date
from typing import List, Dict, Any

# Adjust sys.path to ensure we can import from backend
sys.path.append(os.path.join(os.getcwd(), "backend"))

from scraper.network import NetworkHandler, ProxyGuard, load_proxies
from scraper.engine import discover_articles
from db.database import get_db_sync
from scraper.llm import get_redis_sync

def test_log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def run_tests():
    test_log("=== NEXUS COMPREHENSIVE PIPELINE TEST ===")
    
    # 1. DB/Redis
    try:
        r = get_redis_sync()
        r.ping()
        test_log("[OK] Redis: OK")
    except Exception as e:
        test_log(f"[FAIL] Redis: FAILED - {e}")
        return

    try:
        with get_db_sync() as db:
            from sqlalchemy import text
            db.execute(text("SELECT 1"))
        test_log("[OK] PostgreSQL: OK")
    except Exception as e:
        test_log(f"[FAIL] PostgreSQL: FAILED - {e}")
        return

    # 2. Proxy Pool
    proxies = load_proxies()
    test_log(f"[INFO] Proxies: Loaded {len(proxies)} from pool.")
    if not proxies:
        test_log("[WARN] Warning: Proxy pool is EMPTY. Test will proceed direct.")

    # 3. Networking Component (Sync-Bridge)
    test_log("[TEST] Testing RSS Fetch (Google) WITHOUT PROXY...")
    test_url = "https://news.google.com/rss/search?q=NVIDIA&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        # Use direct connection for test
        proxy = None
        start = time.time()
        xml = NetworkHandler.get_google_rss(test_url, proxy=proxy, use_cache=False)
        dur = time.time() - start
        if xml and "<rss" in xml.lower():
            test_log(f"[OK] Google RSS Fetch: SUCCESS ({dur:.2f}s)")
        else:
            test_log(f"[FAIL] Google RSS Fetch: FAILED (No XML or unexpected response)")
    except Exception as e:
        test_log(f"[FAIL] Google RSS Fetch: EXCEPTION - {e}")

    # 4. Engine Discovery Logic
    test_log("[TEST] Testing Engine Discovery (Keyword: 'NVIDIA')...")
    try:
        start = time.time()
        # discover_articles is now SYNC in engine.py
        results = discover_articles(["NVIDIA"], date.today(), "IN", "India", "TEST_JOB_123")
        dur = time.time() - start
        if results:
            test_log(f"[OK] Engine Discovery: SUCCESS (Found {len(results)} articles in {dur:.2f}s)")
            test_log(f"   First Article: {results[0]['title'][:50]}...")
        else:
            test_log(f"[FAIL] Engine Discovery: FAILED (Found 0 articles)")
    except Exception as e:
        test_log(f"[FAIL] Engine Discovery: EXCEPTION - {e}")

    # 5. Enrichment Bridge
    test_log("[TEST] Testing Enrichment Logic (Trafilatura + Metadata)...")
    sample_url = results[0]['url'] if results else "https://www.reuters.com/technology/nvidia-ceo-unveils-new-ai-chips-2024-03-18/"
    try:
        from scraper.parser import extract_body
        import httpx
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(sample_url)
            html = resp.text
            body = extract_body(html)
            if body and len(body) > 100:
                test_log(f"[OK] Trafilatura Extraction: SUCCESS ({len(body)} chars)")
            else:
                test_log(f"[FAIL] Trafilatura Extraction: FAILED (Body too short or empty)")
    except Exception as e:
        test_log(f"[FAIL] Trafilatura Extraction: EXCEPTION - {e}")

    test_log("=== TEST SUITE COMPLETE ===")

if __name__ == "__main__":
    run_tests()
