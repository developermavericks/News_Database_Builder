"""
NEXUS Semantic Search Engine
GPU-accelerated article embeddings using sentence-transformers + ChromaDB.
Optimized for RTX 3060 12GB VRAM — runs entirely on-device, no API needed.
"""

import os
import logging
import hashlib
import threading
from typing import Optional, List, Dict, Any

logger = logging.getLogger("SEMANTIC")

# --- Configuration ---
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")  # 90MB, ~1000 articles/sec on RTX 3060
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")
CHROMA_COLLECTION = "nexus_articles"
SEMANTIC_ENABLED = os.getenv("SEMANTIC_SEARCH_ENABLED", "true").lower() == "true"

_embedder = None
_chroma_client = None
_collection = None
_semantic_lock = threading.Lock()
_chroma_lock = threading.Lock()


def _get_embedder():
    """Lazy-load the embedding model onto GPU with thread-safety."""
    global _embedder
    if _embedder is None:
        with _semantic_lock:
            if _embedder is not None: return _embedder # Double-check
            try:
                from sentence_transformers import SentenceTransformer
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
                logger.info(f"NEXUS: Initializing AI Embedder on {device.upper()}...")
                _embedder = SentenceTransformer(EMBEDDING_MODEL, device=device)
            except Exception as e:
                logger.error(f"Failed to load embedding model: {e}")
                return None
    return _embedder


def _get_collection():
    """Lazy-load ChromaDB collection with thread-safety."""
    global _chroma_client, _collection
    if _collection is None:
        with _chroma_lock:
            if _collection is not None: return _collection
            try:
                import chromadb
                _chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
                _collection = _chroma_client.get_or_create_collection(
                    name=CHROMA_COLLECTION,
                    metadata={"hnsw:space": "cosine"}
                )
                logger.debug(f"ChromaDB collection '{CHROMA_COLLECTION}' ready.")
            except Exception as e:
                logger.error(f"Failed to init ChromaDB: {e}")
                return None
    return _collection


def embed_article(article_id: int, title: str, body: str, sector: str, user_id: str) -> bool:
    """
    Embed a single article and store in ChromaDB.
    Called after scraping completes for each article.
    """
    if not SEMANTIC_ENABLED: return False

    embedder = _get_embedder()
    collection = _get_collection()
    if not embedder or not collection:
        return False

    try:
        # Combine title + first 512 tokens of body for embedding
        text = f"{title}. {(body or '')[:2000]}"
        embedding = embedder.encode(text, normalize_embeddings=True).tolist()

        doc_id = f"art_{article_id}"
        collection.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            metadatas=[{
                "article_id": article_id,
                "title": title[:500],
                "sector": sector,
                "user_id": user_id,
            }],
            documents=[text[:1000]]
        )
        return True
    except Exception as e:
        logger.error(f"Failed to embed article {article_id}: {e}")
        return False


def semantic_search(
    query: str,
    user_id: str,
    sector: Optional[str] = None,
    n_results: int = 20,
) -> List[Dict[str, Any]]:
    """
    Find semantically similar articles using vector search.
    Much more powerful than keyword search — finds contextually related articles
    even when exact keywords don't match.
    """
    if not SEMANTIC_ENABLED: return []

    embedder = _get_embedder()
    collection = _get_collection()
    if not embedder or not collection:
        return []

    try:
        query_embedding = embedder.encode(query, normalize_embeddings=True).tolist()

        # Build filter: always scope to user, optionally filter sector
        where_filter = {"user_id": user_id}
        if sector:
            where_filter = {"$and": [{"user_id": user_id}, {"sector": sector}]}

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(n_results, collection.count() or 1),
            where=where_filter,
            include=["metadatas", "distances", "documents"]
        )

        hits = []
        if results and results.get("ids") and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i]
                distance = results["distances"][0][i]
                hits.append({
                    "article_id": meta.get("article_id"),
                    "title": meta.get("title"),
                    "sector": meta.get("sector"),
                    "similarity_score": round(1 - distance, 4),  # Convert cosine distance to similarity
                    "snippet": results["documents"][0][i][:200] if results.get("documents") else "",
                })

        return hits
    except Exception as e:
        logger.error(f"Semantic search failed: {e}")
        return []


def bulk_embed_existing(user_id: Optional[str] = None, batch_size: int = 500) -> Dict[str, int]:
    """
    Backfill semantic embeddings for all existing articles in the database.
    Run once after enabling semantic search to index historical data.
    """
    if not SEMANTIC_ENABLED:
        return {"embedded": 0, "skipped": 0, "error": "Semantic search disabled"}

    embedder = _get_embedder()
    collection = _get_collection()
    if not embedder or not collection:
        return {"embedded": 0, "skipped": 0, "error": "Embedder or ChromaDB unavailable"}

    try:
        from db.database import get_db_sync, Article
        from sqlalchemy import select

        embedded = 0
        skipped = 0

        with get_db_sync() as db:
            stmt = select(Article.id, Article.title, Article.full_body, Article.sector, Article.user_id)
            if user_id:
                stmt = stmt.where(Article.user_id == user_id)
            stmt = stmt.where(Article.full_body.isnot(None))

            rows = db.execute(stmt).all()
            logger.info(f"Bulk embedding {len(rows)} articles...")

            # Process in batches for GPU efficiency
            texts, ids, metas, docs = [], [], [], []
            for row in rows:
                art_id, title, body, sector, uid = row
                doc_id = f"art_{art_id}"

                # Skip already embedded
                existing = collection.get(ids=[doc_id])
                if existing and existing.get("ids"):
                    skipped += 1
                    continue

                text = f"{title}. {(body or '')[:2000]}"
                texts.append(text)
                ids.append(doc_id)
                metas.append({"article_id": art_id, "title": (title or "")[:500], "sector": sector or "", "user_id": uid or ""})
                docs.append(text[:1000])

                if len(texts) >= batch_size:
                    embeddings = embedder.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False).tolist()
                    collection.upsert(ids=ids, embeddings=embeddings, metadatas=metas, documents=docs)
                    embedded += len(texts)
                    logger.info(f"Embedded {embedded}/{len(rows)} articles...")
                    texts, ids, metas, docs = [], [], [], []

            # Final batch
            if texts:
                embeddings = embedder.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False).tolist()
                collection.upsert(ids=ids, embeddings=embeddings, metadatas=metas, documents=docs)
                embedded += len(texts)

        logger.info(f"Bulk embedding complete. Embedded: {embedded}, Skipped (already done): {skipped}")
        return {"embedded": embedded, "skipped": skipped}

    except Exception as e:
        logger.error(f"Bulk embedding failed: {e}")
        return {"embedded": 0, "skipped": 0, "error": str(e)}
