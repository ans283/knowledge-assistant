"""Text → vectors, using a local sentence-transformers model.

Local rather than a hosted API, deliberately:
  * the retrieval test suite runs with no API key and no network
  * deterministic — the same text always produces the same vector, so
    eval numbers are reproducible
  * free, which matters when re-embedding the corpus on every chunker change

bge-base-en-v1.5 produces 768-dimensional vectors and expects an instruction
prefix on queries but not on documents. That asymmetry is not optional: it is
how the model was trained, and skipping it measurably degrades retrieval.
"""
from __future__ import annotations

import threading

import numpy as np

from app.config import settings

# The prefix bge models expect on the QUERY side only. Documents are embedded bare.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

_model = None
_lock = threading.Lock()


def get_model():
    """Load once per process. The model is ~400MB in memory and several
    seconds to construct, so it must not be rebuilt per request.

    Double-checked locking: FastAPI serves requests from a thread pool, and two
    threads hitting a cold cache simultaneously would otherwise load it twice.
    """
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(settings.embedding_model)
    return _model


def embed_documents(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Embed chunk text for storage. No instruction prefix.

    normalize_embeddings=True gives unit-length vectors. Combined with the
    collection's cosine space, this makes distances bounded and comparable:
    0.0 identical, 1.0 unrelated. The Step 9 abstention threshold depends on
    that scale being meaningful.
    """
    if not texts:
        return []
    vectors = get_model().encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return vectors.tolist()


def embed_query(text: str) -> list[float]:
    """Embed a user question. Instruction-prefixed, per the bge training setup."""
    vector = get_model().encode(
        QUERY_INSTRUCTION + text,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return vector.tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Convenience for tests and diagnostics. Assumes unit-length inputs."""
    return float(np.dot(np.asarray(a), np.asarray(b)))


def dimension() -> int:
    return get_model().get_embedding_dimension()