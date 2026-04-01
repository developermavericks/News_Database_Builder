import redis, psycopg2, os
from dotenv import load_dotenv

def mass_stop(exclude_id):
    load_dotenv("backend/.env")
    load_dotenv("backend/.env.local", override=True)
    
    r = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
    
    db_url = os.getenv("DATABASE_URL").replace("postgresql+asyncpg://", "postgresql://")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    # Find all running/pending jobs
    cur.execute("SELECT id FROM scrape_jobs WHERE status IN ('running', 'pending')")
    job_ids = [row[0] for row in cur.fetchall()]
    
    to_kill = [jid for jid in job_ids if not jid.startswith(exclude_id)]
    
    for jid in to_kill:
        r.sadd("nexus:cancelled_jobs", jid)
        cur.execute("UPDATE scrape_jobs SET status='interrupted' WHERE id=%s", (jid,))
        print(f"Halted Job: {jid}")
        
    conn.commit()
    conn.close()
    print(f"Total jobs halted: {len(to_kill)}. Keeping {exclude_id} alive.")

if __name__ == "__main__":
    mass_stop("f6106e2d")
