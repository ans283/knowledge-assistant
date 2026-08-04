# Knowledge Assistant

A grounded question-answering system over a document corpus. Answers cite the
exact passage they came from — and when the documents don't contain the answer,
the system says so instead of guessing.

**Status:** backend in progress. Ingestion, retrieval, and generation complete;
guardrails, API, evaluation, and UI to follow.

## Design notes so far

- **Span-level citations.** Every chunk stores character offsets into a canonical
  normalized text, so a citation resolves to an exact range a reader can verify.
- **Grounding enforced by type.** `QueryResponse` cannot be constructed with an
  answer that has no citations — abstention is a valid state, ungrounded is not.
- **Four retrieval modes.** Dense, BM25 sparse, RRF fusion, and cross-encoder
  reranking, selectable per query for comparison.
- **Runs offline.** Local embeddings and a `MockProvider` mean the full test
  suite runs with no API key, no network, and no cost.

## Quickstart

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest -v
python -m uvicorn app.main:app --reload
```

Then `POST /ingest` with `{"force": true}` to index the corpus in `data/corpus/`,
and open `http://127.0.0.1:8000/docs`.

Corpus sources and licences: `data/corpus/SOURCES.md`.