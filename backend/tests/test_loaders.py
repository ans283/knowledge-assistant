import pytest

from app.core.loaders import (
    EmptyDocumentError, LoadedDocument, PageSpan, UnsupportedFormatError,
    assemble_pages, discover_corpus, extract_title, load_document,
    looks_like_heading, normalize, slugify, split_frontmatter,
    strip_markdown_noise, strip_repeated_lines, unwrap_soft_linebreaks,
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


def test_extract_title_precedence():
    """Four tiers, highest first: frontmatter → markdown H1 → first
    heading-like line (the PDF case) → tidied filename."""
    # 1. frontmatter wins outright
    assert extract_title(
        "# Ignored Heading", "fallback", frontmatter_title="Personal Data Requests"
    ) == "Personal Data Requests"

    # 2. markdown H1 beats a heading-like first line
    assert extract_title(
        "Some Line\n\n# Security Policy\n\nbody", "fallback"
    ) == "Security Policy"

    # 3. PDF case — first line is the title, with no marker
    assert extract_title(
        "Privacy Management Policy\n(Last Updated February 2026)\n"
        "Purpose\nOur Data Privacy Policy aims to establish...",
        "fallback",
    ) == "Privacy Management Policy"

    # 4. nothing heading-like → tidied filename
    assert extract_title(
        "This document opens straight into a long prose sentence that ends in a period.",
        "my_doc-name",
    ) == "my doc name"
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


# ── soft line-break unwrapping (pypdf layout mode) ───────────────────────

def test_unwrap_joins_wrapped_sentences_but_keeps_headings():
    """Verbatim shape of the SANS layout-mode extraction."""
    raw = (
        "Privacy Management Policy\n"
        "(Last Updated February 2026)\n"
        "Purpose\n"
        "Our Data Privacy Policy aims to establish a comprehensive framework for protecting the\n"
        "privacy and confidentiality of personal and sensitive data entrusted to our organization.\n"
        "This policy aims to provide clear guidelines and procedures for data collection, storage,\n"
        "use, disclosure, and disposal in compliance with applicable privacy laws."
    )
    lines = unwrap_soft_linebreaks(raw).split("\n")

    assert "Purpose" in lines                       # heading survives alone
    assert any(l.startswith("Our Data Privacy Policy") and
               "privacy and confidentiality" in l for l in lines)
    assert any(l.startswith("This policy aims") for l in lines)   # new sentence
    assert not any(l.startswith("privacy and") for l in lines)    # was joined


def test_unwrap_leaves_bullets_alone():
    raw = "Requirements:\n- encrypt data at rest\n- rotate keys annually"
    assert unwrap_soft_linebreaks(raw).split("\n") == [
        "Requirements:", "- encrypt data at rest", "- rotate keys annually"
    ]


def test_unwrap_is_idempotent():
    raw = "a sentence that wraps across\nthe line boundary here.\nA new one."
    once = unwrap_soft_linebreaks(raw)
    assert unwrap_soft_linebreaks(once) == once


def test_normalize_still_idempotent_after_unwrapping():
    messy = (
        "   Privacy Management Policy   \n"
        "Purpose\n"
        "Our policy aims to protect the\n"
        "privacy of personal data.\n\n\n\n"
    )
    once = normalize(messy)
    assert normalize(once) == once


# ── running headers/footers ──────────────────────────────────────────────

def _fake_page(n: int, heading: str, body: str) -> str:
    """Realistic page shape: header lines, distinct body, footer."""
    return "\n".join([
        f"Page {n}",                                            # header
        "Privacy Management Policy",                            # header
        heading,                                                # distinct
        body,                                                   # distinct, middle
        "This standard clause appears on every single page.",    # repeated, middle
        f"Closing note for {heading.lower()}.",                  # distinct
        f"Continued discussion of {heading.lower()} follows.",   # distinct
        "Confidential - Internal Use Only",                      # footer
    ])


def test_strip_repeated_lines_removes_headers_and_footers():
    pages = [
        _fake_page(1, "Purpose", "The lawful basis for processing is documented."),
        _fake_page(2, "Scope", "All systems storing personal data are covered."),
        _fake_page(3, "Policy", "Retention periods are defined per data class."),
        _fake_page(4, "Compliance", "Audit evidence is retained for seven years."),
    ]
    cleaned = strip_repeated_lines(pages)

    assert not any("Page" in p for p in cleaned)              # digits masked
    assert not any("Privacy Management Policy" in p for p in cleaned)
    assert not any("Confidential" in p for p in cleaned)      # footer
    assert "Retention periods are defined per data class." in cleaned[2]
    assert "Policy" in cleaned[2]


def test_strip_repeated_lines_spares_repeats_in_page_body():
    """Position is the guard: a line repeating mid-page is content, not a
    running header, however often it recurs."""
    pages = [
        _fake_page(1, "Purpose", "distinct body one"),
        _fake_page(2, "Scope", "distinct body two"),
        _fake_page(3, "Policy", "distinct body three"),
        _fake_page(4, "Compliance", "distinct body four"),
    ]
    cleaned = strip_repeated_lines(pages)

    clause = "This standard clause appears on every single page."
    assert all(clause in p for p in cleaned)

def test_strip_repeated_lines_spares_long_repeated_text():
    """Only short lines are eligible — a repeated legal paragraph is content."""
    boiler = ("This document is confidential and proprietary to the organization "
              "and may not be reproduced without prior written consent of counsel.")
    pages = [f"{boiler}\nPage {n}" for n in (1, 2, 3, 4)]
    cleaned = strip_repeated_lines(pages)

    assert all(boiler in p for p in cleaned)
    assert not any("Page" in p for p in cleaned)


def test_strip_repeated_lines_skips_short_documents():
    pages = ["Header\nbody one", "Header\nbody two"]
    assert strip_repeated_lines(pages) == pages       # under min_pages


# ── markdown preprocessing ───────────────────────────────────────────────

def test_split_frontmatter_extracts_title_and_removes_block():
    raw = ('---\ntitle: "Data Protection Impact Assessment (DPIA)"\n'
           'description: "why DPIAs matter"\n---\nGitLab is fully committed to...')
    title, body = split_frontmatter(raw)

    assert title == "Data Protection Impact Assessment (DPIA)"
    assert body.startswith("GitLab is fully committed")
    assert "description:" not in body


def test_split_frontmatter_passthrough_when_absent():
    title, body = split_frontmatter("# Heading\n\nbody")
    assert title is None and body == "# Heading\n\nbody"


def test_strip_markdown_noise():
    raw = ('{{% details summary="Law Enforcement Requests" %}}\n'
           "Click the [GitLab Privacy Center](https://privacy.gitlab.com/) option.\n"
           "![diagram](flow.png)")
    out = strip_markdown_noise(raw)

    assert "{{%" not in out and "https://" not in out and "![" not in out
    assert "GitLab Privacy Center" in out


def test_load_markdown_uses_frontmatter_title(tmp_path):
    p = tmp_path / "gitlab-privacy-gdpr.md"
    p.write_text(
        '---\ntitle: Personal Data Requests\n---\n'
        "Under various global data privacy laws, users have the right to request data.\n",
        encoding="utf-8",
    )
    doc = load_document(p)

    assert doc.title == "Personal Data Requests"
    assert doc.doc_id == "gitlab-privacy-gdpr"
    assert "title:" not in doc.text


def test_looks_like_heading():
    assert looks_like_heading("Purpose")
    assert looks_like_heading("Roles and Responsibilities")
    assert not looks_like_heading("Our policy aims to protect personal data.")
    assert not looks_like_heading("   ")

def test_strip_markdown_noise_removes_css_and_html():
    raw = (
        "<style>\n.tg td{border-color:black;border-style:solid;}\n</style>\n"
        "<p>GitLab reviews each request.</p>\n"
        ".tg {border-collapse:collapse;border-spacing:0;}\n"
        "Requests are logged."
    )
    out = strip_markdown_noise(raw)

    assert "border-collapse" not in out
    assert "border-color" not in out
    assert "<p>" not in out
    assert "GitLab reviews each request." in out
    assert "Requests are logged." in out


def test_strip_markdown_noise_removes_css_and_html():
    raw = (
        "<style>\n.tg td{border-color:black;border-style:solid;}\n</style>\n"
        "<p>GitLab reviews each request.</p>\n"
        ".tg {border-collapse:collapse;border-spacing:0;}\n"
        "Requests are logged."
    )
    out = strip_markdown_noise(raw)

    assert "border-collapse" not in out
    assert "border-color" not in out
    assert "<p>" not in out
    assert "GitLab reviews each request." in out
    assert "Requests are logged." in out


def test_strip_markdown_noise_removes_css_and_html():
    raw = (
        "<style>\n.tg td{border-color:black;border-style:solid;}\n</style>\n"
        "<p>GitLab reviews each request.</p>\n"
        ".tg {border-collapse:collapse;border-spacing:0;}\n"
        "Requests are logged."
    )
    out = strip_markdown_noise(raw)

    assert "border-collapse" not in out
    assert "border-color" not in out
    assert "<p>" not in out
    assert "GitLab reviews each request." in out
    assert "Requests are logged." in out


def test_strip_markdown_noise_keeps_table_rows():
    """Markdown tables are content. Only HTML/CSS is noise."""
    raw = "| Type | Count |\n|------|:-----:|\n| Subpoena | 12 |"
    assert strip_markdown_noise(raw) == raw