import logging
import json
from datetime import datetime
from sqlalchemy import select, update
from db.database import get_db_sync, ScrapeJob

logger = logging.getLogger("ORCHESTRATOR")

def update_phase_status(db, job_id, phase_name, status):
    """Updates the internal phase stats JSON for monitoring."""
    try:
        res = db.execute(select(ScrapeJob.phase_stats).where(ScrapeJob.id == job_id))
        phase_stats_raw = res.scalar()
        current_stats = json.loads(phase_stats_raw) if phase_stats_raw else {}
        current_stats[phase_name] = {"status": status, "updated_at": datetime.now().isoformat()}
        db.execute(
            update(ScrapeJob)
            .where(ScrapeJob.id == job_id)
            .values(phase_stats=json.dumps(current_stats), current_phase=phase_name)
        )
        db.commit()
    except Exception as e:
        logger.error(f"Error updating phase status for {job_id}: {e}")

def _mark_article_processed(job_id: str):
    """
    Safely increment total_scraped using Redis atomic counters.
    Ensures robustness even if workers crash or restart.
    """
    from scraper.llm import get_redis_sync
    try:
        r = get_redis_sync()
        counter_key = f"nexus:job_counter:{job_id}"
        
        # 1. Atomic increment in Redis
        current_scraped = r.incr(counter_key)
        
        # 2. Sync to DB occasionally (every 5 increments) to maintain visibility 
        # and permanently on the final increment
        with get_db_sync() as db:
            job = db.execute(
                select(ScrapeJob.total_found, ScrapeJob.status)
                .where(ScrapeJob.id == job_id)
            ).first()
            
            if not job: return

            is_final = current_scraped >= job.total_found
            
            if current_scraped % 5 == 0 or is_final:
                db.execute(
                    update(ScrapeJob)
                    .where(ScrapeJob.id == job_id)
                    .values(total_scraped=current_scraped)
                )
                db.commit()

            # 3. Finalize job if all articles are accounted for
            if is_final and job.status != 'completed':
                db.execute(
                    update(ScrapeJob)
                    .where(ScrapeJob.id == job_id)
                    .values(
                        status='completed', 
                        current_phase='Completed', 
                        completed_at=datetime.now(),
                        total_scraped=current_scraped
                    )
                )
                db.commit()
                # Cleanup Redis counter is handled by exploration/discovery phases or natural expiry
                logger.info(f"Job {job_id} effectively finalized: {current_scraped}/{job.total_found} articles.")
                
    except Exception as e:
        logger.error(f"Error marking article processed (Redis-Atomic) for job {job_id}: {e}")
