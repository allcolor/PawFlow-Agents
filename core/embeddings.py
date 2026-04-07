"""Embedding provider with fallback local model.

Supports OpenAI API embeddings and local sentence-transformers.
Zero required dependencies — graceful import with clear errors.
"""

import logging
import math
import threading
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors. Uses numpy if available."""
    if len(a) != len(b):
        raise ValueError(f"Vector dimension mismatch: {len(a)} vs {len(b)}")
    try:
        import numpy as np
        va = np.array(a, dtype=np.float64)
        vb = np.array(b, dtype=np.float64)
        dot = np.dot(va, vb)
        na = np.linalg.norm(va)
        nb = np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(dot / (na * nb))
    except ImportError:
        pass

    # Pure Python fallback
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def top_k_similar(
    query_emb: List[float],
    candidates: List[Tuple[str, List[float]]],
    k: int = 5,
) -> List[Tuple[str, float]]:
    """Return top-K candidates by cosine similarity (descending).

    Args:
        query_emb: Query embedding vector.
        candidates: List of (id, embedding) tuples.
        k: Number of results to return.

    Returns:
        List of (id, similarity_score) tuples, sorted by score descending.
    """
    scored = []
    for cid, emb in candidates:
        try:
            sim = cosine_similarity(query_emb, emb)
            scored.append((cid, sim))
        except (ValueError, ZeroDivisionError):
            continue
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


class EmbeddingProvider:
    """Singleton provider for text embeddings.

    Supports:
    - provider="openai": OpenAI text-embedding-3-small API
    - provider="local": sentence-transformers all-MiniLM-L6-v2
    - provider="auto": OpenAI if api_key set, else local
    """

    _instance: Optional["EmbeddingProvider"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._local_model = None
        self._local_model_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "EmbeddingProvider":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._instance = None

    def embed(
        self,
        texts: List[str],
        provider: str = "auto",
        api_key: str = "",
        base_url: str = "",
        model: str = "",
    ) -> List[List[float]]:
        """Embed a list of texts into vectors.

        Args:
            texts: Texts to embed.
            provider: "openai", "local", or "auto".
            api_key: OpenAI API key (for openai/auto provider).
            base_url: API base URL override.
            model: Model name override.

        Returns:
            List of embedding vectors (one per input text).
        """
        if not texts:
            return []

        if provider == "auto":
            if api_key:
                provider = "openai"
            else:
                provider = "local"

        if provider == "openai":
            return self._embed_openai(texts, api_key, base_url, model)
        elif provider == "local":
            return self._embed_local(texts)
        else:
            raise ValueError(f"Unknown embedding provider: {provider}")

    def _embed_openai(
        self, texts: List[str], api_key: str, base_url: str, model: str,
    ) -> List[List[float]]:
        """Embed via OpenAI /v1/embeddings API."""
        if not api_key:
            raise ValueError("api_key is required for OpenAI embeddings")

        from core.llm_client import LLMClient
        config = {"api_key": api_key}
        if base_url:
            config["base_url"] = base_url
        client = LLMClient(provider="openai", config=config)
        return client.embed(texts, model=model or "text-embedding-3-small")

    def _embed_local(self, texts: List[str]) -> List[List[float]]:
        """Embed via sentence-transformers (local model)."""
        with self._local_model_lock:
            if self._local_model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError:
                    raise ImportError(
                        "sentence-transformers is required for local embeddings. "
                        "Install with: pip install sentence-transformers"
                    )
                self._local_model = SentenceTransformer("all-MiniLM-L6-v2")
                logger.info("Loaded local embedding model: all-MiniLM-L6-v2")

        embeddings = self._local_model.encode(texts, show_progress_bar=False)
        return [emb.tolist() for emb in embeddings]
