import os
import sys
from datetime import datetime

# Add the backend directory to the path so we can import from db
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from db.database import get_db_sync, ScrapeJob
from sqlalchemy import select, desc

def list_latest_jobs():
    print("NEXUS: Fetching latest jobs summary...")
    try:
        with get_db_sync() as db:
            jobs = db.execute(
                select(ScrapeJob)
                .order_by(desc(ScrapeJob.started_at))
                .limit(10)
            ).scalars().all()
            
            print(f"{'ID':<15} | {'Sector':<15} | {'Status':<12} | {'Found':<6} | {'Scraped':<8} | {'Date'}")
            print("-" * 75)
            for j in jobs:
                print(f"{j.id[:14]:<15} | {j.sector[:14]:<15} | {j.status:<12} | {j.total_found:<6} | {j.total_scraped:<8} | {j.started_at.strftime('%m-%d %H:%M')}")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    list_latest_jobs()
