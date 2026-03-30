# NEXUS — System Architecture & Developer Documentation

## 1. Overview
NEXUS is a high-performance, distributed news intelligence system designed for large-scale news discovery, extraction, and AI-driven analysis. It is optimized to run on modern multi-core hardware with significant RAM (targeting 16GB to 64GB+ profiles).

## 2. Technical Stack
*   **Backend**: FastAPI (Python 3.11+)
*   **Task Queue**: Celery (using `threads` or `gevent` pools)
*   **Message Broker**: Redis (Dockerized)
*   **Database**: PostgreSQL (Dockerized) with SQLAlchemy (Async + Sync)
*   **Frontend**: React + Vite + Vanilla CSS
*   **Extraction**: Playwright (Headless Chromium) + Trafilatura + Feedparser
*   **AI Engine**: LLM integration via Groq (Llama-3), Grok, or local Ollama

## 3. Core Architecture
NEXUS follows a decoupled, service-oriented architecture:

### A. The Orchestrator (`start.py`)
The orchestrator is the "brain" of the local environment. It:
1.  **Cleans Up Zombies**: Force-terminates stale Python/Celery/Vite processes from previous runs.
2.  **Manages Dependencies**: Ensures Docker providers (Redis/Postgres) are active.
3.  **Applies Hardware Profiles**: Reads `.env.local` to determine if it should run in **16GB** (conservative) or **64GB** (aggressive) mode.
4.  **Parallel Service Launch**: Boots the API, Worker, Beat, and Frontend in parallel with live log streaming.

### B. The Scraper Engine (`scraper/engine.py`)
Handles the heavy lifting of news acquisition:
*   **Discovery**: Multi-threaded RSS scanning (Google News/Bing) with date-range support.
*   **Sitemap Manager**: Burst-mode discovery for rapid ingestion of thousands of articles.
*   **Network Layer**: Managed via `NetworkHandler` with a global rate-limiter and a **20,000+ Residential Proxy Pool** for rotation.

### C. The Worker Pipeline (`scraper/tasks.py`)
Tasks flow through three distinct nodes:
1.  **Node 1: Scrape**: Fetches raw HTML using rotated proxies, resolves redirects, and extracts the primary body text.
2.  **Node 2: Enrich**: Performs AI analysis including 3-bullet summaries, sentiment scoring, and tag extraction.
3.  **Watchdog**: Periodically scans for "stuck" jobs and force-completes them if the counters drift.

## 4. Hardware Optimization Engine
NEXUS dynamically adjusts its intensity based on the `HARDWARE_PROFILE` in `.env.local`:

| Feature | 16GB Profile | 64GB Profile |
| :--- | :--- | :--- |
| **Worker Pool** | `solo` (Sequential) | `threads` (Parallel) |
| **Concurrency** | 1 | 16+ |
| **DB Pool Size** | 20 | 50+ |
| **Discovery Mode** | Throttled RSS | Burst Sitemap + RSS |

## 5. Proxy Rotation System
NEXUS includes a robust `ProxyGuard` class that:
*   Loads proxies from `Webshare residential proxies.txt`.
*   Selects a healthy node for every request.
*   **Dynamic Blacklisting**: If a proxy encounters a `403` or `429`, it is blacklisted for 5 minutes, ensuring high success rates.

## 6. Data Integrity & Deduplication
*   **URL Deduplication**: O(1) checking via Redis sets (`nexus:processed_urls`).
*   **Database Upserts**: PostgreSQL `ON CONFLICT` logic ensures that even if a discovery task finds the same URL twice, it is updated rather than duplicated.
*   **Atomic Progress**: Progress counters use atomic increments in the database to prevent race conditions during high-concurrency 16-thread runs.

## 7. Developer Setup (Clean Boot)
1.  **Clone & Install**: Run `pip install -r backend/requirements.txt` and `npm install` in frontend.
2.  **Configure**: Create `backend/.env.local` and set `HARDWARE_PROFILE=64GB`.
3.  **Boot**: Run `python start.py` from the root.
4.  **Monitor**: 
    *   API: `api.log`
    *   Worker: `worker.log`
    *   Frontend: `frontend.log`

---

## 🛠 Maintenance & Debugging
*   **Resetting DB**: `docker stop nexus_db && docker rm nexus_db` and delete `pgdata` volume.
*   **Clearing Queue**: `redis-cli flushall` (via docker).
*   **Emergency Stop**: Use the `/api/diagnostics/emergency-stop` endpoint or the secret phrase in the console.
