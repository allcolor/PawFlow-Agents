"""Embedding provider with fallback local model.

Supports OpenAI API embeddings and local sentence-transformers.
Zero required dependencies — graceful import with clear errors.
"""

import logging
import math
import threading
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _resolve_embedding_llm_service(user_id: str = "",
                                   conversation_id: str = ""):
    """Resolve the optional embedding_llm_service parameter."""
    try:
        from core.expression import resolve_value
        svc_id = resolve_value(
            "${embedding_llm_service}", owner=user_id,
            conversation_id=conversation_id) or ""
    except Exception:
        svc_id = ""
    if not svc_id or str(svc_id).startswith("${"):
        return None, ""
    try:
        from core.service_registry import ServiceRegistry
        svc = ServiceRegistry.get_instance().resolve(
            str(svc_id), user_id=user_id, conv_id=conversation_id)
    except Exception:
        logger.debug("embedding_llm_service resolution failed", exc_info=True)
        return None, str(svc_id)
    if svc and hasattr(svc, "embed"):
        return svc, str(svc_id)
    logger.warning(
        "embedding_llm_service '%s' is not an embedding-capable LLM service; "
        "falling back to local embeddings", svc_id)
    return None, str(svc_id)


def build_memory_embed_fn(user_id: str = "", conversation_id: str = ""):
    """Build the memory embedding function.

    If `${embedding_llm_service}` resolves to an LLM service exposing the
    OpenAI-compatible embeddings endpoint, use it first. Otherwise, keep the
    existing local MiniLM fallback as best-effort.
    """
    svc, svc_id = _resolve_embedding_llm_service(user_id, conversation_id)

    def _embed(text: str) -> List[float]:
        if svc is not None:
            try:
                vecs = svc.embed([text])
                if vecs and vecs[0]:
                    return vecs[0]
            except Exception:
                logger.debug(
                    "embedding_llm_service '%s' embed failed; falling back "
                    "to local embeddings", svc_id, exc_info=True)
        vecs = EmbeddingProvider.instance().embed([text], provider="local")
        return vecs[0] if vecs else []

    return _embed


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
