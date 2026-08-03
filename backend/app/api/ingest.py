"""Ingestion endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.indexer import ingest_corpus
from app.core.registry import list_documents
from app.core.schemas import DocumentSummary, IngestRequest, IngestResponse

router = APIRouter(tags=["ingest"])


@router.post("/ingest", response_model=IngestResponse)
def ingest(request: IngestRequest) -> IngestResponse:
    """Ingest the corpus directory, or specific paths.

    Idempotent: unchanged files are skipped by content hash. Pass force=true
    to re-embed everything, which is what you want after a chunker change.
    """
    result = ingest_corpus(paths=request.paths, force=request.force)

    if result.errors and result.documents_processed == 0:
        raise HTTPException(status_code=422, detail={"errors": result.errors})

    return result


@router.get("/documents", response_model=list[DocumentSummary])
def documents() -> list[DocumentSummary]:
    return [
        DocumentSummary(
            doc_id=d["doc_id"],
            title=d["title"],
            source_path=d["source_path"],
            char_count=d["char_count"],
            chunk_count=d["chunk_count"],
            ingested_at=d["ingested_at"],
        )
        for d in list_documents()
    ]