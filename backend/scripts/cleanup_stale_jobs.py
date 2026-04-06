import os
import sys
from datetime import datetime

# Add the backend directory to the path so we can import from db
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from db.database import get_db_sync, ScrapeJob
from sqlalchemy import update

def cleanup_stale_jobs():
    print("NEXUS: Cleaning up stale 'running' jobs from past sessions...")
    try:
        with get_db_sync() as db:
            # Mark all currently 'running' jobs as 'failed' (stale from old crashed workers)
            # Only if they were started more than 30 minutes ago
            res = db.execute(
                update(ScrapeJob)
                .where(ScrapeJob.status == 'running')
                .values(
                    status='failed', 
                    error='Stale: Worker session disconnected or crashed.',
                    completed_at=datetime.now()
                )
            )
            db.commit()
            print(f"✅ Cleaned up {res.rowcount} jobs.")
    except Exception as e:
        print(f"❌ Cleanup failed: {e}")

if __name__ == "__main__":
    cleanup_stale_jobs()
