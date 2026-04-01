import psycopg2, os
from dotenv import load_dotenv

# Load env to get DB URL
load_dotenv("backend/.env")
load_dotenv("backend/.env.local", override=True)

db_url = os.getenv("DATABASE_URL", "postgresql://postgres:password@127.0.0.1:5432/news_scraper")

def check():
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT id, total_found, current_phase, status FROM scrape_jobs WHERE status = 'running' OR status = 'pending'")
    res = cur.fetchall()
    for row in res:
        print(f"Job {row[0]} Status: {row[1:]}")
    conn.close()

if __name__ == "__main__":
    check()
