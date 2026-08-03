"""Cross-encoder reranking.

A bi-encoder (the embedding model) encodes query and chunk independently, so
it never sees them together — which is what makes it fast enough to index the
whole corpus, and what limits its precision.

A cross-encoder takes [query, chunk] as a single input and runs attention
across both, so it can judge whether this specific chunk answers this specific
question. Too slow to score 180 chunks per query, so it only ever sees the
top ~20 candidates from the cheap retrieval stage.
"""
from __future__ import annotations

import threading

_model = None
_lock = threading.Lock()

RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


def get_reranker():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from sentence_transformers import CrossEncoder
                _model = CrossEncoder(RERANK_MODEL)
    return _model


def rerank(query: str, texts: list[str]) -> list[float]:
    """Relevance scores, higher is better. Raw logits — unbounded, not 0..1.

    ms-marco models emit a logit rather than a probability, so scores are
    comparable within one query but not across queries. The Step 9 abstention
    threshold is calibrated against this scale.
    """
    if not texts:
        return []
    scores = get_reranker().predict(
        [(query, t) for t in texts], show_progress_bar=False
    )
    return [float(s) for s in scores]