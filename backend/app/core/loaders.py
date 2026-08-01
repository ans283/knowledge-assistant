"""Source files → canonical normalized text + page map.

The text produced here is the single source of truth for character offsets.
It is written verbatim to data/normalized/{doc_id}.txt, and every chunk's
char_start/char_end indexes into it.

CRITICAL ORDERING RULE: every operation that adds or removes characters must
happen in this module, before assemble_pages() records offsets. Once the
canonical text exists, its offsets are frozen. Nothing downstream may
re-normalize, strip, or trim.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

# pypdf logs "Ignoring wrong pointing object" for malformed xref tables.
# Harmless, noisy, and not part of the extracted text.
logging.getLogger("pypdf").setLevel(logging.ERROR)

SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}
PAGE_SEPARATOR = "\n\n"


class UnsupportedFormatError(ValueError):
    pass


class EmptyDocumentError(ValueError):
    pass


@dataclass(frozen=True)
class PageSpan:
    """Half-open interval [char_start, char_end) of the canonical text."""
    page: int
    char_start: int
    char_end: int


@dataclass
class LoadedDocument:
    doc_id: str
    title: str
    source_path: str
    text: str                              # canonical — do not transform further
    page_map: list[PageSpan] = field(default_factory=list)
    content_hash: str = ""

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def page_count(self) -> int:
        return len(self.page_map)

    def pages_for_span(self, start: int, end: int) -> list[int]:
        """Page numbers overlapping the half-open range [start, end)."""
        return [
            s.page for s in self.page_map
            if s.char_start < end and s.char_end > start
        ]


# ───────────────────────── markdown pre-processing ───────────────────────

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.DOTALL)
_FM_TITLE = re.compile(r'^title:\s*["\']?(.+?)["\']?\s*$', re.MULTILINE)
_SHORTCODE = re.compile(r"\{\{[%<].*?[%>]\}\}", re.DOTALL)
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")

_HTML_STYLE = re.compile(r"<style\b.*?</style>", re.DOTALL | re.IGNORECASE)
_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]{0,200}>")
_CSS_LINE = re.compile(r"^[^\n{}]{0,80}\{[^{}]*\}?;?\s*$", re.MULTILINE)


def split_frontmatter(raw: str) -> tuple[str | None, str]:
    """Return (title_from_frontmatter, body_without_frontmatter).

    GitLab handbook files are Hugo pages: a YAML block with title/description
    sits above the prose. The metadata is useful; leaving it in the body would
    pollute both embeddings and citation quotes.
    """
    m = _FRONTMATTER.match(raw)
    if not m:
        return None, raw
    title_match = _FM_TITLE.search(m.group(1))
    title = title_match.group(1).strip() if title_match else None
    return title, raw[m.end():]


def strip_markdown_noise(text: str) -> str:
    """Remove template syntax, HTML, and link targets; keep readable prose.

    The GitLab law-enforcement page embeds a <style> block for an HTML table.
    Left in, its CSS rules would be embedded as if they were policy text and
    could surface inside a citation quote.

    {{% details summary="..." %}}   → removed
    <style>td{border:1px}</style>   → removed
    ![alt](img.png)                 → removed
    [Privacy Center](https://...)   → Privacy Center
    """
    text = _SHORTCODE.sub("", text)
    text = _HTML_STYLE.sub("", text)
    text = _CSS_LINE.sub("", text)      # stray rules outside a <style> block
    text = _HTML_TAG.sub("", text)
    text = _MD_IMAGE.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    return text


# ───────────────────────── running headers/footers ───────────────────────

def _line_key(line: str) -> str:
    """Comparison key with digits masked, so 'Page 1' and 'Page 7' match."""
    return re.sub(r"\d+", "#", line.strip().lower())


def strip_repeated_lines(
    page_texts: list[str],
    ratio: float = 0.6,
    min_pages: int = 3,
    edge_lines: int = 3,
) -> list[str]:
    """Drop short lines that recur near the top or bottom of most pages.

    Two constraints keep this from eating real content:
      * position — only the first and last `edge_lines` non-blank lines of a
        page are candidates, since that is where headers and footers live;
      * length — lines over 80 characters are never candidates, so a repeated
        boilerplate *paragraph* is never silently deleted.

    Detection needs every page at once, so this runs as a pre-pass before
    per-page normalization.
    """
    if len(page_texts) < min_pages:
        return page_texts

    def candidates(lines: list[str]) -> set[int]:
        filled = [i for i, l in enumerate(lines) if l.strip()]
        edges = filled[:edge_lines] + filled[-edge_lines:]
        return {i for i in edges if len(lines[i].strip()) <= 80}

    counts: Counter[str] = Counter()
    for text in page_texts:
        lines = text.split("\n")
        counts.update({_line_key(lines[i]) for i in candidates(lines)})

    threshold = max(2, int(len(page_texts) * ratio))
    repeated = {k for k, c in counts.items() if c >= threshold}
    if not repeated:
        return page_texts

    cleaned = []
    for text in page_texts:
        lines = text.split("\n")
        drop = {i for i in candidates(lines) if _line_key(lines[i]) in repeated}
        cleaned.append("\n".join(l for i, l in enumerate(lines) if i not in drop))
    return cleaned

# ───────────────────────────── normalization ─────────────────────────────

_BULLET = re.compile(r"^(?:[-*•▪‣]|\(?\d{1,2}[.)]|[a-z]\))\s+")
_CONTINUES = (",", ";", "-", "—", "(")


def unwrap_soft_linebreaks(text: str) -> str:
    """Rejoin lines broken by visual wrapping, preserving real breaks.

    pypdf's layout mode keeps the PDF's line wrapping as hard newlines. A line
    is treated as a continuation when it starts lowercase and the previous line
    did not end a sentence. Headings ('Purpose' / 'Our Data...') and paragraph
    starts ('organization.' / 'This policy...') are left alone, as are bullets.
    """
    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        prev = out[-1] if out else ""
        is_continuation = (
            bool(prev)
            and bool(stripped)
            and not _BULLET.match(stripped)
            and (stripped[0].islower() or stripped[0] in ")]")
            and (not prev.endswith((".", "!", "?", ":")) or prev.endswith(_CONTINUES))
        )
        if is_continuation:
            out[-1] = f"{prev} {stripped}"
        else:
            out.append(stripped)
    return "\n".join(out)


def normalize(raw: str) -> str:
    """Canonicalize text. MUST be idempotent: normalize(normalize(x)) == x.

    Applied per page *before* pages are concatenated, so assembled offsets
    stay exact. See assemble_pages().
    """
    # NFKC folds ligatures (ﬁ → fi), full-width forms, odd PDF codepoints
    text = unicodedata.normalize("NFKC", raw)

    # unify line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # layout-mode extraction pads with leading spaces to mimic centring
    text = "\n".join(line.strip() for line in text.split("\n"))

    # PDF line breaks split words: "reimburse-\nment" → "reimbursement"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    # rejoin visually wrapped lines — must run after de-hyphenation
    text = unwrap_soft_linebreaks(text)

    # collapse horizontal whitespace runs, never newlines
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)

    # 3+ newlines → one paragraph break
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def assemble_pages(page_texts: list[str]) -> tuple[str, list[PageSpan]]:
    """Normalize each page, then concatenate, recording exact offsets.

    Order matters. Normalizing *after* joining would shift every offset,
    because collapsing whitespace changes string length.
    """
    parts: list[str] = []
    page_map: list[PageSpan] = []
    cursor = 0

    for page_number, raw in enumerate(page_texts, start=1):
        page_text = normalize(raw)
        if not page_text:
            continue                       # blank or image-only page

        if parts:
            parts.append(PAGE_SEPARATOR)
            cursor += len(PAGE_SEPARATOR)

        start = cursor
        parts.append(page_text)
        cursor += len(page_text)
        page_map.append(PageSpan(page=page_number, char_start=start, char_end=cursor))

    text = "".join(parts)
    assert len(text) == cursor, "offset bookkeeping drifted"
    return text, page_map


# ───────────────────────────── identifiers ───────────────────────────────

def slugify(value: str) -> str:
    """Filesystem- and URL-safe stable id. Same input always yields same id."""
    s = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    s = re.sub(r"-{2,}", "-", s)
    if len(s) < 3:
        s = f"doc-{s}".rstrip("-")
    return s[:120]


def looks_like_heading(line: str) -> bool:
    """A short line with no terminal punctuation — a title or section heading.

    Used for title detection here, and reused by the Step 4 chunker to find
    section boundaries in PDF text.
    """
    s = line.strip()
    return bool(s) and len(s) <= 80 and not s.endswith((".", ",", ";", ":", "!", "?"))


def extract_title(text: str, fallback: str, frontmatter_title: str | None = None) -> str:
    """Precedence: YAML frontmatter → markdown H1 → first heading-like line."""
    if frontmatter_title:
        return frontmatter_title

    lines = text.split("\n")[:40]
    for line in lines:
        m = re.match(r"^#\s+(.{1,200})$", line.strip())
        if m:
            return m.group(1).strip()

    for line in lines:
        if looks_like_heading(line):
            return line.strip()

    return fallback.replace("_", " ").replace("-", " ").strip()


# ───────────────────────────── loaders ──────────────────────────────────

def _load_pdf(path: Path) -> tuple[str, list[PageSpan]]:
    """layout mode positions characters by coordinates rather than emitting
    them in content-stream order, which is what preserves headings as their
    own lines. Default mode flattens them into the running text."""
    reader = PdfReader(str(path))
    pages = [
        (p.extract_text(extraction_mode="layout") or "") for p in reader.pages
    ]
    return assemble_pages(strip_repeated_lines(pages))


def _load_plain(path: Path, is_markdown: bool) -> tuple[str, list[PageSpan], str | None]:
    raw = path.read_text(encoding="utf-8", errors="replace")

    fm_title = None
    if is_markdown:
        fm_title, raw = split_frontmatter(raw)
        raw = strip_markdown_noise(raw)

    text = normalize(raw)
    page_map = [PageSpan(page=1, char_start=0, char_end=len(text))] if text else []
    return text, page_map, fm_title


def load_document(path: str | Path) -> LoadedDocument:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedFormatError(
            f"{path.name}: {suffix or 'no suffix'} not in {sorted(SUPPORTED_SUFFIXES)}"
        )

    content_hash = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    if suffix == ".pdf":
        text, page_map = _load_pdf(path)
        fm_title = None
    else:
        text, page_map, fm_title = _load_plain(
            path, is_markdown=suffix in {".md", ".markdown"}
        )

    if not text.strip():
        raise EmptyDocumentError(
            f"{path.name}: no extractable text (scanned image? OCR is out of scope)"
        )

    return LoadedDocument(
        doc_id=slugify(path.stem),
        title=extract_title(text, fallback=path.stem, frontmatter_title=fm_title),
        source_path=str(path),
        text=text,
        page_map=page_map,
        content_hash=content_hash,
    )


def discover_corpus(directory: str | Path) -> list[Path]:
    """Supported files in the corpus dir, sorted for deterministic ingest order."""
    return sorted(
        p for p in Path(directory).rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )