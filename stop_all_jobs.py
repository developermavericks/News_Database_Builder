import redis, psycopg2, os
from dotenv import load_dotenv

def mass_stop_all():
    load_dotenv("backend/.env")
    load_dotenv("backend/.env.local", override=True)
    
    r = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
    
    db_url = os.getenv("DATABASE_URL").replace("postgresql+asyncpg://", "postgresql://")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    # Find all running/pending jobs
    cur.execute("SELECT id FROM scrape_jobs WHERE status IN ('running', 'pending')")
    job_ids = [row[0] for row in cur.fetchall()]
    
    for jid in job_ids:
        r.sadd("nexus:cancelled_jobs", jid)
        cur.execute("UPDATE scrape_jobs SET status='interrupted' WHERE id=%s", (jid,))
        print(f"Halted Job: {jid}")
        
    # Set a global stop flag for safety (expires in 5 minutes)
    r.set("nexus:global_stop", "1", ex=300)
    
    conn.commit()
    conn.close()
    print(f"Total jobs halted: {len(job_ids)}.")

if __name__ == "__main__":
    mass_stop_all()
