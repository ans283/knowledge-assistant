import pytest

from app.core.chunking import (
    MAX_TOKENS_HARD, TARGET_TOKENS, build_chunks, estimate_tokens,
    split_into_blocks,
)
from app.core.loaders import LoadedDocument, PageSpan, normalize


def make_doc(text: str, title="Privacy Management Policy", doc_id="privacy-policy") -> LoadedDocument:
    return LoadedDocument(
        doc_id=doc_id, title=title, source_path=f"corpus/{doc_id}",
        text=text,
        page_map=[PageSpan(page=1, char_start=0, char_end=len(text))],
        content_hash="sha256:test",
    )


SANS_SHAPE = normalize(
    "Privacy Management Policy\n"
    "(Last Updated February 2026)\n"
    "Purpose\n"
    "Our Data Privacy Policy aims to establish a comprehensive framework for "
    "protecting the privacy and confidentiality of personal data.\n"
    "Scope\n"
    "This policy applies to all employees, contractors, and third parties who "
    "process personal data on behalf of the organization.\n"
)


# ── the offset invariant ─────────────────────────────────────────────────

def test_offsets_reproduce_chunk_text_exactly():
    """The guarantee the entire citation feature rests on."""
    doc = make_doc(SANS_SHAPE)
    for chunk in build_chunks(doc):
        assert doc.text[chunk.meta.char_start:chunk.meta.char_end] == chunk.text


def test_offsets_hold_for_long_multi_chunk_documents():
    body = " ".join(
        f"Personal data must be retained for {n} years under clause {n}." 
        for n in range(1, 120)
    )
    doc = make_doc(f"Retention\n{body}")
    chunks = build_chunks(doc)

    assert len(chunks) > 1
    for chunk in chunks:
        assert doc.text[chunk.meta.char_start:chunk.meta.char_end] == chunk.text


def test_offsets_hold_after_non_ascii():
    doc = make_doc(
        "Sécurité\n"
        "Les données personnelles doivent être protégées conformément à la loi "
        "applicable et aux politiques internes de l'organisation.\n"
    )
    for chunk in build_chunks(doc):
        assert doc.text[chunk.meta.char_start:chunk.meta.char_end] == chunk.text


# ── structure detection ──────────────────────────────────────────────────

def test_detects_pdf_style_headings():
    blocks = split_into_blocks(SANS_SHAPE)
    paths = {b.section_path for b in blocks}
    assert any("Purpose" in p for p in paths)
    assert any("Scope" in p for p in paths)


def test_detects_markdown_heading_hierarchy():
    text = (
        "## Background\n"
        "GitLab provides a collaboration platform for software developers.\n"
        "### Request Volume\n"
        "GitLab received a total of 32 valid law enforcement requests.\n"
        "## Process\n"
        "Each request is reviewed by counsel before any data is disclosed.\n"
    )
    paths = [b.section_path for b in split_into_blocks(text)]

    assert "Background" in paths
    assert "Background > Request Volume" in paths
    assert "Process" in paths          # ### popped when ## appeared


def test_heading_text_is_not_duplicated_into_chunk_body():
    doc = make_doc(SANS_SHAPE)
    purpose = next(c for c in build_chunks(doc) if "Purpose" in c.meta.section_path)
    assert not purpose.text.startswith("Purpose")
    assert purpose.text.startswith("Our Data Privacy Policy")


# ── embed_text disambiguation ────────────────────────────────────────────

def test_embed_text_carries_section_path_but_display_text_does_not():
    """The fix for five SANS policies with near-identical Purpose sections."""
    doc = make_doc(SANS_SHAPE)
    purpose = next(c for c in build_chunks(doc) if "Purpose" in c.meta.section_path)

    assert purpose.embed_text.startswith("Privacy Management Policy > Purpose")
    assert purpose.text in purpose.embed_text
    assert "Privacy Management Policy" not in purpose.text


def test_boilerplate_sections_get_distinct_embed_text():
    shared = (
        "Purpose\n"
        "Our policy aims to establish a comprehensive framework for protecting "
        "organizational assets and ensuring regulatory compliance.\n"
    )
    a = build_chunks(make_doc(shared, title="Privacy Management Policy", doc_id="privacy"))
    b = build_chunks(make_doc(shared, title="Software Development Policy", doc_id="software"))

    assert a[0].text == b[0].text                  # identical prose
    assert a[0].embed_text != b[0].embed_text      # distinct vectors
    assert a[0].chunk_id != b[0].chunk_id


# ── tables ───────────────────────────────────────────────────────────────

def test_markdown_table_is_never_split():
    rows = "\n".join(f"| Subpoena {n} | {n * 3} | Disclosed |" for n in range(1, 25))
    text = (
        "## Request Volume\n"
        "The following requests were received during the reporting period.\n"
        "| Type | Count | Outcome |\n"
        "|------|:-----:|---------|\n"
        f"{rows}\n"
        "All requests were reviewed by legal counsel before disclosure.\n"
    )
    doc = make_doc(text)
    chunks = build_chunks(doc)

    table_chunks = [c for c in chunks if "|------" in c.text]
    assert len(table_chunks) == 1                              # exactly one
    assert table_chunks[0].text.count("| Subpoena") == 24      # all rows present


def test_table_beyond_hard_cap_splits_on_row_boundaries():
    """Oversized tables must split, but never mid-row: a fragment like
    '| Request 47 | 47 | Disc' is worse than useless in a citation."""
    rows = "\n".join(f"| Request {n} | {n} | Disclosed in full |" for n in range(1, 400))
    doc = make_doc(f"## Volume\n| Type | Count | Outcome |\n|---|---|---|\n{rows}\n")
    chunks = build_chunks(doc)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.meta.token_count <= MAX_TOKENS_HARD * 1.2
        for line in chunk.text.split("\n"):
            if line.strip():
                assert line.strip().startswith("|") and line.strip().endswith("|")

    # every row survives somewhere, exactly once
    joined = "\n".join(c.text for c in chunks)
    assert joined.count("| Request 200 |") == 1
    assert joined.count("| Request 399 |") == 1
    
# ── sizing and overlap ───────────────────────────────────────────────────

def test_chunks_respect_target_size():
    body = " ".join(f"Clause {n} governs the retention of personal data." for n in range(1, 200))
    for chunk in build_chunks(make_doc(f"Retention\n{body}")):
        assert chunk.meta.token_count <= MAX_TOKENS_HARD


def test_consecutive_chunks_overlap():
    body = " ".join(f"Clause {n} governs the retention of personal data." for n in range(1, 200))
    chunks = build_chunks(make_doc(f"Retention\n{body}"))

    assert len(chunks) >= 2
    first, second = chunks[0], chunks[1]
    assert second.meta.char_start < first.meta.char_end       # ranges overlap


def test_overlap_never_starts_mid_word():
    body = " ".join(f"Clause {n} governs retention of personal data." for n in range(1, 200))
    doc = make_doc(f"Retention\n{body}")

    for chunk in build_chunks(doc)[1:]:
        preceding = doc.text[chunk.meta.char_start - 1]
        assert preceding.isspace() or not doc.text[chunk.meta.char_start].isalnum()


# ── metadata ─────────────────────────────────────────────────────────────

def test_chunk_ids_are_unique_stable_and_ordered():
    doc = make_doc(SANS_SHAPE)
    chunks = build_chunks(doc)
    ids = [c.chunk_id for c in chunks]

    assert len(ids) == len(set(ids))
    assert ids == [c.chunk_id for c in build_chunks(doc)]     # deterministic
    assert [c.meta.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.chunk_id.startswith("privacy-policy::") for c in chunks)


def test_pages_are_attributed_from_the_page_map():
    text = normalize("Purpose\nFirst page content about privacy.")
    doc = LoadedDocument(
        doc_id="d", title="Doc", source_path="d", text=text,
        page_map=[
            PageSpan(page=1, char_start=0, char_end=20),
            PageSpan(page=2, char_start=20, char_end=len(text)),
        ],
        content_hash="sha256:x",
    )
    chunks = build_chunks(doc)
    assert all(c.meta.pages for c in chunks)


def test_metadata_survives_chroma_encoding():
    doc = make_doc(SANS_SHAPE)
    for chunk in build_chunks(doc):
        encoded = chunk.meta.to_chroma()
        assert all(isinstance(v, (str, int, float, bool)) for v in encoded.values())
        assert ChunkMeta_roundtrip(chunk.meta) == chunk.meta


def ChunkMeta_roundtrip(meta):
    from app.core.schemas import ChunkMeta
    return ChunkMeta.from_chroma(meta.to_chroma())


# ── edge cases ───────────────────────────────────────────────────────────

def test_empty_and_tiny_documents():
    assert build_chunks(make_doc("")) == []
    assert build_chunks(make_doc("Short.")) == []             # below MIN_CHUNK_TOKENS


def test_document_with_no_headings_still_chunks():
    body = " ".join(f"Sentence number {n} discusses data handling." for n in range(1, 60))
    chunks = build_chunks(make_doc(body))

    assert chunks
    assert all(c.meta.section_path == "" for c in chunks)
    assert all(c.embed_text.startswith("Privacy Management Policy") for c in chunks)


def test_estimate_tokens_is_monotonic():
    assert estimate_tokens("") >= 1
    assert estimate_tokens("a" * 400) > estimate_tokens("a" * 100)