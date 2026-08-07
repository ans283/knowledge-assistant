# Assumptions and limitations

What this system deliberately does not do, and how I would extend it. Everything
here is a scope decision, not an oversight.

## Corpus and ingestion

**No OCR.** A scanned PDF with no text layer raises `EmptyDocumentError` at
ingest with a message saying so, rather than indexing an empty document.
*Extension:* Tesseract or a hosted OCR pass in `_load_pdf`, gated on
`extract_text()` returning nothing for a page.

**Heading detection is heuristic.** PDFs have no heading markup, so a short line
with no terminal punctuation and eight words or fewer is treated as a section
heading. Markdown rules and list items are explicitly excluded. This misfires
both ways: a short unpunctuated sentence can become a spurious section, and a
long heading is missed.
*Extension:* font-size and weight data from `pdfplumber` would make this
structural rather than statistical.

**Running-header stripping is positional and proportional.** A short line
recurring near the top or bottom of ≥60% of pages is dropped. A recurring
mid-page label survives (by design — position is the guard), and a header on
fewer than 60% of pages is kept. Tunable via `ratio` and `edge_lines`.

**Oversized tables split without repeating headers.** A table beyond 600
estimated tokens splits on row boundaries, and continuation chunks lack column
labels. Repeating the header would break the invariant that
`chunk.text == doc.text[char_start:char_end]`, which every citation depends on.
*Extension:* a separate `display_text` field carrying the header, alongside the
verbatim `text` used for offsets.

**Token counts are estimated at ~4 characters per token.** Chunk sizing is a soft
target and the embedding model truncates at its own limit, so exactness buys
nothing here — and loading a tokenizer at ingest would cost more than it saves.

## Storage and scale

**Chroma runs as an embedded single-process store.** `PersistentClient` is
appropriate to a few hundred thousand vectors. This corpus has 174.
*Extension:* Chroma in server mode, or pgvector on Postgres — which would also
let hybrid search become a single SQL query.

**BM25 is in-memory and rebuilt at startup.** 174 chunks build in milliseconds,
and a second persisted index is one more thing to keep consistent with the vector
store. Startup cost grows linearly with corpus size.
*Extension:* Postgres full-text search, which removes the separate index entirely.

**Normalized text is read whole for each citation.** Fine at ~45k characters per
document. A gigabyte-scale document would need a proper byte-offset index — note
that offsets here are Unicode code-point indices, not byte offsets, so a naive
byte seek would drift past every non-ASCII character.

**Single-tenant, no authentication.** There is no user model, no per-user corpus
scoping, and no rate limiting. `doc_id` is validated against the registry before
any filesystem access, so path traversal through `/documents/{id}/text` is not
possible — but that is input validation, not authorization.

## Retrieval and generation

**Abstention thresholds are corpus-calibrated.** `min_rerank_score = -3.0` sits
in a measured 15-logit gap on this corpus. A different corpus or reranker would
need recalibration, and
`test_abstention_threshold_separates_in_scope_from_out_of_scope` fails if the
separation collapses — deliberately, so the constant cannot silently go stale.

**Dense-only and sparse-only modes effectively cannot abstain.** They are exposed
for comparison, not for production. Sparse mode has no distance signal at all;
dense separation is too narrow to threshold reliably. This is documented in
`EVALUATION.md` as a measured result rather than hidden.

**No conversational memory.** Each query is independent; follow-ups like "what
about contractors?" are not resolved against prior turns.
*Extension:* a query-rewriting step using the cheap model to expand a follow-up
into a standalone question before retrieval.

**Injection detection is pattern-based.** The regexes catch known directive
phrasings and will miss novel ones. It is a signal recorded in the trace, layered
behind the real defence: retrieved text is delimited and declared as data, and
answers require verifiable citations regardless of what the model was told.
*Extension:* a classifier pass at ingest, quarantining flagged documents for
review rather than only flagging at query time.

**Streaming is deferred.** Citation verification needs a complete response object,
so a streamed path would emit answer text incrementally and verified citations as
a terminal event. Same pipeline, different transport — `answer_question` already
returns a complete verified object either way. Left out to keep the initial
implementation debuggable.

**LLM faithfulness scoring is optional.** It requires a judge API key and a judge
from a different model family than the generator, to avoid self-preference bias.
Every other metric runs offline.

## Evaluation

**The golden set is 30 items and hand-verified.** Small enough that every expected
answer was checked against the corpus; large enough to distinguish the four
retrieval modes. It is keyed on `doc_id` and expected phrases rather than chunk
IDs, so it survives re-chunking — chunk IDs changed twice during development.

**It is also somewhat easy.** 15 answerable questions over 174 chunks in a tightly
scoped corpus produces hit@5 of 100% for three of four modes. Only one item tests
exact-identifier lookup, which is where hybrid retrieval most clearly beats dense.
*Extension:* more identifier lookups (`SDV-04`, `PRV-11`) and more questions whose
answers span two documents, to give fusion a case where it demonstrably earns its
place rather than being justified by argument.