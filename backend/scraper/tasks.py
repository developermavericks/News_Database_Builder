import logging
import json
import hashlib
import httpx
import trafilatura
from datetime import datetime
from typing import Optional
from celery_app import app as celery_app
from config import run_async
from db.database import get_db_sync, Article, ScrapeJob
from scraper.orchestrator import _mark_article_processed
from scraper.browser import scrape_url
from sqlalchemy import select, update

logger = logging.getLogger(__name__)

class ScraperPersistence:
    """Manages persistent network resources for workers."""
    _sync_clients: dict = {}
    
    @classmethod
    def get_sync_client(cls, proxy: Optional[str] = None, timeout: int = 15) -> httpx.Client:
        if proxy not in cls._sync_clients or cls._sync_clients[proxy].is_closed:
            cls._sync_clients[proxy] = httpx.Client(
                proxy=proxy,
                timeout=timeout, 
                follow_redirects=True, 
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)
            )
        return cls._sync_clients[proxy]


# --- Orchestrator Task --------------------------------------------------------

@celery_app.task(name="scraper.tasks.run_scrape_task", bind=True)
def run_scrape_task(self, job_id, sector, region, date_from, date_to, search_mode, user_id):
    """
    Orchestrator: Discovers URLs and dispatches independent scraping nodes.
    Bridges to the async run_scrape_job implementation.
    """
    logger.info(f"Starting Orchestrator (Async-Bridged) for job {job_id}")
    from scraper.engine import run_scrape_job
    try:
        run_async(run_scrape_job(
            job_id=job_id,
            sector=sector,
            region=region,
            date_from=date_from,
            date_to=date_to,
            search_mode=search_mode,
            user_id=user_id
        ))
        logger.info(f"Discovery phase for job {job_id} completed.")
    except Exception as e:
        logger.error(f"Orchestrator failed for job {job_id}: {e}")
        raise e

# ─── Scraper Node (I/O Intensive) ─────────────────────────────────────────────

@celery_app.task(
    bind=True, 
    max_retries=5, 
    default_retry_delay=10, 
    retry_backoff=True,
    retry_backoff_max=300,
    rate_limit="200/m"
)
def scrape_article_node(self, article_data, job_id, sector, region, user_id, scaling_mode=False):
    """
    Task Node 1: Fetches HTML and extracts raw body. 
    Optimized for 2.3 articles/sec in scaling mode.
    """
    from scraper.engine import scrape_only, is_job_cancelled
    from scraper.google_news import resolve_google_news_url_sync
    from scraper.llm import get_redis_sync
    
    try:
        url = article_data.get("url") or article_data.get("link")
        if is_job_cancelled(job_id):
            logger.info(f"Scrape task halted for job {job_id} [Reason: Job Cancelled/Global Stop]")
            _mark_article_processed(job_id, article_url=url)
            return None
        
        if not url:
            _mark_article_processed(job_id, article_url="unknown_missing_url")
            return None
            
        # Resolve Google News redirect if needed (scaling mode sitemaps usually have direct URLs)
        resolved_url = url
        if "news.google.com" in url:
            resolved_url = resolve_google_news_url_sync(url)
        
        if not resolved_url:
            _mark_article_processed(job_id, article_url=url)
            return None
        
        # --- FAST-TRACK SCRAPING (Persistent Pooling) ---
        html = None
        timeout = 5 if scaling_mode else 15
        
        try:
            # --- BACKBONE ROTATION ---
            from scraper.network import load_proxies, ProxyGuard
            proxy_pool = load_proxies()
            proxy = ProxyGuard.get_healthy_proxy(proxy_pool)
            
            # 1. Strict Proxy Enforcement for Google
            is_google = "news.google.com" in resolved_url
            if is_google and not proxy:
                logger.error(f"Strict Proxy Hard-Block: No healthy proxies for Google URL {resolved_url}. Raising for retry.")
                raise Exception("Proxy Unavailable for Google News")

            # Use shared client (will be bare IP if proxy is deliberately None for non-Google)
            client = ScraperPersistence.get_sync_client(proxy=proxy, timeout=timeout)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Cookie": "CONSENT=YES+cb.20230531-17-p0.en+FX+908",
            }
            resp = client.get(resolved_url, headers=headers)
            
            # --- PHASE 3: CONTENT VALIDATION ---
            if resp.status_code == 200:
                text_content = trafilatura.extract(resp.text)
                is_paywall = any(p in resp.text.lower() for p in ["subscribe to read", "premium content", "log in to access", "sign up to keep reading"])
                
                # Check for "Thin Content" / Suspected Bot Block (Phase 3.1)
                if text_content and len(text_content) > 500 and not is_paywall:
                    html = resp.text
                else:
                    reason = "Paywall" if is_paywall else "Thin Content (<500 chars)"
                    logger.warning(f"Suspected Block / {reason} detected for {resolved_url}. Escalating to Browser Phase...")
            
            elif resp.status_code in [403, 429, 503]: # Phase 3.1: Explicitly handle Forbidden/Rate-Limited
                logger.warning(f"Fast-track BLOCKED ({resp.status_code}) for {resolved_url}. Rotating proxy and escalating.")
                if proxy:
                    from scraper.network import ProxyGuard
                    ProxyGuard.mark_unhealthy(proxy, duration=1200) # Full 20m cool-down
        
        except Exception as e:
            if proxy:
                from scraper.network import ProxyGuard
                ProxyGuard.mark_unhealthy(proxy, duration=1200)
            logger.debug(f"Fast-track exception for {resolved_url}: {e}")

        # --- FALLBACK: POOLED BROWSER (Max Depth Extraction) ---
        if not html and not scaling_mode:
            is_google = "news.google.com" in resolved_url
            logger.info(f"Escalating to Pooled Browser for {resolved_url} (Max Depth Mode)")
            html = run_async(scrape_url(resolved_url))
            
        if not html:
            logger.warning(f"Maximum Depth Extraction failed for {resolved_url} (Job: {job_id})")
            _mark_article_processed(job_id, article_url=url) # Standardized on ORIGINAL URL
            return None

        # Move processed data back to article_data for Engine
        article_data["resolved_url"] = resolved_url
        article_data["raw_html"] = html

        article_id = run_async(scrape_only(article_data, job_id, sector, region, user_id))
        if article_id:
            # Mark as processed in Redis for O(1) deduplication in future discovery
            redis = get_redis_sync()
            url_hash = hashlib.md5(resolved_url.encode()).hexdigest()
            redis.sadd("nexus:processed_urls", url_hash)
            
            logger.info(f"Scraped article {article_id}. Triggering enrichment...")
            enrich_article_node.delay(article_id, original_url=url) # Pass original URL forward
        else:
            _mark_article_processed(job_id, article_url=url) # Standardized on ORIGINAL URL

    except Exception as e:
        logger.error(f"Scrape node failed for {article_data.get('url')}: {e}")
        if self.request.retries >= self.max_retries:
            _mark_article_processed(job_id)
        raise self.retry(exc=e)

# ─── Enrichment Node (Compute Intensive) ──────────────────────────────────────

@celery_app.task(name="scraper.tasks.enrich_article_node", bind=True, max_retries=3)
def enrich_article_node(self, article_id, original_url=None):
    """
    Task Node 2: Performs AI analysis (Grok/Groq).
    Runs server-side, completely independent of user session.
    """
    from scraper.llm import perform_full_enrichment_sync
    from scraper.engine import is_job_cancelled
    
    with get_db_sync() as db:
        res = db.execute(select(Article).where(Article.id == article_id))
        article = res.scalar_one_or_none()
        if not article or not article.full_body: return
        
        if is_job_cancelled(article.scrape_job_id):
            logger.info(f"Enrichment cancelled for job {article.scrape_job_id}. Skipping article {article_id}")
            return

        try:
            enriched_data = perform_full_enrichment_sync(
                article.full_body, 
                article.title, 
                article.resolved_url or article.url, 
                article.sector,
                context_agency=article.agency,
                extra_metadata=article.extra_metadata
            )
            
            article.summary = enriched_data.get("summary")
            article.sentiment = enriched_data.get("sentiment")
            article.tags = enriched_data.get("tags")
            if enriched_data.get("agency"): article.agency = enriched_data.get("agency")
            if enriched_data.get("author"): article.author = enriched_data.get("author")
            
            db.commit()
            logger.info(f"Successfully enriched article {article_id}")
            
            # --- PROGRESS SYNC ---
            from scraper.orchestrator import _mark_article_processed
            _mark_article_processed(article.scrape_job_id, article_url=original_url or article.url)
        except Exception as e:
            logger.error(f"AI Enrichment failed for article {article_id}: {e}")
            if self.request.retries >= self.max_retries:
                from scraper.orchestrator import _mark_article_processed
                _mark_article_processed(article.scrape_job_id, article_url=original_url or article.url)
            raise self.retry(exc=e, countdown=60)


# ─── Stale Job Watchdog (runs every 5 minutes via Celery Beat) ────────────────

@celery_app.task(name="scraper.tasks.complete_stale_jobs")
def complete_stale_jobs():
    """
    Watchdog: Scans for jobs stuck in 'running' state and marks complete if all articles are scraped.
    Ensures jobs finish even if some tasks crash silently.
    Runs every 5 minutes via Celery Beat schedule.
    """
    from datetime import datetime, timedelta
    try:
        with get_db_sync() as db:
            stale_cutoff = datetime.now() - timedelta(minutes=10)
            running_jobs = db.execute(
                select(ScrapeJob).where(
                    ScrapeJob.status == 'running',
                    ScrapeJob.started_at < stale_cutoff,
                    ScrapeJob.total_found > 0
                )
            ).scalars().all()

            for job in running_jobs:
                from scraper.llm import get_redis_sync
                r = get_redis_sync()
                job_set_key = f"nexus:job_processed_set:{job.id}"
                current_scraped = r.scard(job_set_key)

                # Force-complete if total_scraped is near total_found
                if current_scraped >= max(0, job.total_found - 3):
                    db.execute(
                        update(ScrapeJob).where(ScrapeJob.id == job.id).values(
                            status='completed',
                            current_phase='Completed',
                            total_scraped=max(current_scraped, job.total_found),  # Sync from real truth
                            completed_at=datetime.now()
                        )
                    )
                    logger.info(f"Watchdog synchronized stale job {job.id} ({current_scraped}/{job.total_found})")
            
            db.commit()
    except Exception as e:
        logger.error(f"Stale job watchdog error: {e}")
