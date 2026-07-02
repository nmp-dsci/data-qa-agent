"""Local embeddings for agent memory (recall/remember) — no API key required.

BAAI/bge-small-en-v1.5 is 384-dim, matching app.user_memories.embedding
vector(384). Runs fully offline via onnxruntime (no torch). The model is
pre-fetched at Docker build time (see Dockerfile) so it's cached in the image.
"""

from __future__ import annotations

from functools import lru_cache

from .config import settings


@lru_cache(maxsize=1)
def _model() -> object:
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=settings.embedding_model)


def embed_text(text: str) -> list[float]:
    """Synchronous, CPU-bound — call via asyncio.to_thread from async code."""
    vector = next(iter(_model().embed([text])))  # type: ignore[attr-defined]
    return vector.tolist()  # type: ignore[no-any-return]
