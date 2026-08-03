import chromadb
import pytest

from app.core.indexer import delete_document_chunks, ingest_corpus, ingest_file
from app.core.registry import get_document, init_db, read_normalized
from app.core.schemas import ChunkMeta

pytestmark = pytest.mark.slow          # embeds text

COSINE = {"hnsw": {"space": "cosine"}}

POLICY = """Privacy Management Policy

Purpose
Our Data Privacy Policy aims to establish a comprehensive framework for protecting
the privacy and confidentiality of personal data entrusted to our organization.
Personal data must be handled lawfully, fairly, and transparently at all times.

Scope
This policy applies to all employees, contractors, and third parties who process
personal data on behalf of the organization, regardless of location or system.

Safeguards
Personal data shall be retained for seven years following the termination of the
customer relationship, after which it is securely destroyed using approved methods.
"""


@pytest.fixture
def env(tmp_path, request):
    """Isolated store, registry, and normalized-text directory per test.

    The collection name is derived from the test name: EphemeralClient can
    hand back a cached client within one process, so a fixed collection name
    would leak vectors between tests and produce order-dependent failures.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "privacy_policy.md").write_text(POLICY, encoding="utf-8")

    db = tmp_path / "registry.db"
    init_db(db)

    safe = "".join(c if c.isalnum() else "-" for c in request.node.name)[:60].strip("-")
    col = chromadb.EphemeralClient().get_or_create_collection(
        name=f"test-{safe}", configuration=COSINE
    )
    return {
        "corpus": corpus, "db": db, "norm": tmp_path / "normalized", "col": col,
    }

def run(env, **kw):
    return ingest_corpus(
        collection=env["col"], db_path=env["db"],
        normalized_dir=env["norm"], corpus_dir=env["corpus"], **kw
    )


def test_ingest_populates_store_registry_and_normalized_text(env):
    result = run(env)

    assert result.documents_processed == 1
    assert result.chunks_added > 0
    assert result.errors == []
    assert env["col"].count() == result.chunks_added

    doc = get_document("privacy-policy", env["db"])
    assert doc["chunk_count"] == result.chunks_added
    assert doc["title"] == "Privacy Management Policy"

    assert "Personal data shall be retained" in read_normalized("privacy-policy", env["norm"])


def test_reingest_is_a_noop_when_unchanged(env):
    first = run(env)
    second = run(env)

    assert second.chunks_added == 0
    assert second.chunks_skipped == 1
    assert env["col"].count() == first.chunks_added      # not doubled


def test_force_reingest_replaces_rather_than_appends(env):
    first = run(env)
    second = run(env, force=True)

    assert second.chunks_added == first.chunks_added
    assert env["col"].count() == first.chunks_added      # old vectors deleted


def test_shrinking_a_document_leaves_no_orphan_vectors(env):
    """The reason delete-before-insert exists: overwriting by id would strand
    surplus chunks that still match queries."""
    run(env)

    (env["corpus"] / "privacy_policy.md").write_text(
        "Privacy Management Policy\n\nPurpose\n"
        "Personal data must be handled lawfully, fairly, and transparently "
        "at all times under this policy.\n",
        encoding="utf-8",
    )
    second = run(env)

    assert env["col"].count() == second.chunks_added
    stored = env["col"].get(include=["documents"])
    assert not any("seven years" in d for d in stored["documents"])


def test_stored_metadata_rehydrates_into_chunkmeta(env):
    run(env)
    stored = env["col"].get(include=["metadatas"], limit=1)
    meta = ChunkMeta.from_chroma(stored["metadatas"][0])

    assert meta.doc_id == "privacy-policy"
    assert meta.char_end > meta.char_start
    assert meta.content_hash.startswith("sha256:")


def test_offsets_survive_the_full_pipeline(env):
    """End-to-end offset invariant: what Chroma returns must slice exactly out
    of the normalized text on disk. This is the citation guarantee."""
    run(env)
    text = read_normalized("privacy-policy", env["norm"])
    stored = env["col"].get(include=["documents", "metadatas"])

    for document, raw_meta in zip(stored["documents"], stored["metadatas"]):
        meta = ChunkMeta.from_chroma(raw_meta)
        assert text[meta.char_start:meta.char_end] == document


def test_stored_document_is_display_text_not_embed_text(env):
    """Chroma's document field feeds citation quotes, so it must not carry the
    section-path prefix used for embedding."""
    run(env)
    stored = env["col"].get(include=["documents"])
    assert not any(d.startswith("Privacy Management Policy >") for d in stored["documents"])


def test_unsupported_extensions_are_skipped_not_errors(env):
    """A stray .xlsx in the corpus directory isn't a failure — it isn't a
    corpus file. discover_corpus filters by extension before ingest sees it."""
    (env["corpus"] / "broken.xlsx").write_bytes(b"not a document")

    result = run(env)

    assert result.documents_processed == 1
    assert result.errors == []


def test_one_bad_file_does_not_abort_the_run(env):
    """A supported file that fails to parse is reported, and the rest proceed."""
    (env["corpus"] / "empty.txt").write_text("   \n  ", encoding="utf-8")

    result = run(env)

    assert result.documents_processed == 1        # the good one still landed
    assert len(result.errors) == 1
    assert "empty.txt" in result.errors[0]
    assert env["col"].count() > 0

def test_delete_document_chunks_is_scoped(env):
    run(env)
    (env["corpus"] / "second_policy.md").write_text(
        "Second Policy\n\nPurpose\nThis unrelated policy governs software "
        "development practices across all engineering teams.\n",
        encoding="utf-8",
    )
    run(env)
    total = env["col"].count()

    removed = delete_document_chunks("privacy-policy", env["col"])

    assert removed > 0
    assert env["col"].count() == total - removed
    remaining = env["col"].get(include=["metadatas"])
    assert all(m["doc_id"] == "second-policy" for m in remaining["metadatas"])


def test_ingest_specific_paths_only(env):
    (env["corpus"] / "other.md").write_text(
        "Other Policy\n\nPurpose\nThis policy covers unrelated operational "
        "matters within the organization.\n",
        encoding="utf-8",
    )
    result = ingest_corpus(
        paths=[str(env["corpus"] / "privacy_policy.md")],
        collection=env["col"], db_path=env["db"], normalized_dir=env["norm"],
    )

    assert result.documents_processed == 1
    metas = env["col"].get(include=["metadatas"])["metadatas"]
    assert all(m["doc_id"] == "privacy-policy" for m in metas)