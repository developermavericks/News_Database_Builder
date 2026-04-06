import requests
import json
from datetime import date, timedelta
import os
import sys

# Adjust path to find backend
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from db.database import get_db, User
from sqlalchemy import select
from routers.auth_utils import create_access_token
import asyncio

API_URL = "http://127.0.0.1:8000/api/scrape/start"
# Use matching SECRET_KEY from .env
os.environ["SECRET_KEY"] = "nexus_local_dev_key_2026"

async def trigger_test():
    async with get_db() as db:
        res = await db.execute(select(User).limit(1))
        user = res.scalar_one_or_none()
        if not user:
            print("No user found in DB. Creating a 'system' user.")
            user = User(id="system", name="System", email="system@nexus.local", hashed_password="pw", is_admin=True)
            db.add(user)
            await db.commit()
        
        # FIX: The backend expects 'sub' for email and 'user_id' for ID
        token = create_access_token(data={"sub": user.email, "user_id": user.id, "is_admin": user.is_admin})
        print(f"Generated token for {user.email}")

    headers = {"Authorization": f"Bearer {token}"}
    today = date.today()
    payload = {
        "sector": "artificial intelligence",
        "region": "india",
        "date_from": str(today - timedelta(days=1)),
        "date_to": str(today - timedelta(days=1)),
        "search_mode": "broad"
    }
    
    print(f"Triggering test scrape job for {payload['sector']}...")
    resp = requests.post(API_URL, json=payload, headers=headers)
    print(f"Response: {resp.status_code} - {resp.json()}")

if __name__ == "__main__":
    asyncio.run(trigger_test())
