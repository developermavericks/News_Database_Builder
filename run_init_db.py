import sys, os, asyncio

# Correct pathing: setup sys.path so it can find 'backend' and 'scraper'
base_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.join(base_dir, "backend")

# Prepend paths to sys.path to ensure local versions take precedence
sys.path.insert(0, base_dir)
sys.path.insert(0, backend_dir)

# Import the init_db function from backend.db.database
from backend.db import database

async def main():
    await database.init_db()
    print('✅ Database schema initialized successfully.')

if __name__ == '__main__':
    asyncio.run(main())
