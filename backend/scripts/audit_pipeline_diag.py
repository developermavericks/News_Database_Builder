import asyncio
import sys
import os
import logging
import json
from datetime import datetime

# Add backend root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set up logging to both file and console
log_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit_pipeline_report.txt")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, mode='w'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("AUDIT")

async def run_audit():
    logger.info("==================================================")
    logger.info("  NEXUS - PIPELINE COMPREHENSIVE AUDIT")
    logger.info("==================================================")
    
    from scraper.network import NetworkHandler, load_proxies, ProxyGuard
    from scraper.google_news import resolve_google_news_url_sync
    from scraper.parser import extract_body, extract_author_v2
    from scraper.llm import perform_full_enrichment_sync
    from config import run_async
    
    # 1. CORE COMPONENT AUDIT
    logger.info("\n[1/5] Core Component Verification:")
    try:
        proxies = load_proxies()
        logger.info(f"[OK] Proxies Loaded: {len(proxies)} slots available.")
        
        from scraper.llm import OLLAMA_BASE_URL, OLLAMA_MODEL
        logger.info(f"[OK] Ollama Config: {OLLAMA_MODEL} at {OLLAMA_BASE_URL}")
    except Exception as e:
        logger.error(f"[ERROR] Component Check Failed: {e}")
        return

    # 2. DISCOVERY AUDIT
    logger.info("\n[2/5] Discovery Audit (Google News RSS):")
    test_q = "Reliance"
    hl, gl, ceid = "en-IN", "IN", "IN:en"
    from urllib.parse import quote
    rss_url = f"https://news.google.com/rss/search?q={quote(test_q)}&hl={hl}&gl={gl}&ceid={ceid}"
    
    try:
        proxy = ProxyGuard.get_healthy_proxy(proxies)
        logger.info(f"Using Proxy: {proxy[:35]}...")
        xml = await NetworkHandler.get_google_rss(rss_url, proxy=proxy, use_cache=False)
        if xml and "<item>" in xml:
            import feedparser
            feed = feedparser.parse(xml)
            logger.info(f"[OK] Discovery Success: Found {len(feed.entries)} articles for '{test_q}'.")
            target_article = feed.entries[0]
            logger.info(f"Target Title: {target_article.title}")
            logger.info(f"Target URL: {target_article.link}")
        else:
            logger.error("[ERROR] Discovery Failed: No XML content or 0 items.")
            return
    except Exception as e:
        logger.error(f"[ERROR] Discovery Error: {e}")
        return

    # 3. RESOLUTION AUDIT
    logger.info("\n[3/5] Resolution Audit (Multi-Layer):")
    gnews_url = target_article.link
    try:
        resolved_url = resolve_google_news_url_sync(gnews_url)
        if resolved_url and "news.google.com" not in resolved_url:
            logger.info(f"[OK] Resolution Success: {resolved_url}")
        else:
            logger.warning(f"[WARN] Resolution did not escape Google domain: {resolved_url}")
            resolved_url = gnews_url # Fallback for test
    except Exception as e:
        logger.error(f"[ERROR] Resolution Error: {e}")
        return

    # 4. SCRAPING & EXTRACTION AUDIT
    logger.info("\n[4/5] Scraping & Extraction Audit:")
    try:
        from scraper.tasks import ScraperPersistence
        client = ScraperPersistence.get_sync_client(proxy=proxy)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"}
        resp = client.get(resolved_url, headers=headers)
        
        if resp.status_code == 200:
            html = resp.text
            body = extract_body(html)
            author_data = extract_author_v2(html)
            
            logger.info(f"[OK] Scraping Success: {len(html)} bytes fetched.")
            logger.info(f"[OK] Extraction: Body Length = {len(body or '')}")
            logger.info(f"[OK] Extraction: Author = {author_data.get('name')} (Method: {author_data.get('method')})")
        else:
            logger.error(f"[ERROR] Scraping Failed: Status {resp.status_code}")
            return
    except Exception as e:
        logger.error(f"[ERROR] Scraping Error: {e}")
        return

    # 5. ENRICHMENT & SUMMARIZATION AUDIT (THE FIX VERIFICATION)
    logger.info("\n[5/5] Enrichment & Summarization Audit:")
    try:
        enrich_results = perform_full_enrichment_sync(
            body=body,
            title=target_article.title,
            url=resolved_url,
            sector="General News",
            context_agency=target_article.source.title if hasattr(target_article, 'source') else "Google News",
            extra_metadata={"author_metadata": author_data}
        )
        
        summary = enrich_results.get("summary")
        if summary and "*" in summary: # Usually bullet points have * or -
            logger.info("[OK] Summarization Success: AI Summary generated.")
            logger.info(f"Summary Snippet: {summary[:200]}...")
        else:
            logger.warning(f"[WARN] Summarization might be placeholder or failed: {summary[:100] if summary else 'None'}")
            
        logger.info(f"[OK] Enrichment: Publication = {enrich_results.get('agency')}")
        logger.info(f"[OK] Enrichment: Final Author = {enrich_results.get('author')}")
        logger.info(f"[OK] Enrichment: Sentiment = {enrich_results.get('sentiment')}")

    except Exception as e:
        logger.error(f"[ERROR] Enrichment Error: {e}")

    logger.info("\n" + "="*50)
    logger.info(" AUDIT COMPLETE")
    logger.info(f" Full report saved to: {log_file}")
    logger.info("="*50)

if __name__ == "__main__":
    asyncio.run(run_audit())
