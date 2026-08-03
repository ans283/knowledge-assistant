"""Ingestion pipeline: corpus files → searchable index.

Orchestrates the components built in Steps 3-5:
    load_document → build_chunks → embed_documents → Chroma + registry

Idempotency is the central property. Re-running ingest on an unchanged corpus
must be a no-op, so the pipeline can be re-run freely after a chunker change
without duplicating vectors or orphaning old ones.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from app.config import settings
from app.core.chunking import build_chunks
from app.core.embeddings import embed_documents
from app.core.loaders import (
    EmptyDocumentError, UnsupportedFormatError, discover_corpus, load_document,
)
from app.core.registry import (
    content_hash_unchanged, init_db, save_normalized, set_chunk_count, upsert_document,
)
from app.core.schemas import Chunk, IngestResponse
from app.core.store import get_collection

log = logging.getLogger(__name__)


def delete_document_chunks(doc_id: str, collection=None) -> int:
    """Remove every vector belonging to a document.

    Re-ingesting an edited file must delete the old chunks first. Chunk counts
    change between versions, so overwriting by id would strand the surplus:
    a 40-chunk document edited down to 30 would leave 10 stale vectors that
    still match queries and cite text no longer in the document.
    """
    col = collection if collection is not None else get_collection()
    existing = col.get(where={"doc_id": doc_id}, include=[])
    ids = existing.get("ids", [])
    if ids:
        col.delete(ids=ids)
    return len(ids)


def index_chunks(chunks: list[Chunk], collection=None) -> int:
    """Embed and store. Vectors are always supplied explicitly — Chroma's
    default embedding function must never run (see store.py)."""
    if not chunks:
        return 0

    col = collection if collection is not None else get_collection()
    vectors = embed_documents([c.embed_text for c in chunks])

    col.add(
        ids=[c.chunk_id for c in chunks],
        documents=[c.text for c in chunks],      # display text, not embed_text
        embeddings=vectors,
        metadatas=[c.meta.to_chroma() for c in chunks],
    )
    return len(chunks)


def ingest_file(
    path: str | Path,
    force: bool = False,
    collection=None,
    db_path: Path | None = None,
    normalized_dir: Path | None = None,
) -> tuple[int, int]:
    """Ingest one file. Returns (chunks_added, chunks_skipped)."""
    doc = load_document(path)

    if not force and content_hash_unchanged(doc.doc_id, doc.content_hash, db_path):
        return 0, 1

    delete_document_chunks(doc.doc_id, collection)

    # canonical text first: citation offsets are meaningless without it
    save_normalized(doc.doc_id, doc.text, normalized_dir)
    upsert_document(doc, db_path)

    chunks = build_chunks(doc)
    added = index_chunks(chunks, collection)
    set_chunk_count(doc.doc_id, added, db_path)

    log.info("indexed %s: %d chunks", doc.doc_id, added)
    return added, 0


def ingest_corpus(
    paths: list[str] | None = None,
    force: bool = False,
    collection=None,
    db_path: Path | None = None,
    normalized_dir: Path | None = None,
    corpus_dir: Path | None = None,
) -> IngestResponse:
    """Ingest a list of files, or the whole corpus directory.

    One bad file must not abort the run — errors are collected and reported
    per file so a single malformed PDF doesn't block the other eleven.
    """
    started = time.perf_counter()
    init_db(db_path)

    targets = (
        [Path(p) for p in paths] if paths
        else discover_corpus(corpus_dir or settings.corpus_dir)
    )

    processed = added = skipped = 0
    errors: list[str] = []

    for path in targets:
        try:
            n_added, n_skipped = ingest_file(
                path, force, collection, db_path, normalized_dir
            )
            added += n_added
            skipped += n_skipped
            processed += 1
        except (UnsupportedFormatError, EmptyDocumentError, FileNotFoundError) as exc:
            errors.append(str(exc))
        except Exception as exc:                       # noqa: BLE001
            log.exception("unexpected failure ingesting %s", path)
            errors.append(f"{Path(path).name}: unexpected error: {exc}")

    from app.core.retriever import get_bm25_index
    get_bm25_index().build(collection)
    
    return IngestResponse(
        documents_processed=processed,
        chunks_added=added,
        chunks_skipped=skipped,
        errors=errors,
        duration_ms=(time.perf_counter() - started) * 1000,
    )