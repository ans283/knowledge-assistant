import pytest

from app.core.loaders import (
    EmptyDocumentError, LoadedDocument, PageSpan, UnsupportedFormatError,
    assemble_pages, discover_corpus, extract_title, load_document,
    normalize, slugify,
)


# ── normalization ────────────────────────────────────────────────────────

def test_normalize_is_idempotent():
    """The offset contract depends on this: applying normalize twice must not
    change the string, or re-processing a document would shift every offset."""
    messy = "Leave  Policy\r\n\r\n\r\n\r\nEmployees   are\tentitled to  \nreimburse-\nment.\n\n\n"
    once = normalize(messy)
    assert normalize(once) == once


def test_normalize_rejoins_hyphenated_linebreaks():
    assert "reimbursement" in normalize("reimburse-\nment is available")


def test_normalize_collapses_spaces_but_keeps_paragraphs():
    out = normalize("one   two\n\n\n\nthree")
    assert out == "one two\n\nthree"


def test_normalize_folds_unicode_ligatures():
    assert "office" in normalize("o\ufb03ce")   # ﬃ ligature


# ── page assembly ────────────────────────────────────────────────────────

def test_assemble_pages_offsets_are_exact():
    text, page_map = assemble_pages(["page one text", "page two text"])
    for span in page_map:
        extracted = text[span.char_start:span.char_end]
        assert extracted == f"page {'one' if span.page == 1 else 'two'} text"


def test_assemble_pages_skips_blank_pages():
    text, page_map = assemble_pages(["real content", "   \n  ", "more content"])
    assert [s.page for s in page_map] == [1, 3]     # page numbers preserved
    assert text[page_map[1].char_start:page_map[1].char_end] == "more content"


def test_assemble_pages_handles_empty_input():
    text, page_map = assemble_pages([])
    assert text == "" and page_map == []


def test_pages_for_span_reports_overlap():
    text, page_map = assemble_pages(["alpha", "bravo", "charlie"])
    doc = LoadedDocument(
        doc_id="d", title="d", source_path="d", text=text, page_map=page_map
    )
    p1 = page_map[0]
    assert doc.pages_for_span(p1.char_start, p1.char_end) == [1]
    # a chunk straddling the page break touches both pages
    assert doc.pages_for_span(p1.char_end - 1, page_map[1].char_start + 1) == [1, 2]


# ── identifiers ──────────────────────────────────────────────────────────

def test_slugify_is_stable_and_safe():
    assert slugify("Employee Handbook 2024") == "employee-handbook-2024"
    assert slugify("Sécurité_Policy!!") == "securite-policy"
    assert slugify("Employee Handbook 2024") == slugify("Employee Handbook 2024")
    assert len(slugify("a")) >= 3          # padded to a usable length


def test_extract_title_prefers_markdown_h1():
    assert extract_title("# Security Policy\n\nbody", "fallback") == "Security Policy"
    assert extract_title("no heading here", "my_doc-name") == "my doc name"


# ── load_document ────────────────────────────────────────────────────────

def test_load_markdown(tmp_path):
    p = tmp_path / "employee_handbook.md"
    p.write_text("# Employee Handbook\n\nParental leave is twelve weeks.\n",
                 encoding="utf-8")

    doc = load_document(p)

    assert doc.doc_id == "employee-handbook"
    assert doc.title == "Employee Handbook"
    assert doc.content_hash.startswith("sha256:")
    assert doc.page_count == 1
    assert doc.text[doc.page_map[0].char_start:doc.page_map[0].char_end] == doc.text
    assert doc.char_count == len(doc.text)


def test_load_rejects_unsupported_format(tmp_path):
    p = tmp_path / "sheet.xlsx"
    p.write_bytes(b"not text")
    with pytest.raises(UnsupportedFormatError):
        load_document(p)


def test_load_rejects_empty_document(tmp_path):
    p = tmp_path / "blank.txt"
    p.write_text("   \n\n  ", encoding="utf-8")
    with pytest.raises(EmptyDocumentError):
        load_document(p)


def test_content_hash_detects_change(tmp_path):
    p = tmp_path / "policy.txt"
    p.write_text("original text", encoding="utf-8")
    first = load_document(p).content_hash

    p.write_text("original text", encoding="utf-8")
    assert load_document(p).content_hash == first     # same bytes, same hash

    p.write_text("edited text", encoding="utf-8")
    assert load_document(p).content_hash != first


def test_discover_corpus_is_sorted_and_filtered(tmp_path):
    (tmp_path / "b.md").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "skip.xlsx").write_bytes(b"x")

    found = [p.name for p in discover_corpus(tmp_path)]
    assert found == ["a.txt", "b.md"]