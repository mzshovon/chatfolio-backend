from typing import Any

from chromadb.utils import embedding_functions

# DeepSeek (our only configured LLM provider) has no embeddings API, and no other provider is
# configured yet. Chroma ships a local ONNX model (all-MiniLM-L6-v2) precisely for this case —
# no API key, no network call, no per-embedding cost. Swap this for a hosted provider later by
# changing only this module; everything downstream consumes plain list[list[float]] vectors.
_embedder: Any = embedding_functions.DefaultEmbeddingFunction()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Synchronous, CPU-bound (ONNX inference) — call via asyncio.to_thread from async code."""
    result: list[list[float]] = _embedder(texts)
    return result
