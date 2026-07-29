import json

import pytest
from pydantic import ValidationError

from app.core.schemas import (
    ChunkMeta, Citation, Confidence, QueryRequest, QueryResponse,
    RetrievalMode, Trace,
)


def meta(**kw) -> ChunkMeta:
    base = dict(
        doc_id="handbook", doc_title="Employee Handbook 2024",
        section_path="Leave Policy > Parental Leave",
        char_start=18422, char_end=19180, chunk_index=47,
        token_count=210, content_hash="sha256:abc", pages=[11, 12],
    )
    return ChunkMeta(**{**base, **kw})


def citation() -> Citation:
    return Citation(
        chunk_id="c_47", doc_id="handbook", doc_title="Employee Handbook 2024",
        section_path="Leave Policy > Parental Leave",
        quote="Employees are entitled to twelve weeks",
        char_start=18422, char_end=19180, pages=[11],
    )


def trace() -> Trace:
    return Trace(mode=RetrievalMode.HYBRID_RERANK)


def test_chunk_meta_chroma_roundtrip():
    m = meta()
    d = m.to_chroma()
    assert all(isinstance(v, (str, int, float, bool)) for v in d.values())
    assert json.loads(d["pages_json"]) == [11, 12]
    assert ChunkMeta.from_chroma(d) == m


def test_grounded_answer_requires_a_citation():
    with pytest.raises(ValidationError):
        QueryResponse(
            question="How much parental leave?",
            answer="Twelve weeks.",
            citations=[],                    # ← the violation
            insufficient_context=False,
            trace=trace(),
        )


def test_abstain_factory_is_valid():
    r = QueryResponse.abstain(
        question="What is the dental reimbursement cap?",
        missing="No document covers dental benefits.",
        trace=trace(),
    )
    assert r.insufficient_context
    assert r.citations == []
    assert r.confidence is Confidence.LOW
    assert "dental" in r.missing_information


def test_abstained_answer_cannot_carry_citations():
    with pytest.raises(ValidationError):
        QueryResponse(
            question="q?", answer="a",
            citations=[citation()],
            insufficient_context=True,
            missing_information="something",
            trace=trace(),
        )


def test_abstained_answer_must_explain_the_gap():
    with pytest.raises(ValidationError):
        QueryResponse(
            question="q?", answer="a", citations=[],
            insufficient_context=True,
            missing_information=None,        # ← the violation
            trace=trace(),
        )


def test_query_request_defaults_and_bounds():
    q = QueryRequest(question="How much parental leave do I get?")
    assert q.mode is RetrievalMode.HYBRID_RERANK
    assert q.top_k == 5

    with pytest.raises(ValidationError):
        QueryRequest(question="hi")          # min_length=3 → "hi" is 2

    with pytest.raises(ValidationError):
        QueryRequest(question="valid question", top_k=99)