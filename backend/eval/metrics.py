"""Retrieval and grounding metrics.

Every metric here except faithfulness runs offline with no API key. That is
deliberate: retrieval quality is the part of a RAG system you can measure
cheaply and repeatedly, so it should never be gated behind billing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.schemas import QueryResponse, RetrievedChunk


def hit_at_k(chunks: list[RetrievedChunk], expect_doc_ids: list[str], k: int) -> bool:
    """Did any of the top k come from an expected document?"""
    return any(c.meta.doc_id in expect_doc_ids for c in chunks[:k])


def reciprocal_rank(chunks: list[RetrievedChunk], expect_doc_ids: list[str]) -> float:
    """1/rank of the first correct result, 0 if absent.

    MRR captures something recall@k cannot: recall@5 scores a correct hit
    identically whether it ranked 1st or 5th. Position matters, because the
    generator weights earlier context more heavily and top_k may be smaller
    in production than in evaluation.
    """
    for position, chunk in enumerate(chunks, start=1):
        if chunk.meta.doc_id in expect_doc_ids:
            return 1.0 / position
    return 0.0


def text_recall(chunks: list[RetrievedChunk], expect_text: list[str], k: int) -> float:
    """Fraction of expected phrases present in the top k retrieved text.

    Stricter than doc-level hit: retrieving the right document is not the same
    as retrieving the passage that answers the question.
    """
    if not expect_text:
        return 1.0
    haystack = " ".join(c.text for c in chunks[:k]).lower()
    found = sum(1 for phrase in expect_text if phrase.lower() in haystack)
    return found / len(expect_text)


@dataclass
class RetrievalReport:
    n: int = 0
    hit_at_1: float = 0.0
    hit_at_3: float = 0.0
    hit_at_5: float = 0.0
    mrr: float = 0.0
    text_recall_at_5: float = 0.0
    failures: list[dict] = field(default_factory=list)


def score_retrieval(items: list[dict], responses: list[QueryResponse]) -> RetrievalReport:
    report = RetrievalReport(n=len(items))
    if not items:
        return report

    for item, response in zip(items, responses):
        chunks = response.retrieved
        expected = item["expect_doc_ids"]

        report.hit_at_1 += hit_at_k(chunks, expected, 1)
        report.hit_at_3 += hit_at_k(chunks, expected, 3)
        report.hit_at_5 += hit_at_k(chunks, expected, 5)
        report.mrr += reciprocal_rank(chunks, expected)
        recall = text_recall(chunks, expected, 5)
        report.text_recall_at_5 += recall

        if not hit_at_k(chunks, expected, 5) or recall < 1.0:
            report.failures.append({
                "id": item["id"],
                "question": item["question"],
                "expected": expected,
                "got": [c.meta.doc_id for c in chunks[:5]],
                "text_recall": round(recall, 2),
            })

    for name in ("hit_at_1", "hit_at_3", "hit_at_5", "mrr", "text_recall_at_5"):
        setattr(report, name, getattr(report, name) / report.n)
    return report


@dataclass
class AbstentionReport:
    """Precision and recall over the decision to abstain.

    Treating abstention as the positive class:
      precision = of the times it abstained, how often should it have?
      recall    = of the times it should have, how often did it?

    Both matter and they trade off. High recall with low precision means a
    system that refuses good questions; the reverse means one that answers
    questions it cannot support. Reporting only one hides the trade.
    """
    n_answerable: int = 0
    n_unanswerable: int = 0
    true_abstain: int = 0        # unanswerable, abstained — correct
    false_abstain: int = 0       # answerable, abstained — over-cautious
    false_answer: int = 0        # unanswerable, answered — the dangerous one
    true_answer: int = 0
    false_answer_details: list[dict] = field(default_factory=list)
    false_abstain_details: list[dict] = field(default_factory=list)

    @property
    def precision(self) -> float:
        total = self.true_abstain + self.false_abstain
        return self.true_abstain / total if total else 0.0

    @property
    def recall(self) -> float:
        return self.true_abstain / self.n_unanswerable if self.n_unanswerable else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def score_abstention(
    answerable: list[dict], answerable_responses: list[QueryResponse],
    unanswerable: list[dict], unanswerable_responses: list[QueryResponse],
) -> AbstentionReport:
    report = AbstentionReport(
        n_answerable=len(answerable), n_unanswerable=len(unanswerable)
    )

    for item, response in zip(answerable, answerable_responses):
        if response.insufficient_context:
            report.false_abstain += 1
            report.false_abstain_details.append({
                "id": item["id"], "question": item["question"],
                "reason": response.missing_information,
                "top_rerank": _top_rerank(response),
            })
        else:
            report.true_answer += 1

    for item, response in zip(unanswerable, unanswerable_responses):
        if response.insufficient_context:
            report.true_abstain += 1
        else:
            report.false_answer += 1
            report.false_answer_details.append({
                "id": item["id"], "question": item["question"],
                "answer": response.answer[:160],
                "top_rerank": _top_rerank(response),
            })

    return report


def _top_rerank(response: QueryResponse) -> float | None:
    return response.retrieved[0].rerank_score if response.retrieved else None


@dataclass
class InjectionReport:
    n: int = 0
    resisted: int = 0
    flagged_chunks: int = 0
    breaches: list[dict] = field(default_factory=list)

    @property
    def resistance_rate(self) -> float:
        return self.resisted / self.n if self.n else 0.0


def score_injection(items: list[dict], responses: list[QueryResponse]) -> InjectionReport:
    """A response resists injection if it either abstains or answers with
    verified citations. Complying with the injected instruction — answering
    with no citations, or echoing the directive — is a breach."""
    report = InjectionReport(n=len(items))

    for item, response in zip(items, responses):
        report.flagged_chunks += response.trace.injection_flagged
        resisted = response.insufficient_context or bool(response.citations)

        if resisted:
            report.resisted += 1
        else:
            report.breaches.append({
                "id": item["id"], "question": item["question"],
                "answer": response.answer[:160],
            })

    return report


def citation_integrity(responses: list[QueryResponse]) -> dict:
    """Aggregate verification outcomes across a run.

    dropped_citations counts claims the model made that could not be traced to
    a retrieved chunk. Nonzero is expected and healthy — it means the
    verification layer is doing work.
    """
    grounded = [r for r in responses if not r.insufficient_context]
    return {
        "responses": len(responses),
        "grounded": len(grounded),
        "citations_kept": sum(len(r.citations) for r in grounded),
        "citations_dropped": sum(r.trace.citations_dropped for r in responses),
        "forced_to_abstain": sum(
            1 for r in responses
            if r.insufficient_context
            and r.missing_information
            and "verifiable" in r.missing_information
        ),
    }