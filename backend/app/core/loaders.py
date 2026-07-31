"""Source files → canonical normalized text + page map.

The text produced here is the single source of truth for character offsets.
It is written verbatim to data/normalized/{doc_id}.txt, and every chunk's
char_start/char_end indexes into it. Nothing downstream may re-normalize.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

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


# ───────────────────────────── normalization ─────────────────────────────

def normalize(raw: str) -> str:
    """Canonicalize text. MUST be idempotent: normalize(normalize(x)) == normalize(x).

    Applied to each page's text *before* pages are concatenated, so that the
    assembled offsets remain exact. See assemble_pages().
    """
    # NFKC folds ligatures (ﬁ → fi), full-width forms, and odd PDF codepoints
    text = unicodedata.normalize("NFKC", raw)

    # unify line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # PDF extraction leaves hyphenated line breaks: "reimburse-\nment"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    # collapse horizontal whitespace runs, but never newlines
    text = re.sub(r"[ \t\u00a0]+", " ", text)

    # drop trailing horizontal whitespace on each line
    text = re.sub(r"[ \t]+\n", "\n", text)

    # 3+ newlines → one paragraph break
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def assemble_pages(page_texts: list[str]) -> tuple[str, list[PageSpan]]:
    """Normalize each page, then concatenate, recording exact offsets.

    Order matters. Normalizing *after* joining would shift every offset,
    because collapsing whitespace changes string length. Pure function so it
    can be tested without constructing a PDF.
    """
    parts: list[str] = []
    page_map: list[PageSpan] = []
    cursor = 0

    for page_number, raw in enumerate(page_texts, start=1):
        page_text = normalize(raw)
        if not page_text:
            continue                       # blank/image-only page

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


def extract_title(text: str, fallback: str) -> str:
    """First markdown H1 if present, else a tidied filename."""
    for line in text.split("\n", 40)[:40]:
        m = re.match(r"^#\s+(.{1,200})$", line.strip())
        if m:
            return m.group(1).strip()
    return fallback.replace("_", " ").replace("-", " ").strip()


# ───────────────────────────── loaders ───────────────────────────────────

def _load_pdf(path: Path) -> tuple[str, list[PageSpan]]:
    reader = PdfReader(str(path))
    return assemble_pages([(p.extract_text() or "") for p in reader.pages])


def _load_plain(path: Path) -> tuple[str, list[PageSpan]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = normalize(raw)
    page_map = [PageSpan(page=1, char_start=0, char_end=len(text))] if text else []
    return text, page_map


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
    text, page_map = _load_pdf(path) if suffix == ".pdf" else _load_plain(path)

    if not text.strip():
        raise EmptyDocumentError(
            f"{path.name}: no extractable text (scanned image? OCR is out of scope)"
        )

    return LoadedDocument(
        doc_id=slugify(path.stem),
        title=extract_title(text, fallback=path.stem),
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