"""Question-answering and document-reading endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.pipeline import answer_question
from app.core.registry import get_document, read_normalized
from app.core.schemas import ChunkMeta, QueryRequest, QueryResponse
from app.core.store import get_collection

router = APIRouter(tags=["query"])


class DocumentText(BaseModel):
    """Full canonical text plus the offsets citations index into.

    The frontend fetches this once per document and highlights
    [char_start, char_end) directly — no re-fetch per citation, and no
    re-derivation of offsets client-side where they could drift.
    """
    doc_id: str
    title: str
    text: str
    char_count: int
    page_map: list[dict]


class ChunkSummary(BaseModel):
    chunk_id: str
    section_path: str
    char_start: int
    char_end: int
    token_count: int
    text: str


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    """Answer a question from the indexed corpus.

    Always returns 200 with a valid QueryResponse. An out-of-scope question is
    a successful abstention, not an error — 404 would be wrong, because the
    system worked correctly and has something useful to say about why it
    cannot answer.
    """
    return answer_question(request)


@router.get("/documents/{doc_id}/text", response_model=DocumentText)
def document_text(doc_id: str) -> DocumentText:
    """Canonical text for the citation viewer.

    doc_id is validated against the registry before any filesystem access:
    it becomes a filename, so an unchecked value would be a path traversal.
    """
    document = get_document(doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"unknown doc_id: {doc_id!r}")

    try:
        text = read_normalized(doc_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=409,
            detail=f"{doc_id!r} is registered but its text is missing — re-run /ingest",
        )

    return DocumentText(
        doc_id=doc_id,
        title=document["title"],
        text=text,
        char_count=len(text),
        page_map=document["page_map"],
    )


@router.get("/documents/{doc_id}/chunks", response_model=list[ChunkSummary])
def document_chunks(
    doc_id: str, limit: int = Query(default=200, ge=1, le=1000)
) -> list[ChunkSummary]:
    """Chunk boundaries for a document. A debugging view — this is how you see
    what the chunker actually produced without re-running it."""
    if get_document(doc_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown doc_id: {doc_id!r}")

    stored = get_collection().get(
        where={"doc_id": doc_id}, include=["documents", "metadatas"], limit=limit
    )

    summaries = [
        ChunkSummary(
            chunk_id=chunk_id,
            section_path=meta.section_path,
            char_start=meta.char_start,
            char_end=meta.char_end,
            token_count=meta.token_count,
            text=document,
        )
        for chunk_id, document, meta in (
            (cid, doc, ChunkMeta.from_chroma(m))
            for cid, doc, m in zip(
                stored["ids"], stored["documents"], stored["metadatas"]
            )
        )
    ]
    return sorted(summaries, key=lambda c: c.char_start)