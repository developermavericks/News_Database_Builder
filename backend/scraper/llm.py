import os
import random
import asyncio
import httpx
import json
import time
from typing import Optional, List, Dict, Any
import ollama

# --- Configuration ---
# Support GROQ_API_KEY or XAI_API_KEY (legacy name) for Groq credentials
_groq_raw = os.getenv("GROQ_API_KEY") or os.getenv("XAI_API_KEY") or ""
GROQ_API_KEYS = [k.strip() for k in _groq_raw.split(",") if k.strip()]
XAI_API_KEYS = [k.strip() for k in os.getenv("XAI_API_KEY", "").split(",") if k.strip()]
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3:8b")  # Hardware-optimized default
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

# --- Redis for Global Throttling (C-7) ---
import redis.asyncio as redis
_redis_client = None

async def get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
    return _redis_client

# Synchronous Redis for gevent workers
import redis as redis_sync
_redis_sync_client = None

def get_redis_sync():
    global _redis_sync_client
    if _redis_sync_client is None:
        _redis_sync_client = redis_sync.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
    return _redis_sync_client

_ollama_semaphore = None
def get_ollama_semaphore():
    global _ollama_semaphore
    if _ollama_semaphore is None:
        # Optimized for USER hardware (RTX 3060 12GB + 64GB RAM)
        _ollama_semaphore = asyncio.Semaphore(4) 
    return _ollama_semaphore

def log(msg: str):
    from scraper.engine import logger
    logger.info(msg)

# --- Grok (xAI) Client ---
def summarize_with_grok_sync(text: str) -> Optional[str]:
    """Summarizes article using xAI Grok API."""
    is_placeholder = any("your_xai_api_key" in k.lower() for k in XAI_API_KEYS)
    if not XAI_API_KEYS or is_placeholder or not text or len(text) < 100: 
        return None
    
    url = "https://api.x.ai/v1/chat/completions"
    payload = {
        "model": "grok-3",
        "messages": [
            {"role": "system", "content": "You are a news analyst. Summarize this article into EXACTLY 3 bullet points. Output only the bullet points."},
            {"role": "user", "content": text[:5000]},
        ],
        "temperature": 0,
    }

    with httpx.Client(timeout=30) as client:
        for attempt in range(2):
            api_key = random.choice(XAI_API_KEYS)
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            try:
                resp = client.post(url, headers=headers, json=payload, timeout=20)
                if resp.status_code == 200: 
                    return resp.json()["choices"][0]["message"]["content"]
                else: 
                    log(f"Grok API Error ({resp.status_code}): {resp.text}")
                    if resp.status_code == 429: time.sleep(2)
                    else: break
            except Exception as e:
                log(f"Grok Connection Error: {e}")
                time.sleep(1)
    return None

# Unified Summarization: Prioritizes Local GPU (Ollama) -> Groq -> Grok
def summarize_with_groq_sync(text: str) -> Optional[str]:
    if not text or len(text) < 100: return None
    
    # 1. Primary: Local GPU with Ollama (Free, Private, Fast on RTX 3060)
    try:
        import requests
        model = OLLAMA_MODEL
        if ":" not in model: model = f"{model}:latest"
        prompt = f"You are a news analyst. Summarize this article into EXACTLY 3 bullet points. Output only the bullet points.\n\nArticle: {text[:5000]}"
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=45,
        )
        if r.status_code == 200:
            return r.json().get("response", "").strip()
    except Exception as e:
        log(f"Ollama summarization failed, trying fallback: {e}")

    # 2. Fallback: Groq (llama-3.3-70b-versatile)
    is_placeholder = any("your_groq_api_key" in k.lower() for k in GROQ_API_KEYS)
    if GROQ_API_KEYS and not is_placeholder:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": "You are a news analyst. Summarize this article into EXACTLY 3 bullet points. Output only the bullet points."},
                {"role": "user", "content": text[:4000]},
            ],
            "max_tokens": 150,
        }

        with httpx.Client(timeout=30) as client:
            for attempt in range(2):
                api_key = random.choice(GROQ_API_KEYS)
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                try:
                    resp = client.post(url, headers=headers, json=payload, timeout=15)
                    if resp.status_code == 200:
                        return resp.json()["choices"][0]["message"]["content"]
                    elif resp.status_code == 429:
                        time.sleep(1)
                    else:
                        break
                except:
                    pass

    # 3. Final fallback: Grok (xAI)
    return summarize_with_grok_sync(text)

# --- Ollama Client ---
from urllib.parse import urlparse

def get_domain_name(url: str) -> str:
    """Extract a clean domain name from a URL."""
    try:
        domain = urlparse(url).netloc
        if domain.startswith("www."):
            domain = domain[4:]
        # Remove TLD for a cleaner 'Agency' name if needed, or keep it.
        # Let's keep it but capitalized for common ones.
        parts = domain.split('.')
        if len(parts) > 1:
            return parts[-2].capitalize()
        return domain.capitalize()
    except:
        return ""

from concurrent.futures import ThreadPoolExecutor
from config import CURRENT_PROFILE

# Non-blocking executor for LLM calls (RTX 3060 is single-instance per GPU usually)
_llm_executor = ThreadPoolExecutor(max_workers=CURRENT_PROFILE["OLLAMA_MAX_WORKERS"])

def _call_ollama_blocking(prompt: str) -> str:
    """Synchronous blocking call to Ollama API."""
    import requests
    try:
        # Use provided model or fallback
        model = OLLAMA_MODEL
        if ":" not in model: model = f"{model}:latest"
        
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["response"]
    except Exception as e:
        raise e

async def extract_metadata_with_ollama(body: str, url: str = "", context_agency: str = "", author_metadata: Dict = None, html_snippets: Dict = None) -> Dict[str, Any]:
    """
    Non-blocking wrapper for Ollama metadata extraction.
    """
    domain = get_domain_name(url) if url else ""
    
    prompt = (
        f"Analyze this news article and extract high-precision metadata in JSON format.\n\n"
        f"URL CONTEXT: {url}\n"
        f"ESTIMATED AGENCY (from URL): {domain}\n\n"
        f"STAGED EVIDENCE:\n"
        f"1. HTML Metadata Suggestion: {author_metadata.get('name') if author_metadata else 'None'}\n"
        f"2. Suggested Handle: {author_metadata.get('handle') if author_metadata else 'None'}\n"
        f"3. HTML HEAD SNIPPET: {html_snippets.get('head') if html_snippets else 'None'}\n"
        f"4. BYLINE AREA SNIPPET: {html_snippets.get('top') if html_snippets else 'None'}\n\n"
        f"TASK:\n"
        f"- Find the 'author'. If multiple, use the main reporter.\n"
        f"- Find the 'agency' (news organization). Verify if it matches the domain {domain}.\n"
        f"- Set 'is_junk' to true if the content is a cookie wall, paywall, or 'Access Denied'.\n"
        f"- PROVIDE 'cleaned_body' by removing ads, menus, and footer junk.\n\n"
        f"TEXT FOR ANALYSIS:\n{body[:6000]}"
    )
    
    loop = asyncio.get_running_loop()
    try:
        content = await asyncio.wait_for(
            loop.run_in_executor(_llm_executor, _call_ollama_blocking, prompt),
            timeout=65
        )
        data = json.loads(content)
        
        res_agency = data.get("agency")
        if not res_agency or res_agency.lower() in ["google", "google news"]:
             res_agency = context_agency or domain
             
        return {
            "author": data.get("author") or (author_metadata or {}).get("name"), 
            "handle": data.get("handle") or (author_metadata or {}).get("handle"),
            "agency": res_agency, 
            "is_junk": data.get("is_junk", False), 
            "cleaned_body": data.get("cleaned_body", body)
        }
    except Exception as e:
        log(f"[llm] extraction skipped/failed: {e}")
        return {"author": (author_metadata or {}).get("name"), "agency": context_agency or domain, "body": body}

def perform_full_enrichment_sync(body: str, title: str, url: str, sector: str, context_agency: str = "", extra_metadata: Dict = None) -> Dict[str, Any]:
    from config import run_async
    results = {"summary": None, "author": None, "agency": None, "tags": [sector], "sentiment": "neutral"}
    if not body or len(body) < 100: return results
    
    extra_metadata = extra_metadata or {}
    author_metadata = extra_metadata.get("author_metadata")
    html_snippets = extra_metadata.get("html_snippets")
    
    # 1. Primary Extraction with Hardware-Tuned Ollama (Bridged)
    try:
        meta = run_async(extract_metadata_with_ollama(
            body, 
            url=url, 
            context_agency=context_agency, 
            author_metadata=author_metadata,
            html_snippets=html_snippets
        ))
        
        results["author"] = meta.get("author")
        if meta.get("handle"):
            results["author"] = f"{results['author']} (@{meta['handle']})" if results["author"] else f"@{meta['handle']}"
        
        results["agency"] = meta.get("agency")
        
        final_body = meta.get("cleaned_body", body)
        results["summary"] = summarize_with_groq_sync(final_body) or final_body[:1000]
    except Exception as e:
        log(f"[llm] Enrichment bridge fail: {e}")
    
    return results
