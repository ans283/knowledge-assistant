# Knowledge Assistant

Grounded question answering over a document corpus. Every claim is traced to the
exact passage it came from — and when the documents don't contain the answer, the
system says so and names what's missing, rather than guessing.

Built on a corpus of privacy and data-protection governance documents: SANS
policy templates plus GitLab's public handbook (12 documents, 174 indexed
passages). Sources and licences in [`data/corpus/SOURCES.md`](data/corpus/SOURCES.md).

## The guarantee

An ungrounded answer is not a bug in this system — it is unrepresentable.

- The model may emit only two fields per citation: a `chunk_id` and a verbatim
  `quote`. Both are checkable. Character offsets, document titles, and page
  numbers are looked up server-side and never come from the model.
- Each quote is located in the chunk it cites. Anything that can't be found is
  discarded.
- `QueryResponse` has a validator that rejects construction of an answer with no
  citations. If verification leaves nothing, the only remaining path is abstention.

This holds regardless of which model generates the answer. A test
(`test_fabricated_answer_is_forced_to_abstain`) feeds in a provider that returns
a fluent, confident, entirely invented answer and asserts it cannot get through.

## Measured results

Full detail in [`EVALUATION.md`](EVALUATION.md), interpretation in
[`EVALUATION_NOTES.md`](EVALUATION_NOTES.md). Over a 30-item golden set
(15 answerable, 10 deliberately out of scope, 5 adversarial):

| Mode | hit@5 | MRR | Abstention F1 | Injection resistance |
|---|---|---|---|---|
| dense | 100% | **1.000** | 0% | 100% |
| sparse | 93% | 0.856 | 0% | 100% |
| hybrid (RRF) | 100% | 0.913 | 0% | 100% |
| **hybrid + rerank** | 100% | 0.967 | **100%** | 100% |

The interesting result is the abstention column, and it is why `hybrid_rerank` is
the production default — not ranking quality, where dense-only actually scored
marginally better on this corpus. See "Why reranking" below.

## Quickstart

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest -v                          # 170 tests, no API key needed
python -m uvicorn app.main:app --reload
```

Index the corpus, then open http://127.0.0.1:8000/docs:

```bash
curl -X POST http://127.0.0.1:8000/ingest \
  -H 'Content-Type: application/json' -d '{"force": true}'
```

On Windows PowerShell, use `Invoke-RestMethod` instead — `curl` quoting differs:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/ingest -Method Post `
  -ContentType "application/json" -Body '{"force": true}'
```

Frontend:

```bash
cd frontend
npm install
npm run dev                        # http://localhost:5173
```

**No API key is required for any of this.** Embeddings and reranking run locally,
and the default generator is a deterministic `MockProvider`. Set
`ANTHROPIC_API_KEY` and `LLM_PROVIDER=anthropic` in `backend/.env` for real
answer synthesis; retrieval, abstention, and citation verification are unaffected.

## Architecture

```
                    corpus files (.pdf .md .txt)
                                │
                  ┌─────────────▼─────────────┐
                  │  loaders.py               │  layout-mode PDF extraction,
                  │  canonical text + page map│  line unwrapping, header
                  └─────────────┬─────────────┘  stripping, frontmatter/HTML
                                │
            ┌───────────────────┴───────────────────┐
            ▼                                       ▼
  data/normalized/{id}.txt                    chunking.py
  (offset source of truth)            structure-aware split, section
            │                          paths, tables kept whole
            │                                       │
            │                  ┌────────────────────┴────────────┐
            │                  ▼                                 ▼
            │           embeddings.py                       registry.db
            │      bge-base-en-v1.5, local             doc metadata, page map
            │                  │
            │                  ▼
            │            ChromaDB (cosine)  ◄──────  BM25 (in-memory)
            │                  │                          │
            │                  └──────────┬───────────────┘
            │                             ▼
            │                       retriever.py
            │                RRF fusion, cross-encoder rerank
            │                             │
            │                             ▼
            │                      guardrails.py
            │              abstention gate (pre-LLM), injection scan
            │                             │
            │                             ▼
            │                       generator.py
            │            strict JSON contract, provider-swappable
            │                             │
            │                             ▼
            │                      guardrails.py
            └────────────────────►  quote located in cited chunk,
                                    offsets computed server-side
                                             │
                                             ▼
                                  QueryResponse: grounded or abstained
```

## Design decisions

**Character offsets are the spine.** Every chunk stores `char_start`/`char_end`
indexing into a canonical normalized text written to disk at ingest. A citation
resolves to an exact range, so the frontend highlights the quoted sentence with a
single string slice — no client-side search, no fuzzy matching. This works only
because the offset invariant is asserted at three layers: in `assemble_pages`, in
`build_chunks`, and again end-to-end in `test_offsets_survive_the_full_pipeline`.

Consequence: every character-removing transformation happens in the loader,
before offsets are recorded. Nothing downstream may re-normalize.

**Chunking was tuned against the real corpus, not a plan.** `pypdf`'s default
extraction flattened section headings into running text, so the loader uses
layout mode and unwraps the visual line breaks that mode introduces. Inspecting
real chunk output then surfaced two further problems invisible to unit tests:
chunks opening mid-sentence, and orphan 39-token fragments. Both are now fixed
and pinned by tests.

**Section paths disambiguate near-identical boilerplate.** Five SANS policies
share almost the same "Purpose" prose. Embedding the bare text places them at
nearly the same point in vector space. Each chunk therefore has two text fields:
`text` (verbatim, used for display and citation) and `embed_text` (with
`Document > Section` prepended, used only for the vector). Verified in
`test_boilerplate_sections_get_distinct_embed_text`, and again in vector space in
`test_section_path_prefix_separates_boilerplate`.

**Hybrid retrieval fuses on ranks, not scores.** Cosine distance is bounded 0–1
and lower-is-better; BM25 is unbounded and higher-is-better. Reciprocal Rank
Fusion (k=60, implemented directly) sidesteps scale reconciliation entirely. The
two arms fail in complementary ways: dense finds paraphrase with no shared
vocabulary, sparse finds exact identifiers like `PRV-06`.

**Why reranking — the abstention result.** A bi-encoder embeds query and passage
separately, so it can only answer "what's nearest?", which is always answerable.
A cross-encoder reads them together and answers "does this passage address this
question?" Measured on this corpus:

| Signal | In scope | Out of scope | Separation |
|---|---|---|---|
| cosine distance (0–1) | 0.30 | 0.51 – 0.59 | ~0.22 |
| cross-encoder logit (−11…+11) | +4.6 – +7.4 | −10.4 – −11.3 | ~15 |

Both carry signal, but dense separation is under a quarter of its range — any
threshold is brittle to phrasing. `min_rerank_score` is set to **−3.0**, roughly
centred in a 15-point gap. Calibrated, not guessed, and pinned by
`test_abstention_threshold_separates_in_scope_from_out_of_scope` so a model or
corpus change that collapses the gap fails the suite.

**Ingestion is idempotent, and deletes before inserting.** A content hash skips
unchanged files: a forced re-index takes ~72s, an unchanged re-run 2.6s. Re-index
of an edited file deletes its old vectors first — overwriting by chunk id would
strand surplus chunks from a shrunk document, which would keep matching queries
and cite text no longer present. Nothing would error.
(`test_shrinking_a_document_leaves_no_orphan_vectors`)

**Abstention returns 200.** "The corpus doesn't cover dental benefits" is a
correct, successfully computed answer. HTTP status describes transport; a domain
outcome belongs in the body. The UI renders it as a distinct card that names the
gap, not as an error.

## API

| Endpoint | Purpose |
|---|---|
| `POST /ingest` | Index the corpus. `{"force": true}` to re-embed. |
| `GET /documents` | Registry listing with chunk and character counts. |
| `POST /query` | Answer a question. `mode`, `top_k`, `doc_ids` optional. |
| `GET /documents/{id}/text` | Canonical text for the citation viewer. |
| `GET /documents/{id}/chunks` | Chunk boundaries — a debugging view. |
| `GET /health` | Vector and sparse index sizes, active provider. |

Full schema at `/docs`.

## Testing

170 tests, all offline. Run `pytest -v`; `pytest -m "not slow"` skips the ones
that load models.

The suite is organized around invariants rather than coverage. A few worth
reading first:

| Test | What it protects |
|---|---|
| `test_grounded_answer_requires_a_citation` | ungrounded answers are unconstructable |
| `test_fabricated_answer_is_forced_to_abstain` | a confident invented answer cannot get through |
| `test_offsets_survive_the_full_pipeline` | citations resolve in the text on disk |
| `test_out_of_scope_question_abstains_without_calling_the_llm` | uses a provider that raises if called |
| `test_normalize_is_idempotent` | re-processing cannot shift offsets |
| `test_cosine_space_is_active` | the distance metric the thresholds assume |

Evaluation is separate and regenerable: `python -m eval.run_eval` rewrites
`EVALUATION.md`. `--mode` compares retrieval strategies; `--judge` adds LLM
faithfulness scoring if a judge key is configured.

## Stack

FastAPI · Pydantic v2 · ChromaDB 1.5 · sentence-transformers
(`bge-base-en-v1.5`, `ms-marco-MiniLM-L-6-v2`) · rank-bm25 · SQLite ·
React 18 + Vite

Scope boundaries and how I'd extend past them: [`ASSUMPTIONS.md`](ASSUMPTIONS.md).