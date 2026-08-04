from __future__ import annotations

import json
from enum import Enum

from pydantic import BaseModel, Field, model_validator


# ─────────────────────────── enums ───────────────────────────

class RetrievalMode(str, Enum):
    DENSE = "dense"
    SPARSE = "sparse"
    HYBRID = "hybrid"
    HYBRID_RERANK = "hybrid_rerank"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ─────────────────────── chunks & storage ────────────────────

class ChunkMeta(BaseModel):
    """Chunk provenance. Mirrors what lands in Chroma metadata.

    Chroma metadata values must be flat scalars for `where` filtering to work,
    so `pages` is JSON-encoded on write. `char_start`/`char_end` index into
    data/normalized/{doc_id}.txt — this is what makes span highlighting work.
    """
    doc_id: str
    doc_title: str
    section_path: str = ""
    char_start: int
    char_end: int
    chunk_index: int
    token_count: int
    content_hash: str
    pages: list[int] = Field(default_factory=list)

    def to_chroma(self) -> dict[str, str | int | float | bool]:
        d = self.model_dump(exclude={"pages"})
        d["pages_json"] = json.dumps(self.pages)
        return d

    @classmethod
    def from_chroma(cls, d: dict) -> ChunkMeta:
        d = dict(d)
        d["pages"] = json.loads(d.pop("pages_json", "[]"))
        return cls(**d)


class Chunk(BaseModel):
    """A chunk ready for indexing."""
    chunk_id: str
    text: str          # displayed to the user verbatim
    embed_text: str    # section_path prepended — improves retrieval, never shown
    meta: ChunkMeta


class RetrievedChunk(BaseModel):
    """A candidate with whatever scores the active retrieval mode produced.
    All score fields are optional because dense mode has no sparse rank, etc.
    The inspector panel renders these directly."""
    chunk_id: str
    text: str
    meta: ChunkMeta
    dense_distance: float | None = None   # cosine distance, lower is better
    sparse_score: float | None = None     # BM25, higher is better
    fused_score: float | None = None      # RRF, higher is better
    rerank_score: float | None = None     # cross-encoder, higher is better


# ──────────────── what the LLM is allowed to say ─────────────

class LLMCitation(BaseModel):
    """Produced by the model. Intentionally minimal — the model supplies only
    an id and a verbatim quote, so it has nothing else to hallucinate."""
    chunk_id: str
    quote: str = Field(max_length=400)


class LLMAnswer(BaseModel):
    """Raw parsed JSON from the generator, pre-verification."""
    input_tokens: int | None = None
    output_tokens: int | None = None
    answer: str
    citations: list[LLMCitation] = Field(default_factory=list)
    insufficient_context: bool = False
    missing_information: str | None = None
    confidence: Confidence = Confidence.MEDIUM


# ──────────────── what the API returns ───────────────────────

class Citation(BaseModel):
    """Server-built, verified citation. Offsets and titles come from the
    retrieved chunk, never from the model."""
    chunk_id: str
    doc_id: str
    doc_title: str
    section_path: str
    quote: str
    char_start: int
    char_end: int
    pages: list[int] = Field(default_factory=list)


class Trace(BaseModel):
    """Per-query observability. Powers the retrieval inspector."""
    mode: RetrievalMode
    candidates_considered: int = 0
    chunks_sent_to_llm: int = 0
    embedding_model: str = ""
    llm_provider: str = ""
    llm_model: str = ""
    retrieval_ms: float = 0.0
    rerank_ms: float = 0.0
    generation_ms: float = 0.0
    total_ms: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    abstained_before_llm: bool = False
    citations_dropped: int = 0


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    doc_ids: list[str] | None = None      # scope retrieval to specific docs
    mode: RetrievalMode = RetrievalMode.HYBRID_RERANK
    top_k: int = Field(default=5, ge=1, le=20)


class QueryResponse(BaseModel):
    question: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    insufficient_context: bool = False
    missing_information: str | None = None
    confidence: Confidence = Confidence.MEDIUM
    retrieved: list[RetrievedChunk] = Field(default_factory=list)
    trace: Trace

    @model_validator(mode="after")
    def enforce_grounding_invariant(self) -> QueryResponse:
        """The core guarantee of this system, enforced by the type system:
        an answer is either grounded in at least one verified citation, or it
        abstains and says what it would need. There is no third state."""
        if self.insufficient_context:
            if self.citations:
                raise ValueError("abstained answer must have no citations")
            if not self.missing_information:
                raise ValueError("abstained answer must state missing_information")
        else:
            if not self.citations:
                raise ValueError("grounded answer must have >= 1 citation")
        return self

    @classmethod
    def abstain(
        cls,
        question: str,
        missing: str,
        trace: Trace,
        retrieved: list[RetrievedChunk] | None = None,
        answer: str = "The provided documents don't contain enough information "
                      "to answer this question.",
    ) -> QueryResponse:
        return cls(
            question=question,
            answer=answer,
            citations=[],
            insufficient_context=True,
            missing_information=missing,
            confidence=Confidence.LOW,
            retrieved=retrieved or [],
            trace=trace,
        )


# ──────────────────── ingestion & documents ──────────────────

class IngestRequest(BaseModel):
    paths: list[str] | None = None   # None → ingest everything in data/corpus
    force: bool = False              # re-embed even if content_hash matches


class IngestResponse(BaseModel):
    documents_processed: int
    chunks_added: int
    chunks_skipped: int
    errors: list[str] = Field(default_factory=list)
    duration_ms: float


class DocumentSummary(BaseModel):
    doc_id: str
    title: str
    source_path: str
    char_count: int
    chunk_count: int
    ingested_at: str