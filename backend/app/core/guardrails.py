"""Verification layer: what the model claims vs. what the documents contain.

LLM output is untrusted input. It arrives from outside the trust boundary and
is validated before it becomes part of a response — the same posture you would
take toward a web form submission.

The model is only permitted to emit two fields, both verifiable:
  * chunk_id — was it in the retrieved set? O(1) check.
  * quote    — does that text appear in that chunk? substring/fuzzy check.

Everything else in a Citation (offsets, titles, pages) is looked up server-side
from the retrieved chunk. A field the model can invent undetectably is a field
the model does not own.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from app.core.schemas import (
    Citation, Confidence, LLMAnswer, RetrievalMode, RetrievedChunk,
)

FUZZY_THRESHOLD = 88.0        # 0-100; tolerates minor whitespace/punctuation drift
MIN_QUOTE_CHARS = 12          # shorter spans match by accident

# Directive patterns that should never appear as document *content*.
_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|rules?)",
        r"disregard\s+(?:the\s+)?(?:above|previous|prior|system)",
        r"you\s+are\s+now\s+(?:a|an|in)\b",
        r"new\s+(?:instructions?|system\s+prompt|rules?)\s*[:\-]",
        r"</?(?:system|instructions?|excerpts?)>",
        r"do\s+not\s+cite\b",
        r"respond\s+only\s+with\b",
    )
]


# ───────────────────── whitespace-tolerant span location ──────────────────

def _collapse_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to single spaces, keeping an index back to the
    original string.

    The model's quote will differ from the source in whitespace — it retypes
    rather than copies bytes. Matching on collapsed text tolerates that, and
    the index map converts the match position back to an exact offset in the
    original, so citation highlighting stays character-precise.
    """
    out: list[str] = []
    index_map: list[int] = []
    prev_space = False

    for i, ch in enumerate(text):
        if ch.isspace():
            if not prev_space and out:
                out.append(" ")
                index_map.append(i)
            prev_space = True
        else:
            out.append(ch)
            index_map.append(i)
            prev_space = False

    return "".join(out), index_map


def locate_quote(quote: str, chunk_text: str) -> tuple[int, int] | None:
    """Find a quote inside a chunk. Returns (start, end) offsets relative to
    the chunk, or None when the quote is not really there.

    Three passes, cheapest first: exact, whitespace-normalized, then fuzzy.
    """
    quote = quote.strip()
    if len(quote) < MIN_QUOTE_CHARS:
        return None

    # 1. exact
    position = chunk_text.find(quote)
    if position != -1:
        return position, position + len(quote)

    # 2. whitespace-normalized
    haystack, index_map = _collapse_with_map(chunk_text)
    needle, _ = _collapse_with_map(quote)

    position = haystack.find(needle)
    if position != -1 and needle:
        return index_map[position], index_map[position + len(needle) - 1] + 1

    # 3. fuzzy — tolerates a changed comma or a dropped article, nothing more
    alignment = fuzz.partial_ratio_alignment(needle, haystack)
    if alignment is None or alignment.score < FUZZY_THRESHOLD:
        return None

    start, end = alignment.dest_start, alignment.dest_end
    if start >= end or end > len(index_map):
        return None

    return index_map[start], index_map[end - 1] + 1


# ───────────────────────── citation verification ─────────────────────────

def verify_citations(
    answer: LLMAnswer, retrieved: list[RetrievedChunk]
) -> tuple[list[Citation], int]:
    """Turn the model's claims into verified citations. Returns (kept, dropped).

    A citation survives only if its chunk was actually retrieved AND its quote
    is actually present in that chunk. Offsets are computed by adding the
    quote's position within the chunk to the chunk's own document offset — so
    the citation highlights the exact sentence, not the whole chunk.
    """
    by_id = {c.chunk_id: c for c in retrieved}
    kept: list[Citation] = []
    dropped = 0
    seen: set[tuple[str, int]] = set()

    for claim in answer.citations:
        chunk = by_id.get(claim.chunk_id)
        if chunk is None:
            dropped += 1                      # cited a chunk we never retrieved
            continue

        span = locate_quote(claim.quote, chunk.text)
        if span is None:
            dropped += 1                      # quote is not in that chunk
            continue

        quote_start, quote_end = span
        char_start = chunk.meta.char_start + quote_start
        char_end = chunk.meta.char_start + quote_end

        key = (claim.chunk_id, char_start)
        if key in seen:
            continue                          # same span cited twice
        seen.add(key)

        kept.append(Citation(
            chunk_id=chunk.chunk_id,
            doc_id=chunk.meta.doc_id,
            doc_title=chunk.meta.doc_title,
            section_path=chunk.meta.section_path,
            quote=chunk.text[quote_start:quote_end],   # source text, not the model's
            char_start=char_start,
            char_end=char_end,
            pages=chunk.meta.pages,
        ))

    return kept, dropped


# ───────────────────────── the abstention gate ───────────────────────────

def retrieval_is_too_weak(
    chunks: list[RetrievedChunk],
    mode: RetrievalMode,
    min_rerank_score: float,
    max_dense_distance: float,
) -> str | None:
    """Decide before generation whether the corpus plausibly contains an answer.

    Returns a reason string to abstain, or None to proceed. Runs before the LLM
    is called: when the corpus has nothing relevant, the cheapest and most
    reliable response is to say so without spending a token on it.

    Thresholds differ by mode because the scales differ. Cross-encoder scores
    are raw logits (roughly -11..+11, higher better); cosine distance is
    bounded 0..1 (lower better). One threshold cannot serve both.
    """
    if not chunks:
        return "No document in the corpus matched this question."

    if mode is RetrievalMode.HYBRID_RERANK:
        top = chunks[0].rerank_score
        if top is not None and top < min_rerank_score:
            return (
                "The corpus contains no passage that directly addresses this "
                "question; the closest matches were only loosely related."
            )
        return None

    distances = [c.dense_distance for c in chunks if c.dense_distance is not None]
    if distances and min(distances) > max_dense_distance:
        return (
            "The corpus contains no passage semantically close to this question."
        )
    return None


# ───────────────────────── injection scanning ────────────────────────────

def scan_for_injection(chunks: list[RetrievedChunk]) -> list[str]:
    """Flag retrieved chunks containing directive language.

    Indirect prompt injection: an attacker who can influence a source document
    plants instructions there, and retrieval delivers them into the prompt.
    The system prompt declares excerpt text to be data, but that is a request
    to the model. This detection is a signal recorded in the trace and asserted
    by the eval harness — defence in depth, not a replacement for it.
    """
    flagged: list[str] = []
    for chunk in chunks:
        if any(pattern.search(chunk.text) for pattern in _INJECTION_PATTERNS):
            flagged.append(chunk.chunk_id)
    return flagged


def confidence_after_verification(
    answer: LLMAnswer, kept: list[Citation], dropped: int
) -> Confidence:
    """Downgrade confidence when the model's own claims did not hold up."""
    if dropped and not kept:
        return Confidence.LOW
    if dropped:
        return Confidence.LOW if len(kept) < dropped else Confidence.MEDIUM
    return answer.confidence