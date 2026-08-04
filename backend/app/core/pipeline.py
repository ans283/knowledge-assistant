"""End-to-end question answering: retrieve → gate → generate → verify.

Every path out of this function produces a valid QueryResponse. The Step 2
model validator makes an ungrounded response impossible to construct, so
verification failure has exactly one outcome: abstention.
"""
from __future__ import annotations

import logging
import time

from app.config import settings
from app.core.generator import GenerationError, LLMProvider, get_provider
from app.core.guardrails import (
    confidence_after_verification, retrieval_is_too_weak, scan_for_injection,
    verify_citations,
)
from app.core.retriever import retrieve
from app.core.schemas import QueryRequest, QueryResponse, RetrievalMode

log = logging.getLogger(__name__)


def answer_question(
    request: QueryRequest,
    provider: LLMProvider | None = None,
    collection=None,
) -> QueryResponse:
    started = time.perf_counter()
    provider = provider or get_provider()

    chunks, trace = retrieve(
        request.question,
        mode=request.mode,
        top_k=request.top_k,
        doc_ids=request.doc_ids,
        collection=collection,
    )

    trace.llm_provider = provider.name
    trace.llm_model = provider.model

    flagged = scan_for_injection(chunks)
    trace.injection_flagged = len(flagged)
    if flagged:
        log.warning("directive language in retrieved chunks: %s", flagged)

    # gate: decide without the model whether the corpus can answer at all
    weak = retrieval_is_too_weak(
        chunks, request.mode,
        settings.min_rerank_score, settings.max_dense_distance,
    )
    if weak:
        trace.abstained_before_llm = True
        trace.total_ms = (time.perf_counter() - started) * 1000
        return QueryResponse.abstain(
            question=request.question, missing=weak, trace=trace, retrieved=chunks
        )

    generation_started = time.perf_counter()
    try:
        raw = provider.generate(request.question, chunks)
    except GenerationError as exc:
        trace.total_ms = (time.perf_counter() - started) * 1000
        return QueryResponse.abstain(
            question=request.question,
            missing=f"The model did not return a usable response: {exc}",
            trace=trace,
            retrieved=chunks,
        )
    trace.generation_ms = (time.perf_counter() - generation_started) * 1000
    trace.input_tokens = getattr(raw, "input_tokens", None)
    trace.output_tokens = getattr(raw, "output_tokens", None)

    if raw.insufficient_context:
        trace.total_ms = (time.perf_counter() - started) * 1000
        return QueryResponse.abstain(
            question=request.question,
            missing=raw.missing_information or "The model reported insufficient context.",
            trace=trace,
            retrieved=chunks,
            answer=raw.answer,
        )

    citations, dropped = verify_citations(raw, chunks)
    trace.citations_dropped = dropped
    trace.total_ms = (time.perf_counter() - started) * 1000

    if not citations:
        # the model answered, but nothing it said could be traced to a document
        return QueryResponse.abstain(
            question=request.question,
            missing="No verifiable supporting passage was found for this answer.",
            trace=trace,
            retrieved=chunks,
        )

    return QueryResponse(
        question=request.question,
        answer=raw.answer,
        citations=citations,
        insufficient_context=False,
        missing_information=None,
        confidence=confidence_after_verification(raw, citations, dropped),
        retrieved=chunks,
        trace=trace,
    )