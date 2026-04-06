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
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())

logger = logging.getLogger("ENGINE")
logger.setLevel(logging.INFO)
# Avoid adding multiple handlers if the module is re-imported
if not logger.handlers:
    logger.addHandler(_handler)

def log(msg, job_id="SYSTEM"):
    logger.info(msg, extra={"job_id": job_id})
    # Fallback to standard print if stdout is redirected in a way that logging misses
    # but we'll try standard logger first.

# --- Exceptions ---
class NexusBaseError(Exception): pass
class ProxyFailureError(NexusBaseError): pass
class RateLimitError(NexusBaseError): pass
class ArticleFetchError(NexusBaseError): pass

from scraper.network import NetworkHandler, ProxyGuard, load_proxies

def random_ua() -> str:
    return random.choice(USER_AGENTS)

# update_phase_status moved to orchestrator.py

async def is_job_cancelled(job_id: str) -> bool:
    from scraper.llm import get_redis
    try:
        r = await get_redis()
        # Redis async client handles connection pool correctly on loop thread
        if await r.get("nexus:global_stop") or await r.sismember("nexus:cancelled_jobs", job_id):
            return True
        return False
    except:
        return False

def is_job_cancelled_sync(job_id: str) -> bool:
    from scraper.llm import get_redis_sync
    try:
        r = get_redis_sync()
        if r.get("nexus:global_stop") or r.sismember("nexus:cancelled_jobs", job_id):
            return True
        return False
    except:
        return False

def verify_brand_relevance(text: str, keywords: List[str]) -> bool:
    if not text or not keywords: return True
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower: return True
    return False

# ─── Discovery Phase ───

import concurrent.futures

def discover_articles(keywords: List[str], day: date, geo: str, region_name: str, job_id: str, cumulative: set = None) -> List[dict]:
    articles = []
    seen_urls = set()
    proxy_pool = load_proxies() or []
    is_today = day >= date.today()

    def fetch_rss_sync(q, hl="en-IN", ceid="IN:en"):
        if is_job_cancelled_sync(job_id): return []
        
        domain = "google.com" 
        if is_today:
            full_q = f"{q} when:1d"
            rss_url = f"https://news.{domain}/rss/search?q={quote(full_q)}&hl={hl}&gl=IN&ceid={ceid}"
        else:
            date_str = day.strftime("%m/%d/%Y")
            tbs = f"cdr:1,cd_min:{date_str},cd_max:{date_str},sbd:1"
            rss_url = f"https://news.{domain}/rss/search?q={quote(q)}&hl={hl}&gl=IN&ceid={ceid}&tbs={quote(tbs)}"
        
        results = []
        try:
            proxy = ProxyGuard.get_healthy_proxy(proxy_pool)
            xml_content = NetworkHandler.get_google_rss(rss_url, proxy=proxy)
            if not xml_content: return []

            feed = feedparser.parse(xml_content)
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

                    results.append({"title": entry.title, "url": link, "published_at": pub_date_str, "agency": entry.source.title if hasattr(entry, 'source') else "Google News"})
            return results
        except Exception as exc:
            log(f"Discovery fail for '{q}': {exc}", job_id=job_id)
            return []

    def fetch_bing_rss_sync(q):
        if is_job_cancelled_sync(job_id): return []
        bing_url = f"https://www.bing.com/news/search?q={quote(q)}&format=rss&mkt=en-IN&sortby=date"
        results = []
        try:
            proxy = ProxyGuard.get_healthy_proxy(proxy_pool)
            xml_content = NetworkHandler.get_google_rss(bing_url, proxy=proxy)
            if not xml_content: return []
            feed = feedparser.parse(xml_content)
            for entry in feed.entries:
                link = getattr(entry, 'link', None)
                if not link or link in seen_urls or (cumulative and link in cumulative): continue
                pub_date_str = day.isoformat()
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    try: pub_date_str = datetime(*entry.published_parsed[:6]).isoformat()
                    except: pass
                results.append({"title": entry.title, "url": link, "published_at": pub_date_str, "agency": "Bing News"})
            return results
        except Exception as exc:
            log(f"Bing discovery fail for '{q}': {exc}", job_id=job_id)
            return []

    search_languages = [{"code": "en-IN", "ceid": "IN:en"}]
    with get_db_sync() as db:
        job_res = db.execute(select(ScrapeJob.sector).where(ScrapeJob.id == job_id))
        sector_name = job_res.scalar() or "Technology"
        is_brand_tracker = db.execute(select(WatchedBrand).where(WatchedBrand.name == sector_name)).first() is not None
    
    window_queries = [kw for kw in keywords]
    if not is_brand_tracker:
        for kw in keywords:
            for mod in random.sample(SEARCH_MODIFIERS, min(len(SEARCH_MODIFIERS), 3)): 
                window_queries.append(f"{kw} {mod}")
    
    random.shuffle(window_queries)
    
    log(f"Discovery mission started: {len(window_queries)} keywords × 2 sources (Google+Bing) for day {day}", job_id=job_id)
    
    all_results = []
    # Use 10 threads for balanced sync discovery
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_query = {}
        for q in window_queries:
            for lang in search_languages:
                future_to_query[executor.submit(fetch_rss_sync, q, lang['code'], lang['ceid'])] = q
                future_to_query[executor.submit(fetch_bing_rss_sync, q)] = q
        
        completed = 0
        total = len(future_to_query)
        for future in concurrent.futures.as_completed(future_to_query):
            res = future.result()
            if res:
                for item in res:
                    if item['url'] not in seen_urls:
                        all_results.append(item)
                        seen_urls.add(item['url'])
            completed += 1
            if completed % 20 == 0:
                log(f"Discovery Progress [{day}]: {completed}/{total} tasks complete.", job_id=job_id)

    if cumulative is not None: cumulative.update(seen_urls)
    log(f"Discovery for {day} completed. Total found: {len(all_results)}", job_id=job_id)
    return all_results

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
    if await is_job_cancelled(job_id): return None
    try:
        url = article["url"]
        resolved_url = article.get("resolved_url", url)
        raw_html = article.get("raw_html")

        if not raw_html:
            from scraper.orchestrator import _mark_article_processed
            _mark_article_processed(job_id)
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

        # FIX 2: Keyword relevance check — no longer deletes articles.
        # Articles are kept even if keyword match fails; body is preserved.
        # A relevance flag is stored in metadata so future filtering/ranking can use it.
        # This prevents silent data loss for articles with thin or paywalled bodies.
        if keywords:
            title_body = f"{article['title']} {body}"
            is_relevant = any(kw.lower() in title_body.lower() for kw in keywords)
            if not is_relevant and sector.lower() in title_body.lower():
                is_relevant = True
            extra_meta["relevance_matched"] = is_relevant
            if not is_relevant:
                logger.debug(f"Article '{article['title'][:60]}' did not match keywords, keeping with low-relevance flag.")
        
        now = datetime.now()
        if final_pub_at.tzinfo: now = now.astimezone(final_pub_at.tzinfo)
        date_invalid = (now - final_pub_at) > timedelta(hours=48)

        article_id = None
        with get_db_sync() as db:
            if not body or date_invalid:
                from scraper.orchestrator import _mark_article_processed
                db.execute(delete(Article).where(Article.url == article["url"]))
                _mark_article_processed(job_id)
            else:
                # Compute title_hash for cross-URL deduplication
                _norm_title = re.sub(r'\W+', '', article["title"].lower())
                _title_hash = hashlib.md5(_norm_title.encode()).hexdigest()
                val_dict = {
                    "title": article["title"], "url": article["url"], "resolved_url": resolved_url,
                    "full_body": body, "author": author, "agency": article.get("agency"),
                    "published_at": final_pub_at, "sector": sector, "region": region,
                    "scrape_job_id": job_id, "user_id": user_id, "extra_metadata": extra_meta,
                    # FIX: Populate word_count and title_hash on every scrape
                    "word_count": len(body.split()) if body else 0,
                    "title_hash": _title_hash,
                }
                from sqlalchemy.dialects.postgresql import insert as pg_upsert
                stmt = pg_upsert(Article).values(**val_dict).on_conflict_do_update(
                    index_elements=[Article.url],
                    set_={
                        "full_body": val_dict["full_body"], "author": val_dict["author"],
                        "agency": val_dict["agency"], "extra_metadata": val_dict["extra_metadata"],
                        "published_at": val_dict["published_at"], "scrape_job_id": val_dict["scrape_job_id"],
                        "resolved_url": val_dict["resolved_url"],
                        "word_count": val_dict["word_count"],
                        "title_hash": val_dict["title_hash"],
                    }
                ).returning(Article.id)
                res = db.execute(stmt)
                article_id = res.scalar()
                from scraper.orchestrator import _mark_article_processed
                _mark_article_processed(job_id)
            
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
            db.execute(pg_upsert(Article).values(values).on_conflict_do_nothing(index_elements=[Article.url]))
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
        cumulative = set()
        total_found = 0
        from scraper.tasks import scrape_article_node

        # Determine date range
        dates = []
        curr = date_from
        while curr <= date_to:
            dates.append(curr)
            curr += timedelta(days=1)

        # High-Efficiency Streaming Discovery
        discovery_tasks = []
        loop = asyncio.get_running_loop()
        for d in dates:
            if await is_job_cancelled(job_id): break
            # Bridge the sync discovery function back to async via executor
            discovery_tasks.append(loop.run_in_executor(None, discover_articles, keywords, d, geo, region, job_id, cumulative))
        
        if discovery_tasks:
            log(f"Discovery: Launching {len(discovery_tasks)} date-window discovery tasks concurrently.")
            # We use as_completed to process discoveries as they finish
            count = 0
            for future in asyncio.as_completed(discovery_tasks):
                discovered_for_date = await future
                if not discovered_for_date: 
                    continue
                
                # Filter out those we've already dispatched in this session?
                # Actually, cumulative set in discover_articles (line 117) handles this for us!
                
                new_articles = discovered_for_date
                total_found += len(new_articles)
                
                # 1. Bulk insert placeholders (DB)
                bulk_insert_placeholders(db, job_id, new_articles, sector, region, user_id)
                
                # 2. Update UI progress (found count)
                db.execute(update(ScrapeJob).where(ScrapeJob.id == job_id).values(total_found=total_found))
                db.commit()

                # 3. DISPATCH IMMEDIATELY (Worker)
                # No more waiting! Scraping starts while discovery is still pending for other dates.
                for a in new_articles:
                    scrape_article_node.delay(a, job_id, sector, region, user_id)
                
                count += 1
                log(f"Discovery: Dispatched {len(new_articles)} articles from date batch {count}/{len(discovery_tasks)}")

        update_phase_status(db, job_id, "Discovery", "completed")
        db.execute(update(ScrapeJob).where(ScrapeJob.id == job_id).values(current_phase="Scraping"))
        db.commit()
        
        if total_found == 0:
            db.execute(update(ScrapeJob).where(ScrapeJob.id == job_id).values(status='completed', completed_at=datetime.now(), total_found=0))
            log(f"Job {job_id} Finished (0 articles found).")
            return {"job_id": job_id, "found": 0}

        log(f"Job {job_id}: Streaming discovery complete. Total {total_found} articles enqueued.")
        return {"job_id": job_id, "found": total_found}
