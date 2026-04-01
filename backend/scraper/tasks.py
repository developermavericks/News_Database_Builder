import logging
import json
import hashlib
import httpx
import trafilatura
from datetime import datetime
from celery_app import app as celery_app
from config import run_async
from db.database import get_db_sync, Article, ScrapeJob
from scraper.orchestrator import _mark_article_processed
from scraper.browser import scrape_url
from sqlalchemy import select, update

logger = logging.getLogger(__name__)


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
    from scraper.engine import scrape_only, is_job_cancelled_sync
    from scraper.google_news import resolve_google_news_url_sync
    from scraper.llm import get_redis_sync
    from scraper.network import load_proxies, ProxyGuard
    
    try:
        if is_job_cancelled_sync(job_id):
            logger.info(f"Scrape task halted for job {job_id} [Reason: Job Cancelled/Global Stop]")
            _mark_article_processed(job_id)
            return None

        url = article_data.get("url") or article_data.get("link")
        if not url:
            _mark_article_processed(job_id)
            return None
            
        # --- FIX 3: Resolve Google News redirect with fallback retry ---
        resolved_url = url
        if "news.google.com" in url:
            resolved_url = resolve_google_news_url_sync(url)
            # If resolution failed, retry once with a different approach
            if not resolved_url or "news.google.com" in resolved_url:
                try:
                    with httpx.Client(timeout=10, follow_redirects=True) as client:
                        resp = client.head(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                        if str(resp.url) and "news.google.com" not in str(resp.url):
                            resolved_url = str(resp.url)
                        else:
                            # Last resort: GET request to force redirect
                            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                            resolved_url = str(resp.url)
                except Exception as resolve_err:
                    logger.warning(f"Google News URL resolution retry failed for {url}: {resolve_err}")
        
        if not resolved_url or "news.google.com/rss/articles" in resolved_url:
            logger.warning(f"Could not resolve Google News URL, skipping: {url}")
            _mark_article_processed(job_id)
            return None
        
        # --- FAST-TRACK SCRAPING (httpx + trafilatura) ---
        html = None
        timeout = 5 if scaling_mode else 20
        
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            }
            
            # --- Proxy Rotation ---
            proxy_pool = load_proxies()
            proxy = ProxyGuard.get_healthy_proxy(proxy_pool) if proxy_pool else None
            
            # Use connection pooling via httpx.Client
            # FIX 1: Store HTML if status is 200, regardless of trafilatura pre-check.
            # The full extract_body() in engine.py has multi-strategy extraction and will
            # do a better job than a simple 400-char pre-gate here.
            with httpx.Client(timeout=timeout, follow_redirects=True, limits=httpx.Limits(max_connections=10), proxy=proxy) as client:
                resp = client.get(resolved_url, headers=headers)
                if resp.status_code == 200 and len(resp.text) > 500:
                    html = resp.text  # Store HTML unconditionally; let extract_body() decide
                elif resp.status_code in [403, 429, 503]:
                    if proxy:
                        ProxyGuard.mark_unhealthy(proxy)
                    logger.debug(f"HTTP {resp.status_code} for {resolved_url}, will try browser fallback")
        except Exception as e:
            logger.debug(f"Fast-track failed for {resolved_url}: {e}")

        # --- FALLBACK: POOLED BROWSER (always try for non-scaling mode if httpx failed) ---
        if not html and not scaling_mode:
            logger.info(f"Falling back to Pooled Browser for {resolved_url}")
            html = run_async(scrape_url(resolved_url))
            
        if not html:
            logger.warning(f"Scrape failed for {resolved_url} (Job: {job_id})")
            _mark_article_processed(job_id)
            return None

        # Move processed data back to article_data for Engine
        article_data["resolved_url"] = resolved_url
        article_data["raw_html"] = html

        # scrape_only internally handles _mark_article_processed for all success/fail paths
        article_id = run_async(scrape_only(article_data, job_id, sector, region, user_id))
        
        if article_id:
            # Mark as processed in Redis for O(1) deduplication in future discovery
            redis = get_redis_sync()
            url_hash = hashlib.md5(resolved_url.encode()).hexdigest()
            redis.sadd("nexus:processed_urls", url_hash)
            
            logger.info(f"Scraped article {article_id}. Triggering enrichment...")
            enrich_article_node.delay(article_id)
            
            try:
                # Trigger semantic embedding generation in the background
                from scraper.semantic import embed_article
                with get_db_sync() as db:
                    from db.database import Article
                    from sqlalchemy import select
                    art = db.execute(select(Article).where(Article.id == article_id)).scalar_one_or_none()
                    if art and art.full_body:
                        embed_article(art.id, art.title, art.full_body, art.sector, art.user_id)
            except Exception as emb_e:
                logger.warning(f"Failed to trigger semantic embedding for article {article_id}: {emb_e}")
        # Note: If article_id is None, scrape_only has already called _mark_article_processed

    except Exception as e:
        # FIX 4: logger is module-level so it is always defined; this block will no longer crash
        _url = article_data.get('url', 'unknown') if isinstance(article_data, dict) else 'unknown'
        logger.error(f"Scrape node failed for {_url}: {e}", exc_info=True)
        if self.request.retries >= self.max_retries:
            _mark_article_processed(job_id)
        raise self.retry(exc=e)

# ─── Enrichment Node (Compute Intensive) ──────────────────────────────────────

@celery_app.task(name="scraper.tasks.enrich_article_node", bind=True, max_retries=3)
def enrich_article_node(self, article_id):
    """
    Task Node 2: Performs AI analysis (Grok/Groq).
    Runs server-side, completely independent of user session.
    """
    from scraper.llm import perform_full_enrichment_sync
    from scraper.engine import is_job_cancelled_sync
    
    with get_db_sync() as db:
        res = db.execute(select(Article).where(Article.id == article_id))
        article = res.scalar_one_or_none()
        if not article or not article.full_body: return
        
        if is_job_cancelled_sync(article.scrape_job_id):
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
        except Exception as e:
            logger.error(f"AI Enrichment failed for article {article_id}: {e}")
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
                # Force-complete if total_scraped is near total_found (within 3 to handle edge cases)
                if job.total_scraped >= max(0, job.total_found - 3):
                    db.execute(
                        update(ScrapeJob).where(ScrapeJob.id == job.id).values(
                            status='completed',
                            current_phase='Completed',
                            total_scraped=job.total_found,  # Correct the counter
                            completed_at=datetime.now()
                        )
                    )
                    logger.info(f"Watchdog force-completed stale job {job.id} ({job.total_scraped}/{job.total_found})")
            
            db.commit()
    except Exception as e:
        logger.error(f"Stale job watchdog error: {e}")
