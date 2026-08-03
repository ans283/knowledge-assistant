"""Four retrieval modes behind one interface.

    dense          — embedding similarity; matches meaning, misses exact tokens
    sparse         — BM25; matches exact tokens, misses paraphrase
    hybrid         — RRF fusion of both
    hybrid_rerank  — hybrid, then cross-encoder over the candidates

Fusion operates on RANKS, not scores. Cosine distance is 0..1 lower-is-better;
BM25 is unbounded higher-is-better. Reconciling those scales is corpus-
dependent and fragile; ranks make the problem disappear.
"""
from __future__ import annotations

import re
import threading
import time

from app.config import settings
from app.core.embeddings import embed_query
from app.core.rerank import rerank
from app.core.schemas import ChunkMeta, RetrievalMode, RetrievedChunk, Trace
from app.core.store import get_collection

_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, keeping internal hyphens.

    'PRV-06' must survive as one token: splitting it into 'prv' and '06'
    destroys exactly the rare-term signal BM25 exists to exploit.
    """
    return _TOKEN.findall(text.lower())


# ─────────────────────────── BM25 index ──────────────────────────────────

class BM25Index:
    """In-memory sparse index over all chunks.

    Rebuilt from Chroma rather than persisted: 180 chunks build in
    milliseconds, and a second persistence layer is one more thing to keep
    consistent with the vector store. This is a corpus-size assumption worth
    stating in ASSUMPTIONS.md — beyond ~100k chunks it would need Postgres
    full-text or a dedicated index.
    """

    def __init__(self) -> None:
        self._bm25 = None
        self._chunk_ids: list[str] = []
        self._lock = threading.Lock()

    def build(self, collection=None) -> int:
        from rank_bm25 import BM25Okapi

        col = collection if collection is not None else get_collection()
        stored = col.get(include=["documents", "metadatas"])

        ids = stored.get("ids", [])
        documents = stored.get("documents") or []
        metadatas = stored.get("metadatas") or []

        with self._lock:
            if not ids:
                self._bm25, self._chunk_ids = None, []
                return 0

            # index section_path alongside body text, mirroring embed_text:
            # both retrieval arms should see the same disambiguating context
            corpus = [
                tokenize(f"{m.get('section_path', '')} {d}")
                for d, m in zip(documents, metadatas)
            ]
            self._bm25 = BM25Okapi(corpus)
            self._chunk_ids = list(ids)
            return len(ids)

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        with self._lock:
            if self._bm25 is None:
                return []
            scores = self._bm25.get_scores(tokenize(query))
            ranked = sorted(zip(self._chunk_ids, scores), key=lambda t: -t[1])
        return [(cid, float(s)) for cid, s in ranked[:top_k] if s > 0]

    @property
    def size(self) -> int:
        return len(self._chunk_ids)


_bm25_index = BM25Index()


def get_bm25_index() -> BM25Index:
    return _bm25_index


def ensure_bm25(collection=None) -> int:
    """Build the sparse index if empty. Called at startup and after ingest."""
    if _bm25_index.size == 0:
        return _bm25_index.build(collection)
    return _bm25_index.size


# ─────────────────────────── fusion ──────────────────────────────────────

def reciprocal_rank_fusion(
    rank_lists: list[list[str]], k: int = 60
) -> list[tuple[str, float]]:
    """Merge ranked lists by reciprocal rank: score = sum of 1/(k + rank).

    k=60 is the value from the original RRF paper. It damps the gap between
    top positions: without it, rank 1 would score 1.0 and rank 2 only 0.5,
    letting one arm's top hit dominate. With k=60 those become 0.0167 and
    0.0164 — close enough that agreement between arms outweighs any single
    arm's confidence, which is the entire point of fusing.
    """
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for position, chunk_id in enumerate(ranks):
            rank = position + 1                      # RRF ranks are 1-based
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda t: -t[1])


# ─────────────────────────── retrieval ───────────────────────────────────

def _hydrate(chunk_ids: list[str], collection=None) -> dict[str, RetrievedChunk]:
    if not chunk_ids:
        return {}
    col = collection if collection is not None else get_collection()
    stored = col.get(ids=chunk_ids, include=["documents", "metadatas"])
    return {
        cid: RetrievedChunk(
            chunk_id=cid, text=doc, meta=ChunkMeta.from_chroma(meta)
        )
        for cid, doc, meta in zip(
            stored["ids"], stored["documents"], stored["metadatas"]
        )
    }


def _dense_search(
    question: str, n: int, doc_ids: list[str] | None, collection=None
) -> list[tuple[str, float]]:
    col = collection if collection is not None else get_collection()
    where = {"doc_id": {"$in": doc_ids}} if doc_ids else None

    result = col.query(
        query_embeddings=[embed_query(question)],
        n_results=min(n, max(1, col.count())),
        where=where,
        include=["distances"],
    )
    return list(zip(result["ids"][0], result["distances"][0]))


def retrieve(
    question: str,
    mode: RetrievalMode = RetrievalMode.HYBRID_RERANK,
    top_k: int = 5,
    doc_ids: list[str] | None = None,
    collection=None,
) -> tuple[list[RetrievedChunk], Trace]:
    """Retrieve candidates and return them with a trace of how they were found.

    The trace is part of the contract, not a log line: the inspector panel
    renders it, and the eval harness reads it.
    """
    started = time.perf_counter()
    candidates = max(settings.retrieve_candidates, top_k)

    trace = Trace(mode=mode, embedding_model=settings.embedding_model)

    dense_hits: list[tuple[str, float]] = []
    sparse_hits: list[tuple[str, float]] = []

    if mode in (RetrievalMode.DENSE, RetrievalMode.HYBRID, RetrievalMode.HYBRID_RERANK):
        dense_hits = _dense_search(question, candidates, doc_ids, collection)

    if mode in (RetrievalMode.SPARSE, RetrievalMode.HYBRID, RetrievalMode.HYBRID_RERANK):
        ensure_bm25(collection)
        sparse_hits = get_bm25_index().search(question, candidates)

    if mode is RetrievalMode.DENSE:
        ordered = [cid for cid, _ in dense_hits]
        fused: dict[str, float] = {}
    elif mode is RetrievalMode.SPARSE:
        ordered = [cid for cid, _ in sparse_hits]
        fused = {}
    else:
        merged = reciprocal_rank_fusion(
            [[cid for cid, _ in dense_hits], [cid for cid, _ in sparse_hits]],
            k=settings.rrf_k,
        )
        ordered = [cid for cid, _ in merged]
        fused = dict(merged)

    trace.candidates_considered = len(ordered)
    trace.retrieval_ms = (time.perf_counter() - started) * 1000

    # sparse-only mode has no doc filter of its own — apply it after the fact
    hydrated = _hydrate(ordered[:candidates], collection)
    if doc_ids:
        hydrated = {
            cid: rc for cid, rc in hydrated.items() if rc.meta.doc_id in doc_ids
        }
        ordered = [cid for cid in ordered if cid in hydrated]

    dense_by_id = dict(dense_hits)
    sparse_by_id = dict(sparse_hits)

    results: list[RetrievedChunk] = []
    for chunk_id in ordered[:candidates]:
        rc = hydrated.get(chunk_id)
        if rc is None:
            continue
        rc.dense_distance = dense_by_id.get(chunk_id)
        rc.sparse_score = sparse_by_id.get(chunk_id)
        rc.fused_score = fused.get(chunk_id)
        results.append(rc)

    if mode is RetrievalMode.HYBRID_RERANK and results:
        rerank_started = time.perf_counter()
        scores = rerank(question, [r.text for r in results])
        for rc, score in zip(results, scores):
            rc.rerank_score = score
        results.sort(key=lambda r: -(r.rerank_score or float("-inf")))
        trace.rerank_ms = (time.perf_counter() - rerank_started) * 1000

    results = results[:top_k]
    trace.chunks_sent_to_llm = len(results)
    trace.total_ms = (time.perf_counter() - started) * 1000
    return results, trace