"""
Core Scraping Engine: Distributed Systems Edition.
Standardized on SQLAlchemy (sync) and Celery-based task decoupling.
"""

import asyncio
import time
import os
import sys
import random
import re
import json
import httpx
import feedparser
import trafilatura
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any, Set
from urllib.parse import quote
from sqlalchemy import select, update, insert, text, delete
from sqlalchemy.dialects.postgresql import insert as pg_upsert
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert
import hashlib
from scraper.network import NetworkHandler
# from playwright.sync_api import sync_playwright
# from playwright_stealth import Stealth
from scraper.parser import extract_body, extract_author, extract_author_v2, extract_date, is_junk_body
from scraper.sitemap import SitemapManager, DEFAULT_INDIA_SITEMAPS
from scraper.llm import get_redis_sync
from scraper.orchestrator import update_phase_status

from db.database import get_db_sync, Article, ScrapeJob, WatchedBrand
from scraper.config import SECTOR_KEYWORDS, REGION_MAP, SEARCH_MODIFIERS, USER_AGENTS

# --- Logging ---
import logging
import json

class JsonFormatter(logging.Formatter):
    def _mask_pii(self, msg):
        if not isinstance(msg, str): return msg
        # Patterns to mask
        patterns = [
            (r'(?i)(password["\':\s]+)[^"\'\s,]+', r'\1********'),
            (r'(?i)(Authorization["\':\s]+Bearer\s+)[^"\'\s,]+', r'\1********'),
            (r'(?i)(hashed_password["\':\s]+)[^"\'\s,]+', r'\1********')
        ]
        import re
        for pat, repl in patterns:
            msg = re.sub(pat, repl, msg)
        return msg

    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": self._mask_pii(record.getMessage()),
            "module": record.module,
            "job_id": getattr(record, "job_id", "SYSTEM")
        }
        return json.dumps(log_entry)

# Initialize handler safely
_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())

logging.basicConfig(
    level=logging.INFO,
    handlers=[_handler]
)

logger = logging.getLogger("ENGINE")

def log(msg, job_id="SYSTEM"):
    logger.info(msg, extra={"job_id": job_id})

# --- Exceptions ---
class NexusBaseError(Exception): pass
class ProxyFailureError(NexusBaseError): pass
class RateLimitError(NexusBaseError): pass
class ArticleFetchError(NexusBaseError): pass

from scraper.network import NetworkHandler, ProxyGuard, load_proxies

def random_ua() -> str:
    return random.choice(USER_AGENTS)

# update_phase_status moved to orchestrator.py

def is_job_cancelled(job_id: str) -> bool:
    from scraper.llm import get_redis_sync
    try:
        r = get_redis_sync()
        if r.get("nexus:global_stop") or r.sismember("nexus:cancelled_jobs", job_id):
            return True
        return False
    except:
        return False

def get_upsert_stmt(table, values, index_elements):
    """
    Returns an upsert statement compatible with both PostgreSQL and SQLite (3.24+).
    """
    from db.database import engine_sync
    is_postgres = "postgresql" in engine_sync.url.drivername
    
    if is_postgres:
        return pg_upsert(table).values(values)
    else:
        # SQLite support for ON CONFLICT (added in 3.24)
        return sqlite_upsert(table).values(values)

def verify_brand_relevance(text: str, keywords: List[str]) -> bool:
    if not text or not keywords: return True
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower: return True
    return False

# --- Discovery Phase
async def discover_articles(keywords: List[str], day: date, geo: str, region_name: str, job_id: str, cumulative: set = None, queue: asyncio.Queue = None) -> List[dict]:
    """
    Populates the discovery queue or runs standalone discovery for a single day.
    """
    articles = []
    seen_urls = set()
    
    search_languages = [{"code": "en-IN", "ceid": "IN:en"}]
    with get_db_sync() as db:
        job_res = db.execute(select(ScrapeJob.sector).where(ScrapeJob.id == job_id))
        sector_name = job_res.scalar() or "Technology"
        is_brand_tracker = db.execute(select(WatchedBrand).where(WatchedBrand.name == sector_name)).first() is not None
    
    window_queries = [kw for kw in keywords]
    if not is_brand_tracker:
        for kw in keywords:
            # Add some variations but keep it sane for large missions
            for mod in random.sample(SEARCH_MODIFIERS, min(len(SEARCH_MODIFIERS), 2)): 
                window_queries.append(f"{kw} {mod}")
    
    random.shuffle(window_queries)
    
    if queue is not None:
        # We are in multi-date worker pool mode
        for lang in search_languages:
            for q in window_queries:
                queue.put_nowait((q, day, lang['code'], lang['ceid']))
        return []

    # Standalone mode (legacy/fallback)
    log(f"Discovery mission started for {len(window_queries)} keywords for day {day}", job_id=job_id)
    # ... (If we ever need standalone it would use its own pool here, but we focus on the job pool)
    return articles

# ─── High-Throughput Discovery (Scaling Strategy) ───

async def discover_articles_scaling(job_id: str, sectors: List[str] = None) -> List[dict]:
    """
    Scaling Discovery: Uses SitemapManager for parallel multi-sector scanning.
    """
    update_phase_status(get_db_sync(), job_id, "Discovery", "running")
    sm = SitemapManager(target_sectors=sectors)
    redis = get_redis_sync()
    
    # Fetch all URLs from sitemaps
    raw_articles = await sm.discover_all(DEFAULT_INDIA_SITEMAPS)
    
    final_articles = []
    seen_in_batch = set()
    
    for a in raw_articles:
        url = a["url"]
        url_hash = hashlib.md5(url.encode()).hexdigest()
        
        # Redis-based deduplication (O(1))
        if redis.sismember("nexus:processed_urls", url_hash) or url in seen_in_batch:
            continue
            
        final_articles.append(a)
        seen_in_batch.add(url)
    
    log(f"Discovery Burst: {len(final_articles)} new articles discovered across {sectors or 'all'} sectors.")
    return final_articles

# ─── Scraper Phase ───

async def scrape_only(article: dict, job_id: str, sector: str, region: str, user_id: str) -> Optional[int]:
    if is_job_cancelled(job_id): return None
    try:
        url = article["url"]
        resolved_url = article.get("resolved_url", url)
        raw_html = article.get("raw_html")

        if not raw_html:
            from scraper.orchestrator import _mark_article_processed
            _mark_article_processed(job_id, article_url=url)
            return None

        keywords = []
        with get_db_sync() as db:
            from db.database import WatchedBrand
            brand_obj = db.execute(select(WatchedBrand).where(WatchedBrand.name == sector).where(WatchedBrand.user_id == user_id)).scalar_one_or_none()
            if brand_obj and brand_obj.keywords: keywords = [k.strip() for k in brand_obj.keywords.split(",") if k.strip()]
        
        if not keywords:
            from scraper.config import SECTOR_KEYWORDS
            keywords.extend(SECTOR_KEYWORDS.get(sector.lower(), []))
            if not keywords and "brand_name" in article: keywords = [article["brand_name"]]

        pub_at_str = article.get("published_at")
        try: final_pub_at = datetime.fromisoformat(pub_at_str.replace('Z', '+00:00')) if isinstance(pub_at_str, str) else datetime.now()
        except: final_pub_at = datetime.now()

        from scraper import parser
        body = parser.extract_body(raw_html)
        author_data = parser.extract_author_v2(raw_html)
        author = author_data.get("name")
        
        extra_meta = {"author_metadata": author_data}
        try:
            head_match = re.search(r"<head>.*?</head>", raw_html[:15000], re.I | re.S)
            html_head = head_match.group(0) if head_match else ""
            body_start_match = re.search(r"<body.*?>", raw_html, re.I)
            body_start_idx = body_start_match.end() if body_start_match else 0
            html_top = raw_html[body_start_idx:body_start_idx + 3000]
            html_bottom = raw_html[-3000:]
            extra_meta["html_snippets"] = {"head": html_head[:2000], "top": html_top, "bottom": html_bottom}
        except Exception as e:
            logger.warning(f"Metadata snippeting failed: {e}")

        extracted_date = parser.extract_date(raw_html)
        if extracted_date: final_pub_at = extracted_date

        if keywords:
            title_body = f"{article['title']} {body}"
            is_relevant = any(kw.lower() in title_body.lower() for kw in keywords)
            if not is_relevant and sector.lower() in title_body.lower(): is_relevant = True
            if not is_relevant: body = None
        
        now = datetime.now()
        if final_pub_at.tzinfo: now = now.astimezone(final_pub_at.tzinfo)
        date_invalid = (now - final_pub_at) > timedelta(hours=48)

        article_id = None
        with get_db_sync() as db:
            if not body or date_invalid:
                from scraper.orchestrator import _mark_article_processed
                db.execute(delete(Article).where(Article.url == article["url"]))
                _mark_article_processed(job_id, article_url=article["url"])
            else:
                val_dict = {
                    "title": article["title"], "url": article["url"], "resolved_url": resolved_url,
                    "full_body": body, "author": author, "agency": article.get("agency"),
                    "published_at": final_pub_at, "sector": sector, "region": region,
                    "scrape_job_id": job_id, "user_id": user_id, "extra_metadata": extra_meta
                }
                # Use dialect-agnostic upsert
                stmt = get_upsert_stmt(Article, val_dict, [Article.url])
                
                # Perform the "ON CONFLICT UPDATE" part manually for portability if needed, 
                # but standard SQLAlchemy dialect extensions work well for both if structured right.
                if "postgresql" in stmt.__class__.__module__:
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[Article.url],
                        set_={
                            "full_body": val_dict["full_body"], "author": val_dict["author"],
                            "agency": val_dict["agency"], "extra_metadata": val_dict["extra_metadata"],
                            "published_at": val_dict["published_at"], "scrape_job_id": val_dict["scrape_job_id"],
                            "resolved_url": val_dict["resolved_url"]
                        }
                    ).returning(Article.id)
                else:
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[Article.url],
                        set_={
                            "full_body": val_dict["full_body"], "author": val_dict["author"],
                            "agency": val_dict["agency"], "extra_metadata": val_dict["extra_metadata"],
                            "published_at": val_dict["published_at"], "scrape_job_id": val_dict["scrape_job_id"],
                            "resolved_url": val_dict["resolved_url"]
                        }
                    )
                
                res = db.execute(stmt)
                # For SQLite, returning() might not be supported in all versions/drivers,
                # let's fetch article_id if not returned.
                try: 
                    article_id = res.scalar()
                except:
                    article_id = db.execute(select(Article.id).where(Article.url == article["url"])).scalar()
                    
                # We DO NOT mark processed here if it's going to enrichment.
                # The enrichment task will handle the final count.
                pass
            
            if body and not date_invalid:
                return article_id
    except Exception as e:
        log(f"Scrape fail: {e}")
    return None

def bulk_insert_placeholders(db, job_id, articles, sector, region, user_id):
    """
    Optimized batch ingestion using on_conflict_do_nothing.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_upsert
    
    # Chunker for batch scale
    BATCH_SIZE = 2000
    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i:i + BATCH_SIZE]
        values = []
        for a in batch:
            try:
                values.append({
                    "title": a.get("title", "Article Discovery"), 
                    "url": a["url"], 
                    "published_at": datetime.fromisoformat(a["published_at"].replace('Z', '+00:00')) if isinstance(a["published_at"], str) else datetime.now(),
                    "sector": a.get("sector") or sector, 
                    "region": region, 
                    "scrape_job_id": job_id, 
                    "user_id": user_id, 
                    "agency": a.get("agency") or "Sitemap Discovery"
                })
            except: continue
        
        if values:
            stmt = get_upsert_stmt(Article, values, [Article.url])
            # For bulk placeholder ingestion, DO NOTHING is safer.
            stmt = stmt.on_conflict_do_nothing(index_elements=[Article.url])
            db.execute(stmt)
            db.commit()

async def run_scrape_job(job_id, sector, region, date_from, date_to, search_mode, user_id):
    log(f"Job {job_id} Start.")
    if isinstance(date_from, str): date_from = date.fromisoformat(date_from)
    if isinstance(date_to, str): date_to = date.fromisoformat(date_to)
    
    with get_db_sync() as db:
        db.execute(update(ScrapeJob).where(ScrapeJob.id == job_id).values(status='running', started_at=datetime.now()))
        update_phase_status(db, job_id, "Discovery", "running")
        
        keywords = []
        is_brand = False
        brand_query = select(WatchedBrand).where(WatchedBrand.name == sector).where(WatchedBrand.user_id == user_id)
        brand_obj = db.execute(brand_query).scalar_one_or_none()
        
        if brand_obj:
            is_brand = True
            keywords = [k.strip() for k in brand_obj.keywords.split(",")] if brand_obj.keywords else [brand_obj.name]
            log(f"Job {job_id}: Brand Tracker mode for '{sector}' (Keywords: {keywords})")
        else:
            keywords = SECTOR_KEYWORDS.get(sector.lower(), [sector])
            log(f"Job {job_id}: Sector mode for '{sector}' (Mode: {search_mode}, Keywords: {keywords})")

        geo = REGION_MAP.get(region.lower(), {"geo": "IN"})["geo"]
        all_discovered = []
        cumulative = set()
        
        # Determine date range
        dates = []
        curr = date_from
        while curr <= date_to:
            dates.append(curr)
            curr += timedelta(days=1)

        # --- High-Throughput Worker Pool Discovery ---
        discovery_queue = asyncio.Queue()
        all_discovered = []
        seen_urls = set()
        proxy_pool = load_proxies() or []
        
        # 1. Populate Queue
        for d in dates:
            if is_job_cancelled(job_id): break
            await discover_articles(keywords, d, geo, region, job_id, cumulative=cumulative, queue=discovery_queue)
        
        total_tasks = discovery_queue.qsize()
        log(f"Job {job_id}: Dispatched {total_tasks} discovery tasks to worker pool.", job_id=job_id)

        # 2. Worker Definition
        async def discovery_worker():
            while not discovery_queue.empty():
                if is_job_cancelled(job_id): break
                
                try:
                    q, day, hl, ceid = await discovery_queue.get()
                    
                    # Align parameter order and encoding EXACTLY with user's browser example
                    is_today = day >= date.today()
                    domain = "google.com" 
                    
                    # Ensure hl/ceid are correctly matched for the India context (or generic if missing)
                    # User example: hl=en-IN, gl=IN, ceid=IN:en
                    if is_today:
                        full_q = f"{q} when:1d"
                        rss_url = f"https://news.{domain}/rss/search?q={quote(full_q)}&hl={hl}&gl=IN&ceid={ceid}"
                    else:
                        date_str = day.strftime("%m/%d/%Y")
                        tbs = f"cdr:1,cd_min:{date_str},cd_max:{date_str},sbd:1"
                        rss_url = f"https://news.{domain}/rss/search?q={quote(q)}&hl={hl}&gl=IN&ceid={ceid}&tbs={quote(tbs)}"
                    
                    # Add jitter to further prevent thundering herd behavior across cluster
                    await asyncio.sleep(random.uniform(0.5, 2.0))
                    
                    proxy = ProxyGuard.get_healthy_proxy(proxy_pool)
                    xml_content = await NetworkHandler.get_google_rss(rss_url, proxy=proxy)
                    
                    if xml_content:
                        feed = feedparser.parse(xml_content)
                        found_this_q = 0
                        for entry in feed.entries:
                            link = entry.link
                            if link not in seen_urls and (cumulative is None or link not in cumulative):
                                parsed_date = None
                                if hasattr(entry, 'published_parsed'):
                                    parsed_date = datetime.fromtimestamp(time.mktime(entry.published_parsed)).date()
                                
                                if is_today:
                                    if parsed_date and (day - parsed_date).days > 1: continue
                                elif parsed_date and parsed_date != day: continue
                                    
                                pub_date_str = day.isoformat()
                                if hasattr(entry, 'published_parsed'):
                                    try: pub_date_str = datetime(*entry.published_parsed[:6]).isoformat()
                                    except: pass

                                all_discovered.append({
                                    "title": entry.title, 
                                    "url": link, 
                                    "published_at": pub_date_str, 
                                    "agency": entry.source.title if hasattr(entry, 'source') else "Google News"
                                })
                                seen_urls.add(link)
                                found_this_q += 1
                        
                        if found_this_q > 0:
                            log(f"Discovery: Found {found_this_q} articles for '{q}' ({day})", job_id=job_id)
                
                except Exception as e:
                    log(f"Worker Error: {e}", job_id=job_id)
                finally:
                    discovery_queue.task_done()

        # 3. Spawn exactly 495 workers (Webshare Limit Hardening)
        # We cap workers at min(total_tasks, 495) to avoid excessive overhead on tiny jobs.
        num_workers = min(total_tasks, 495)
        log(f"Job {job_id}: Spawning {num_workers} concurrent discovery workers.", job_id=job_id)
        
        worker_tasks = [asyncio.create_task(discovery_worker()) for _ in range(num_workers)]
        
        # 4. Wait for all tasks to complete or job to be cancelled
        try:
            # Check for cancellation while waiting for the queue to drain
            while not discovery_queue.empty():
                if is_job_cancelled(job_id):
                    log(f"Job {job_id}: Termination signal received. Draining pool.", job_id=job_id)
                    # Drain the queue to stop workers
                    while not discovery_queue.empty():
                        try: discovery_queue.get_nowait(); discovery_queue.task_done()
                        except asyncio.QueueEmpty: break
                    break
                await asyncio.sleep(1)
            
            await discovery_queue.join()
        finally:
            for t in worker_tasks:
                t.cancel()
            await asyncio.gather(*worker_tasks, return_exceptions=True)
        
        db.execute(update(ScrapeJob).where(ScrapeJob.id == job_id).values(cumulative_found=len(cumulative)))
        update_phase_status(db, job_id, "Discovery", "completed")
        
        if not all_discovered:
            db.execute(update(ScrapeJob).where(ScrapeJob.id == job_id).values(status='completed', completed_at=datetime.now(), total_found=0))
            return {"job_id": job_id, "found": 0}

        bulk_insert_placeholders(db, job_id, all_discovered, sector, region, user_id)
        db.execute(update(ScrapeJob).where(ScrapeJob.id == job_id).values(total_found=len(all_discovered), current_phase="Scraping"))
        db.commit()

        from scraper.tasks import scrape_article_node
        for a in all_discovered:
            if is_job_cancelled(job_id): break
            scrape_article_node.delay(a, job_id, sector, region, user_id)
        
        return {"job_id": job_id, "found": len(all_discovered)}
