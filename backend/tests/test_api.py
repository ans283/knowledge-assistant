import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.slow

client = TestClient(app)


# ── request validation, no corpus needed ─────────────────────────────────

def test_query_rejects_too_short_question():
    response = client.post("/query", json={"question": "hi"})
    assert response.status_code == 422


def test_query_rejects_out_of_range_top_k():
    response = client.post("/query", json={"question": "valid question", "top_k": 99})
    assert response.status_code == 422


def test_query_rejects_unknown_mode():
    response = client.post(
        "/query", json={"question": "valid question", "mode": "telepathy"}
    )
    assert response.status_code == 422


def test_unknown_document_returns_404():
    assert client.get("/documents/does-not-exist/text").status_code == 404
    assert client.get("/documents/does-not-exist/chunks").status_code == 404


def test_health_reports_both_indexes():
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "chunks_indexed" in body and "bm25_indexed" in body


def test_openapi_documents_the_contract():
    """The generated schema is a real deliverable — a reviewer reads /docs
    before reading source."""
    schema = client.get("/openapi.json").json()
    assert "/query" in schema["paths"]
    assert "QueryResponse" in schema["components"]["schemas"]


# ── against the real indexed corpus ──────────────────────────────────────

@pytest.fixture(scope="module")
def corpus_ready():
    body = client.get("/health").json()
    if body["chunks_indexed"] == 0:
        pytest.skip("corpus not ingested — run POST /ingest first")
    return body


def test_in_scope_question_returns_verified_citations(corpus_ready):
    response = client.post("/query", json={
        "question": "how long is personal data retained?", "top_k": 5,
    })
    assert response.status_code == 200
    body = response.json()

    assert body["citations"], "expected at least one verified citation"
    assert body["insufficient_context"] is False
    assert body["trace"]["citations_dropped"] == 0


def test_citation_offsets_resolve_against_the_text_endpoint(corpus_ready):
    """The end-to-end citation contract, over HTTP: offsets returned by /query
    slice the exact quote out of the text /documents/{id}/text serves."""
    body = client.post("/query", json={
        "question": "how long is personal data retained?"
    }).json()

    for citation in body["citations"]:
        document = client.get(f"/documents/{citation['doc_id']}/text").json()
        span = document["text"][citation["char_start"]:citation["char_end"]]
        assert span == citation["quote"]


def test_out_of_scope_question_abstains_with_200(corpus_ready):
    """Abstention is a successful response, not an error."""
    response = client.post("/query", json={
        "question": "what is the dental reimbursement cap for contractors?"
    })
    assert response.status_code == 200
    body = response.json()

    assert body["insufficient_context"] is True
    assert body["citations"] == []
    assert body["missing_information"]
    assert body["trace"]["abstained_before_llm"] is True


def test_doc_filter_scopes_the_answer(corpus_ready):
    body = client.post("/query", json={
        "question": "what is the purpose of this policy",
        "doc_ids": ["sans-privacy-management-policy-feb2026"],
    }).json()

    for chunk in body["retrieved"]:
        assert chunk["meta"]["doc_id"] == "sans-privacy-management-policy-feb2026"


def test_retrieval_modes_are_all_reachable(corpus_ready):
    for mode in ("dense", "sparse", "hybrid", "hybrid_rerank"):
        body = client.post("/query", json={
            "question": "what are the sanctions for non-compliance", "mode": mode,
        }).json()
        assert body["trace"]["mode"] == mode


def test_chunks_endpoint_returns_ordered_boundaries(corpus_ready):
    documents = client.get("/documents").json()
    doc_id = documents[0]["doc_id"]

    chunks = client.get(f"/documents/{doc_id}/chunks").json()
    assert chunks
    assert chunks == sorted(chunks, key=lambda c: c["char_start"])


def test_chunk_offsets_match_the_document_text(corpus_ready):
    """The chunker's offset invariant, verified through the API rather than
    in-process."""
    documents = client.get("/documents").json()
    doc_id = documents[0]["doc_id"]

    text = client.get(f"/documents/{doc_id}/text").json()["text"]
    for chunk in client.get(f"/documents/{doc_id}/chunks").json():
        assert text[chunk["char_start"]:chunk["char_end"]] == chunk["text"] 