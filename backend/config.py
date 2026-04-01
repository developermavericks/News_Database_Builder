import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
env_local = os.path.join(os.path.dirname(__file__), ".env.local")
if os.path.exists(env_local):
    load_dotenv(env_local, override=True)

# --- Hardware Profiles ---
# Set HARDWARE_PROFILE to "16GB" or "64GB" in your .env.local
HARDWARE_PROFILE = os.getenv("HARDWARE_PROFILE", "16GB")

PROFILES = {
    "16GB": {
        "CELERY_CONCURRENCY": 20,
        "DB_POOL_SIZE": 10,
        "DB_MAX_OVERFLOW": 15,
        "BROWSER_POOL_SIZE": 3,
        "OLLAMA_MAX_WORKERS": 1,
        "TASK_TIME_LIMIT": 180,        # 3 minutes
        "TASK_SOFT_TIME_LIMIT": 150,   # 2.5 minutes
        "WORKER_MAX_MEMORY": 400_000,  # 400MB (in KB for Celery)
    },
    "64GB": {
        "CELERY_CONCURRENCY": 32,
        "DB_POOL_SIZE": 20,
        "DB_MAX_OVERFLOW": 30,
        "BROWSER_POOL_SIZE": 10,
        "OLLAMA_MAX_WORKERS": 4,
        "TASK_TIME_LIMIT": 1800,        # 30 minutes
        "TASK_SOFT_TIME_LIMIT": 1500,   # 25 minutes
        "WORKER_MAX_MEMORY": 1_000_000, # 1GB
    }
}

# Fallback to 16GB if unknown profile
CURRENT_PROFILE = PROFILES.get(HARDWARE_PROFILE, PROFILES["16GB"])

import threading
import asyncio

# --- Sync-to-Async Bridge ---
# Allows gevent/sync tasks to call persistent async components (BrowserPool)
_loop = None
_loop_thread = None

def _get_background_loop():
    global _loop, _loop_thread
    if _loop is None:
        _loop = asyncio.new_event_loop()
        def _start_async_loop(loop):
            asyncio.set_event_loop(loop)
            loop.run_forever()
        # Lazily spawn the thread to prevent Celery Gevent/Forking deadlocks on Windows
        _loop_thread = threading.Thread(target=_start_async_loop, args=(_loop,), daemon=True)
        _loop_thread.start()
    return _loop

def run_async(coro):
    """Bridge: Run an async coroutine on the background thread and wait for result."""
    loop = _get_background_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()

print(f"NEXUS: Loaded Hardware Profile: {HARDWARE_PROFILE}")
