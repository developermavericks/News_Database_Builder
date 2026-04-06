import subprocess
import sys
import os
import time
import socket
import logging

# Basic logging for the orchestrator
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("ORCHESTRATOR")

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def check_redis():
    """Check if Redis is running on default port 6379."""
    try:
        with socket.create_connection(("localhost", 6379), timeout=1):
            return True
    except:
        return False

def check_postgres():
    """Check if PostgreSQL is running on default port 5432."""
    try:
        with socket.create_connection(("localhost", 5432), timeout=1):
            return True
    except:
        return False

def start_redis_docker():
    """Attempt to start Redis via Docker if not running."""
    logger.info("Redis not detected. Attempting to start via Docker...")
    try:
        # Try to start existing container
        subprocess.run(["docker", "start", "nexus-redis-local"], 
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        if check_redis(): return True
        
        # Or create new
        subprocess.run(["docker", "run", "-d", "--name", "nexus-redis-local", "-p", "6379:6379", "redis:7-alpine"], 
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        return check_redis()
    except:
        return False

def start_postgres_docker():
    """Attempt to start Postgres via Docker if not running."""
    logger.info("PostgreSQL not detected. Attempting to start via Docker...")
    try:
        # Try to start existing
        subprocess.run(["docker", "start", "nexus_db"], 
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        if check_postgres(): return True
        
        # Or create new
        subprocess.run(["docker", "run", "-d", "--name", "nexus_db", 
                        "-e", "POSTGRES_USER=postgres", 
                        "-e", "POSTGRES_PASSWORD=password", 
                        "-e", "POSTGRES_DB=news_scraper", 
                        "-p", "5432:5432", "postgres:16-alpine"], 
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(5)
        return check_postgres()
    except Exception as e:
        logger.error(f"Failed to start Postgres: {e}")
        return False

def cleanup_zombies():
    """Kill any orphaned python/celery processes associated with this project."""
    logger.info("Cleaning up existing processes...")
    if os.name == 'nt':
        # On Windows, we use wmic to be selective and terminate processes that are part of the project.
        try:
            # Kill processes that have 'celery' or 'run_backend' or 'vite' in the command line
            subprocess.run(['wmic', 'process', 'where', "commandline like '%celery%' or commandline like '%run_backend%' or commandline like '%vite%'", 'call', 'terminate'], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)
        except Exception as e:
            logger.warning(f"Cleanup routine warning: {e}")
    return True

def main():
    print("\n" + "="*50)
    print(" 🛡️  NEXUS - Global News Intelligence Orchestrator")
    print(" " + "="*50 + "\n")

    cleanup_zombies()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.join(base_dir, "backend")
    frontend_dir = os.path.join(base_dir, "frontend")
    
    python_exe = os.path.join(backend_dir, "venv", "Scripts", "python.exe")
    npx_exe = "npx.cmd" if os.name == 'nt' else "npx"

    if not os.path.exists(python_exe):
        python_exe = "python"
        logger.warning("Virtual environment not found, using global python.")

    # Load .env variables
    env = os.environ.copy()
    def load_env_file(path):
        if os.path.exists(path):
            with open(path, "r") as f:
                for line in f:
                    if "=" in line and not line.startswith("#"):
                        # Split by first "=" and take key, value
                        parts = line.strip().split("=", 1)
                        if len(parts) == 2:
                            k, v = parts
                            env[k.strip()] = v.strip()

    load_env_file(os.path.join(backend_dir, ".env"))
    load_env_file(os.path.join(backend_dir, ".env.local"))

    # 1. Check Infrastructure Dependencies
    if not check_redis():
        if not start_redis_docker():
            logger.error("Redis is required but not running. Please start Redis or Docker Desktop.")
            sys.exit(1)
    logger.info("✅ Redis Connection Verified.")

    if not check_postgres():
        if not start_postgres_docker():
            logger.error("PostgreSQL is required but not running. Please start PostgreSQL or Docker Desktop.")
            sys.exit(1)
    logger.info("✅ PostgreSQL Connection Verified.")

    # 2. Database Schema Check/Init
    logger.info("Syncing Database Schema...")
    try:
        subprocess.run([python_exe, "run_init_db.py"], cwd=base_dir, env=env, check=True)
        logger.info("✅ Schema Synced.")
    except Exception as e:
        logger.warning(f"Note: run_init_db.py failed ({e}). Database might already be initialized.")

    # 3. Port Cleanup
    for port in [8000, 5173]:
        if is_port_in_use(port):
            logger.warning(f"Port {port} is already in use. Service might fail to bind.")

    processes = []

    def start_service(name, cmd, cwd, log_file):
        logger.info(f"Starting {name}...")
        f = open(os.path.join(base_dir, log_file), "a", encoding="utf-8")
        # Added extra flags for better Windows process management
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT, 
            shell=(os.name == 'nt' and not (cmd[0].endswith('.exe') or 'python' in cmd[0])),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        )
        processes.append((name, proc, f))
        return proc

    try:
        # Start API
        start_service("Backend API", [python_exe, "run_backend.py"], backend_dir, "api.log")
        
        # Hardware-specific concurrency
        hw_profile = env.get("HARDWARE_PROFILE", "16GB")
        pool_type = env.get("CELERY_POOL", "threads" if hw_profile == "64GB" else "solo")
        concurrency = env.get("CELERY_WORKER_CONCURRENCY", "16" if hw_profile == "64GB" else "10")
        
        logger.info(f"Using {hw_profile} profile (Pool={pool_type}, Concurrency={concurrency})")
        
        worker_cmd = [
            python_exe, "-m", "celery", "-A", "celery_app", "worker", 
            "--loglevel=info", "-P", pool_type,
            "--prefetch-multiplier=1",
            "-Q", "celery,scrape,enrich"
        ]
        
        if pool_type != "solo":
            worker_cmd.extend(["--concurrency", str(concurrency)])
            
        start_service("Celery Worker", worker_cmd, backend_dir, "worker.log")
        
        # Start Beat (Scheduler)
        start_service("Celery Beat", [python_exe, "-m", "celery", "-A", "celery_app", "beat", "--loglevel=info"], backend_dir, "beat.log")
        
        # Start Frontend
        start_service("Frontend (Vite)", [npx_exe, "vite", "--port", "5173", "--host"], frontend_dir, "frontend.log")

        print("\n" + "🚀 All services initialized!".center(50))
        print("-" * 50)
        print(f" ➜ Dashboard:  http://localhost:5173")
        print(f" ➜ API Docs:   http://localhost:8000/docs")
        print("-" * 50)
        print(" Logs: api.log | worker.log | beat.log | frontend.log")
        print(" Press Ctrl+C to shutdown all services.\n")

        while True:
            time.sleep(5)
            for name, proc, _ in processes:
                if proc.poll() is not None:
                    logger.error(f"Critical service '{name}' has stopped (Exit code: {proc.returncode}).")
                    raise KeyboardInterrupt

    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    finally:
        for name, proc, f in processes:
            logger.info(f"Stopping {name}...")
            if os.name == 'nt':
                # Use taskkill to ensure the entire tree is dead (Windows specialty)
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                proc.terminate()
            f.close()
        logger.info("Full system shutdown complete.")

if __name__ == "__main__":
    main()
