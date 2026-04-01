import redis, psycopg2, os
from dotenv import load_dotenv

def stop_job(job_id):
    # Load env
    load_dotenv("backend/.env")
    load_dotenv("backend/.env.local", override=True)
    
    # Redis
    r = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
    r.sadd("nexus:cancelled_jobs", job_id)
    
    # DB
    db_url = os.getenv("DATABASE_URL").replace("postgresql+asyncpg://", "postgresql://")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("UPDATE scrape_jobs SET status='interrupted' WHERE id=%s", (job_id,))
    conn.commit()
    conn.close()
    print(f"Job {job_id} successfully marked as INTERRUPTED.")

if __name__ == "__main__":
    stop_job("9f2db16d-46de-4eb5-b56e-bc25d054cf7b")
