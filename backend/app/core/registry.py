"""Document-level persistence: SQLite registry + canonical text files.

Chroma stores chunks. This stores documents — the relational metadata that
must exist *before* chunks do, plus the canonical text that offsets index into.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.core.loaders import LoadedDocument, PageSpan

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,
    title        TEXT    NOT NULL,
    source_path  TEXT    NOT NULL,
    content_hash TEXT    NOT NULL,
    char_count   INTEGER NOT NULL,
    page_count   INTEGER NOT NULL,
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    page_map     TEXT    NOT NULL,
    ingested_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash);
"""


@contextmanager
def connection(db_path: Path | None = None):
    path = Path(db_path or settings.registry_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    with connection(db_path) as conn:
        conn.executescript(SCHEMA)


# ───────────────────────────── documents ─────────────────────────────────

def upsert_document(doc: LoadedDocument, db_path: Path | None = None) -> None:
    """Insert or replace. Idempotent: re-ingesting the same file is a no-op
    from the caller's perspective."""
    page_map_json = json.dumps([
        {"page": s.page, "char_start": s.char_start, "char_end": s.char_end}
        for s in doc.page_map
    ])
    with connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO documents
                (doc_id, title, source_path, content_hash,
                 char_count, page_count, page_map, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                title        = excluded.title,
                source_path  = excluded.source_path,
                content_hash = excluded.content_hash,
                char_count   = excluded.char_count,
                page_count   = excluded.page_count,
                page_map     = excluded.page_map,
                ingested_at  = excluded.ingested_at
            """,
            (
                doc.doc_id, doc.title, doc.source_path, doc.content_hash,
                doc.char_count, doc.page_count, page_map_json,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )


def get_document(doc_id: str, db_path: Path | None = None) -> dict | None:
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["page_map"] = json.loads(d["page_map"])
    return d


def list_documents(db_path: Path | None = None) -> list[dict]:
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM documents ORDER BY doc_id"
        ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["page_map"] = json.loads(d["page_map"])
        out.append(d)
    return out


def set_chunk_count(doc_id: str, count: int, db_path: Path | None = None) -> None:
    with connection(db_path) as conn:
        conn.execute(
            "UPDATE documents SET chunk_count = ? WHERE doc_id = ?", (count, doc_id)
        )


def content_hash_unchanged(
    doc_id: str, content_hash: str, db_path: Path | None = None
) -> bool:
    """Idempotency check used by /ingest to skip unchanged files."""
    existing = get_document(doc_id, db_path)
    return existing is not None and existing["content_hash"] == content_hash


def delete_document(doc_id: str, db_path: Path | None = None) -> None:
    with connection(db_path) as conn:
        conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))


def page_spans(doc_id: str, db_path: Path | None = None) -> list[PageSpan]:
    doc = get_document(doc_id, db_path)
    if doc is None:
        return []
    return [PageSpan(**s) for s in doc["page_map"]]


# ─────────────────────── canonical text on disk ──────────────────────────

def _normalized_path(doc_id: str, base_dir: Path | None = None) -> Path:
    return Path(base_dir or settings.normalized_dir) / f"{doc_id}.txt"


def save_normalized(doc_id: str, text: str, base_dir: Path | None = None) -> Path:
    path = _normalized_path(doc_id, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" disables newline translation on write. Without it, Windows
    # rewrites "\n" as "\r\n", adding a character per line and shifting every
    # citation offset after the first newline.
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return path


def read_normalized(doc_id: str, base_dir: Path | None = None) -> str:
    path = _normalized_path(doc_id, base_dir)
    if not path.exists():
        raise FileNotFoundError(f"no normalized text for doc_id={doc_id!r}")
    # newline="" must match the write side. Python's default universal-newlines
    # mode would translate "\r\n" back to "\n" on read — harmless here, but the
    # pair must agree for the roundtrip to be exact.
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()

def read_span(
    doc_id: str, char_start: int, char_end: int, base_dir: Path | None = None
) -> str:
    """Exact text of a citation span. Powers frontend highlighting.

    Reads the whole file and slices: char offsets are Python string indices
    (code points), NOT byte offsets, so seeking by byte would corrupt any
    span following a non-ASCII character.
    """
    return read_normalized(doc_id, base_dir)[char_start:char_end]