import pytest

from app.core.loaders import LoadedDocument, PageSpan
from app.core.registry import (
    content_hash_unchanged, delete_document, get_document, init_db,
    list_documents, page_spans, read_normalized, read_span, save_normalized,
    set_chunk_count, upsert_document,
)


@pytest.fixture
def db(tmp_path):
    """Fresh SQLite file per test — no shared state between tests."""
    path = tmp_path / "registry.db"
    init_db(path)
    return path


def make_doc(doc_id="handbook", text="Parental leave is twelve weeks.") -> LoadedDocument:
    return LoadedDocument(
        doc_id=doc_id,
        title="Employee Handbook",
        source_path=f"corpus/{doc_id}.md",
        text=text,
        page_map=[PageSpan(page=1, char_start=0, char_end=len(text))],
        content_hash="sha256:abc123",
    )


def test_init_db_is_idempotent(tmp_path):
    path = tmp_path / "r.db"
    init_db(path)
    init_db(path)                       # must not raise
    assert list_documents(path) == []


def test_upsert_and_get_roundtrip(db):
    doc = make_doc()
    upsert_document(doc, db)

    stored = get_document("handbook", db)
    assert stored["title"] == "Employee Handbook"
    assert stored["char_count"] == doc.char_count
    assert stored["chunk_count"] == 0
    assert stored["page_map"] == [
        {"page": 1, "char_start": 0, "char_end": doc.char_count}
    ]
    assert stored["ingested_at"]


def test_get_missing_document_returns_none(db):
    assert get_document("nope", db) is None


def test_upsert_twice_updates_rather_than_duplicates(db):
    upsert_document(make_doc(), db)
    upsert_document(make_doc(text="Parental leave is sixteen weeks."), db)

    docs = list_documents(db)
    assert len(docs) == 1
    assert docs[0]["char_count"] == len("Parental leave is sixteen weeks.")


def test_content_hash_unchanged_drives_idempotent_ingest(db):
    doc = make_doc()
    assert content_hash_unchanged("handbook", doc.content_hash, db) is False
    upsert_document(doc, db)
    assert content_hash_unchanged("handbook", doc.content_hash, db) is True
    assert content_hash_unchanged("handbook", "sha256:different", db) is False


def test_list_documents_is_ordered(db):
    upsert_document(make_doc(doc_id="secpol"), db)
    upsert_document(make_doc(doc_id="handbook"), db)
    assert [d["doc_id"] for d in list_documents(db)] == ["handbook", "secpol"]


def test_set_chunk_count(db):
    upsert_document(make_doc(), db)
    set_chunk_count("handbook", 47, db)
    assert get_document("handbook", db)["chunk_count"] == 47


def test_page_spans_rehydrates_dataclasses(db):
    upsert_document(make_doc(), db)
    spans = page_spans("handbook", db)
    assert spans[0] == PageSpan(page=1, char_start=0, char_end=31)


def test_delete_document(db):
    upsert_document(make_doc(), db)
    delete_document("handbook", db)
    assert get_document("handbook", db) is None


# ── canonical text on disk ───────────────────────────────────────────────

def test_normalized_text_roundtrip_is_byte_exact(tmp_path):
    text = "Line one.\n\nLine two with trailing content."
    save_normalized("handbook", text, tmp_path)
    assert read_normalized("handbook", tmp_path) == text


def test_read_span_matches_the_offsets_used_by_citations(tmp_path):
    text = "Parental leave is twelve weeks for all full-time employees."
    save_normalized("handbook", text, tmp_path)

    start, end = 0, len("Parental leave is twelve weeks")
    assert read_span("handbook", start, end, tmp_path) == "Parental leave is twelve weeks"


def test_read_span_is_correct_after_non_ascii(tmp_path):
    """Offsets are code-point indices, not bytes. A multi-byte character
    before the span must not shift it."""
    text = "Sécurité policy: rotate passwords every 90 days."
    save_normalized("secpol", text, tmp_path)

    target = "rotate passwords"
    start = text.index(target)
    assert read_span("secpol", start, start + len(target), tmp_path) == target


def test_read_normalized_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_normalized("ghost", tmp_path)