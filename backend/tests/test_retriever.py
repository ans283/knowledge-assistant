import chromadb
import pytest

from app.core.indexer import ingest_corpus
from app.core.registry import init_db
from app.core.retriever import (
    BM25Index, RetrievalMode, reciprocal_rank_fusion, retrieve, tokenize,
)

COSINE = {"hnsw": {"space": "cosine"}}

CORPUS = {
    "privacy_policy.md": """Privacy Management Policy

Purpose
Our Data Privacy Policy aims to establish a comprehensive framework for protecting
the privacy and confidentiality of personal data entrusted to our organization.

Safeguards
PRV-06 Ensure that the organization's documented privacy program defines a process
to obtain consent before collecting personal data from any data subject.
Personal data shall be retained for seven years following the termination of the
customer relationship, after which it is securely destroyed using approved methods.

Sanctions
Non-compliance with this policy may result in disciplinary action up to and
including termination of employment or contract.
""",
    "software_policy.md": """Software Development Management Policy

Purpose
Our Software Development Policy aims to establish a comprehensive framework for
secure coding practices across the engineering organization.

Safeguards
SDV-04 Ensure that all source code changes are reviewed by a second engineer
before being merged into the main development branch of any repository.
Automated tests must pass in continuous integration before any deployment
to a production environment is permitted under this policy.
""",
}


# ── pure functions, no model loading ─────────────────────────────────────

def test_tokenize_preserves_control_identifiers():
    """'PRV-06' must stay one token: splitting it destroys the rare-term
    signal that makes BM25 useful for identifier lookup."""
    assert "prv-06" in tokenize("Control PRV-06 applies here.")
    assert tokenize("GDPR, CCPA; and HIPAA.") == ["gdpr", "ccpa", "and", "hipaa"]


def test_rrf_rewards_appearing_in_both_arms():
    """RRF's actual guarantee: a chunk both arms retrieved beats one that only
    a single arm found, even when that arm ranked it first."""
    dense = ["a", "b", "c"]
    sparse = ["d", "b", "e"]
    order = [cid for cid, _ in reciprocal_rank_fusion([dense, sparse], k=60)]

    assert order[0] == "b"              # only chunk in both lists
    assert set(order) == {"a", "b", "c", "d", "e"}


def test_rrf_is_convex_so_spread_ranks_edge_out_even_ones():
    """1/x is convex, so for a fixed rank sum the more spread pair scores
    marginally higher: (1st, 3rd) edges out (2nd, 2nd). The margin is tiny —
    documenting it here so the behaviour isn't mistaken for a bug later."""
    scores = dict(reciprocal_rank_fusion([["a", "b", "c"], ["c", "b", "d"]], k=60))

    assert scores["c"] > scores["b"]
    assert scores["c"] / scores["b"] < 1.001        # negligible in practice

def test_rrf_k_damps_top_rank_dominance():
    """With k=60 the gap between rank 1 and rank 2 is small, so agreement
    across arms outweighs one arm's confidence. This is why k exists."""
    scores = dict(reciprocal_rank_fusion([["x", "y"]], k=60))
    assert scores["x"] / scores["y"] < 1.02

    scores_no_k = dict(reciprocal_rank_fusion([["x", "y"]], k=0))
    assert scores_no_k["x"] / scores_no_k["y"] == pytest.approx(2.0)


def test_rrf_handles_empty_lists():
    assert reciprocal_rank_fusion([[], []]) == []
    assert [cid for cid, _ in reciprocal_rank_fusion([["a"], []])] == ["a"]


def test_bm25_index_empty_until_built():
    index = BM25Index()
    assert index.size == 0
    assert index.search("anything", 5) == []


# ── integration ──────────────────────────────────────────────────────────

@pytest.fixture
def indexed(tmp_path, request):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name, body in CORPUS.items():
        (corpus / name).write_text(body, encoding="utf-8")

    db = tmp_path / "registry.db"
    init_db(db)

    safe = "".join(c if c.isalnum() else "-" for c in request.node.name)[:60].strip("-")
    col = chromadb.EphemeralClient().get_or_create_collection(
        name=f"ret-{safe}", configuration=COSINE
    )
    ingest_corpus(
        collection=col, db_path=db,
        normalized_dir=tmp_path / "normalized", corpus_dir=corpus,
    )
    return col


pytestmark = pytest.mark.slow


def test_dense_finds_paraphrase_with_no_shared_words(indexed):
    """The case BM25 cannot handle: no lexical overlap with the answer."""
    results, trace = retrieve(
        "how long do we keep customer information",
        mode=RetrievalMode.DENSE, top_k=3, collection=indexed,
    )
    assert results
    assert any("seven years" in r.text for r in results)
    assert trace.mode is RetrievalMode.DENSE
    assert all(r.dense_distance is not None for r in results)
    assert all(r.sparse_score is None for r in results)


def test_sparse_finds_exact_identifier(indexed):
    """The case dense retrieval handles poorly: 'PRV-06' carries almost no
    semantic content, so its embedding is close to meaningless."""
    results, trace = retrieve(
        "PRV-06", mode=RetrievalMode.SPARSE, top_k=3, collection=indexed
    )
    assert results
    assert "PRV-06" in results[0].text
    assert results[0].sparse_score is not None
    assert results[0].dense_distance is None


def test_hybrid_populates_both_score_arms(indexed):
    results, trace = retrieve(
        "consent before collecting personal data",
        mode=RetrievalMode.HYBRID, top_k=5, collection=indexed,
    )
    assert results
    assert all(r.fused_score is not None for r in results)
    assert any(r.dense_distance is not None for r in results)


def test_rerank_reorders_and_scores(indexed):
    results, trace = retrieve(
        "what happens if someone violates the policy",
        mode=RetrievalMode.HYBRID_RERANK, top_k=3, collection=indexed,
    )
    assert results
    assert all(r.rerank_score is not None for r in results)
    assert results == sorted(results, key=lambda r: -r.rerank_score)
    assert trace.rerank_ms > 0
    assert "disciplinary action" in results[0].text


def test_doc_filter_scopes_retrieval(indexed):
    results, _ = retrieve(
        "comprehensive framework", mode=RetrievalMode.HYBRID, top_k=5,
        doc_ids=["privacy-policy"], collection=indexed,
    )
    assert results
    assert all(r.meta.doc_id == "privacy-policy" for r in results)


def test_top_k_is_respected(indexed):
    results, trace = retrieve(
        "policy", mode=RetrievalMode.HYBRID_RERANK, top_k=2, collection=indexed
    )
    assert len(results) <= 2
    assert trace.chunks_sent_to_llm == len(results)


def test_trace_records_timings_and_counts(indexed):
    _, trace = retrieve("personal data", mode=RetrievalMode.HYBRID_RERANK,
                        collection=indexed)
    assert trace.candidates_considered > 0
    assert trace.retrieval_ms > 0
    assert trace.total_ms >= trace.retrieval_ms
    assert trace.embedding_model


def test_boilerplate_query_routes_to_the_right_document(indexed):
    """Both policies open with near-identical 'comprehensive framework'
    boilerplate. Section-path context should still route correctly."""
    results, _ = retrieve(
        "what is the purpose of the privacy policy",
        mode=RetrievalMode.HYBRID_RERANK, top_k=1, collection=indexed,
    )
    assert results[0].meta.doc_id == "privacy-policy"