"""Canonical text → chunks with exact character offsets.

Every chunk carries char_start/char_end indexing into the document's canonical
text. The invariant, asserted in build_chunks and tested directly:

    doc.text[chunk.meta.char_start:chunk.meta.char_end] == chunk.text

Two text fields per chunk, deliberately:
  * text       — shown to the user and quoted in citations, verbatim
  * embed_text — section_path prepended, used only for the vector

The corpus contains five SANS policies whose 'Purpose' sections are nearly
identical boilerplate. Bare section text embeds them to almost the same point;
prepending the section path separates them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.loaders import LoadedDocument, looks_like_heading
from app.core.schemas import Chunk, ChunkMeta

TARGET_TOKENS = 250
OVERLAP_TOKENS = 40
MIN_CHUNK_TOKENS = 20
MAX_TOKENS_HARD = 600          # tables may exceed TARGET; never exceed this
MIN_TAIL_TOKENS = 60           # below this, a trailing fragment merges backwards
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def estimate_tokens(text: str) -> int:
    """~4 characters per token for English prose.

    A real tokenizer would be exact, but chunk sizing is a soft target and this
    avoids loading one at ingest time. The embedding model truncates at its own
    limit regardless, so the estimate only needs to be in the right range.
    """
    return max(1, len(text) // 4)


@dataclass
class Block:
    """A run of lines sharing one section path, with exact offsets."""
    char_start: int
    char_end: int
    section_path: str
    is_table: bool = False


# ────────────────────────── structure detection ──────────────────────────

def _heading_level(line: str) -> tuple[int, str] | None:
    """(level, title) if the line is a heading, else None.

    Markdown '##' wins when present. Otherwise a short unpunctuated line is
    treated as a level-1 heading — this is the SANS PDF case, where 'Purpose'
    and 'Scope' survive as standalone lines after layout extraction.
    """
    stripped = line.strip()
    if not stripped:
        return None

    m = _MD_HEADING.match(stripped)
    if m:
        return len(m.group(1)), m.group(2).strip()

    if looks_like_heading(stripped) and len(stripped.split()) <= 8:
        return 1, stripped

    return None


def _section_path(stack: list[tuple[int, str]]) -> str:
    return " > ".join(title for _, title in stack)


def split_into_blocks(text: str) -> list[Block]:
    """Walk the text once, tracking a heading stack and exact offsets.

    Offsets are accumulated by adding len(line) + 1 per line, which is exact
    because the canonical text uses '\\n' only — guaranteed by normalize().
    """
    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []

    cursor = 0
    block_start: int | None = None
    in_table = False
    table_start: int | None = None

    def close(end: int, is_table: bool = False) -> None:
        nonlocal block_start
        if block_start is not None and end > block_start:
            blocks.append(Block(block_start, end, _section_path(stack), is_table))
        block_start = None

    for line in text.split("\n"):
        line_start = cursor
        line_end = cursor + len(line)
        cursor = line_end + 1                    # +1 for the newline

        is_table_row = bool(_TABLE_ROW.match(line))

        # a table ends when a non-table line appears
        if in_table and not is_table_row:
            close(table_start_end := line_start - 1, is_table=True)
            in_table = False

        if is_table_row:
            if not in_table:
                close(line_start)                # flush prose before the table
                in_table = True
                block_start = line_start
            continue

        heading = _heading_level(line)
        if heading is not None:
            close(line_start)
            level, title = heading
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            continue

        if line.strip() and block_start is None:
            block_start = line_start

    close(len(text), is_table=in_table)
    return blocks

def _split_table_rows(text: str, block: Block) -> list[tuple[int, int]]:
    """Split an oversized table on row boundaries.

    Tables contain no sentence punctuation, so the prose splitter returns them
    whole. Rows are the only safe boundary.

    The header row is deliberately NOT repeated into continuation chunks:
    doing so would make chunk.text differ from doc.text[char_start:char_end]
    and break the offset invariant that citations depend on. A continuation
    chunk without column headers is the lesser cost.
    """
    spans: list[tuple[int, int]] = []
    start = block.char_start
    cursor = block.char_start

    for line in text[block.char_start:block.char_end].split("\n"):
        line_end = min(cursor + len(line), block.char_end)
        if line_end > start and estimate_tokens(text[start:line_end]) >= TARGET_TOKENS:
            spans.append((start, line_end))
            start = min(line_end + 1, block.char_end)
        cursor = line_end + 1

    if start < block.char_end:
        spans.append((start, block.char_end))

    return spans or [(block.char_start, block.char_end)]


def _overlap_start(text: str, span: tuple[int, int], boundaries: list[int]) -> int:
    """Start the next chunk at the sentence boundary nearest the ideal overlap.

    Snapping to a word boundary is not enough: a chunk opening 'for data
    privacy. PRV-06 Ensure...' carries a sentence fragment that belongs to the
    previous chunk. It embeds poorly and reads badly as a citation quote.
    """
    chunk_start, chunk_end = span
    ideal = chunk_end - OVERLAP_TOKENS * 4

    inside = [b for b in boundaries if chunk_start < b < chunk_end]
    at_or_after = [b for b in inside if b >= ideal]

    if at_or_after:
        start = min(at_or_after)
    elif inside:
        start = max(inside)
    else:
        start = max(chunk_start, ideal)

    return _snap_forward(text, start, chunk_end)
# ──────────────────────────── chunk assembly ─────────────────────────────


def _pack(text: str, block: Block) -> list[tuple[int, int]]:
    """Split one block into (start, end) spans at sentence boundaries.

    Tables are never split — a half table is useless — but a table beyond the
    hard cap is force-split rather than allowed to blow the embedding limit.
    """
    body = text[block.char_start:block.char_end]

    if block.is_table:
        if estimate_tokens(body) <= MAX_TOKENS_HARD:
            return [(block.char_start, block.char_end)]
        return _split_table_rows(text, block)
    if estimate_tokens(body) <= TARGET_TOKENS:
        return [(block.char_start, block.char_end)]

    # sentence ends: '.', '!', '?' followed by whitespace
    boundaries = [
        block.char_start + m.end()
        for m in re.finditer(r"[.!?](?=\s|$)", body)
    ]
    boundaries.append(block.char_end)

    spans: list[tuple[int, int]] = []
    start = block.char_start
    last = start

    for boundary in boundaries:
        if estimate_tokens(text[start:boundary]) >= TARGET_TOKENS:
            spans.append((start, boundary))
            start = _overlap_start(text, spans[-1], boundaries)
        last = boundary

    if last > start:
        if not spans or estimate_tokens(text[start:last]) >= MIN_TAIL_TOKENS:
            spans.append((start, last))
        else:
            # fragment too small to answer anything on its own — absorb it
            prev_start, _ = spans[-1]
            spans[-1] = (prev_start, last)

    return spans or [(block.char_start, block.char_end)]


def _snap_forward(text: str, start: int, limit: int) -> int:
    """Move start to the next word boundary so overlap never cuts mid-word."""
    while start < limit and not text[start].isspace():
        start += 1
    while start < limit and text[start].isspace():
        start += 1
    return start


def build_chunks(doc: LoadedDocument) -> list[Chunk]:
    """Canonical text → chunks. The offset invariant is asserted, not hoped for."""
    chunks: list[Chunk] = []
    index = 0

    for block in split_into_blocks(doc.text):
        for start, end in _pack(doc.text, block):
            body = doc.text[start:end].strip()
            if not body or estimate_tokens(body) < MIN_CHUNK_TOKENS:
                continue

            # .strip() may have moved the boundaries — realign so the invariant holds
            actual_start = start + (len(doc.text[start:end]) - len(doc.text[start:end].lstrip()))
            actual_end = actual_start + len(body)

            prefix = f"{doc.title} > {block.section_path}" if block.section_path else doc.title

            meta = ChunkMeta(
                doc_id=doc.doc_id,
                doc_title=doc.title,
                section_path=block.section_path,
                char_start=actual_start,
                char_end=actual_end,
                chunk_index=index,
                token_count=estimate_tokens(body),
                content_hash=doc.content_hash,
                pages=doc.pages_for_span(actual_start, actual_end),
            )

            chunks.append(Chunk(
                chunk_id=f"{doc.doc_id}::{index:04d}",
                text=body,
                embed_text=f"{prefix}\n\n{body}",
                meta=meta,
            ))
            index += 1

    for c in chunks:
        assert doc.text[c.meta.char_start:c.meta.char_end] == c.text, (
            f"offset drift in {c.chunk_id}"
        )

    return chunks