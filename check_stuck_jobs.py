import asyncio
import sys
import os
from datetime import datetime, timedelta

# Adjust path to find backend
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from db.database import get_db, ScrapeJob, Article
from sqlalchemy import select, func

async def check():
    async with get_db() as db:
        stmt = select(ScrapeJob).where(ScrapeJob.started_at > datetime.now() - timedelta(hours=3)).order_by(ScrapeJob.started_at.desc())
        res = await db.execute(stmt)
        jobs = res.scalars().all()
        
        print(f"Showing {len(jobs)} jobs from last 3 hours:")
        if not jobs:
            print("No new jobs found.")
            return

        print(f"{'ID':<40} | {'Sect':<15} | {'Status':<12} | {'Found':<5} | {'Scraped':<5} | {'Time'}")
        print("-" * 110)
        for j in jobs:
            print(f"{j.id:<40} | {j.sector:<15} | {j.status:<12} | {j.total_found:<5} | {j.total_scraped:<5} | {j.started_at}")

if __name__ == "__main__":
    asyncio.run(check())
