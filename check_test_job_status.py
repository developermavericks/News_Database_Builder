import asyncio
import sys
import os

# Adjust path to find backend
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from db.database import get_db, ScrapeJob
from sqlalchemy import select

async def check():
    async with get_db() as db:
        stmt = select(ScrapeJob).where(ScrapeJob.id == "dbaa0d1f-d9c5-4df8-8513-ee28f51096fd")
        res = await db.execute(stmt)
        j = res.scalar()
        if j:
            print(f"Job {j.id} | Status: {j.status} | Found: {j.total_found} | Scraped: {j.total_scraped}")
        else:
            print("Job dbaa0d1f-d9c5-4df8-8513-ee28f51096fd not found.")

if __name__ == "__main__":
    asyncio.run(check())
