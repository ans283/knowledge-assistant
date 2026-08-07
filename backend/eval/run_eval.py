"""Run the golden set and write EVALUATION.md.

    python -m eval.run_eval
    python -m eval.run_eval --mode dense        # compare retrieval modes
    python -m eval.run_eval --judge             # add faithfulness (needs a key)
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.core.pipeline import answer_question
from app.core.schemas import QueryRequest, RetrievalMode
from eval.faithfulness import judge_available, judge_response
from eval.metrics import (
    citation_integrity, score_abstention, score_injection, score_retrieval,
)

EVAL_DIR = Path(__file__).parent
GOLDEN_SET = EVAL_DIR / "golden_set.json"
OUTPUT = EVAL_DIR.parent.parent / "EVALUATION.md"


def run_items(items: list[dict], mode: RetrievalMode, top_k: int) -> list:
    responses = []
    for item in items:
        responses.append(answer_question(
            QueryRequest(question=item["question"], mode=mode, top_k=top_k)
        ))
    return responses


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="hybrid_rerank",
                        choices=[m.value for m in RetrievalMode])
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--judge", action="store_true")
    args = parser.parse_args()

    mode = RetrievalMode(args.mode)
    golden = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))

    started = time.perf_counter()
    answerable_r = run_items(golden["answerable"], mode, args.top_k)
    unanswerable_r = run_items(golden["unanswerable"], mode, args.top_k)
    injection_r = run_items(golden["injection"], mode, args.top_k)
    elapsed = time.perf_counter() - started

    retrieval = score_retrieval(golden["answerable"], answerable_r)
    abstention = score_abstention(
        golden["answerable"], answerable_r,
        golden["unanswerable"], unanswerable_r,
    )
    injection = score_injection(golden["injection"], injection_r)
    integrity = citation_integrity(answerable_r + unanswerable_r + injection_r)

    faithfulness = None
    if args.judge:
        if not judge_available():
            print("no judge key configured — skipping faithfulness")
        else:
            scores = [j for r in answerable_r if (j := judge_response(r))]
            if scores:
                faithfulness = {
                    "judged": len(scores),
                    "faithful": sum(1 for s in scores if s["verdict"] == "faithful"),
                    "supported_ratio": sum(
                        s["supported_claims"] / max(1, s["total_claims"]) for s in scores
                    ) / len(scores),
                }

    OUTPUT.write_text(
        render(mode, args.top_k, retrieval, abstention, injection, integrity,
               faithfulness, elapsed, len(golden["answerable"])),
        encoding="utf-8", newline="",
    )
    print(f"wrote {OUTPUT}")
    print(f"  hit@5 {retrieval.hit_at_5:.0%}  MRR {retrieval.mrr:.3f}  "
          f"abstention F1 {abstention.f1:.0%}  "
          f"injection {injection.resistance_rate:.0%}")


def render(mode, top_k, retrieval, abstention, injection, integrity,
           faithfulness, elapsed, n_answerable) -> str:
    lines = [
        "# Evaluation",
        "",
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"mode `{mode.value}` · top_k {top_k} · provider `{settings.llm_provider}` · "
        f"{elapsed:.1f}s",
        "",
        "Regenerate with `python -m eval.run_eval`. Retrieval and abstention "
        "metrics require no API key.",
        "",
        "## Retrieval",
        "",
        f"Measured over {retrieval.n} answerable questions. A hit means a result "
        "came from a document that actually contains the answer.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| hit@1 | {retrieval.hit_at_1:.1%} |",
        f"| hit@3 | {retrieval.hit_at_3:.1%} |",
        f"| hit@5 | {retrieval.hit_at_5:.1%} |",
        f"| MRR | {retrieval.mrr:.3f} |",
        f"| phrase recall@5 | {retrieval.text_recall_at_5:.1%} |",
        "",
        "## Abstention",
        "",
        f"{abstention.n_answerable} answerable and {abstention.n_unanswerable} "
        "deliberately out-of-scope questions. Abstention is the positive class.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| precision | {abstention.precision:.1%} |",
        f"| recall | {abstention.recall:.1%} |",
        f"| F1 | {abstention.f1:.1%} |",
        f"| false answers (answered when it should not) | {abstention.false_answer} |",
        f"| false abstentions (refused a fair question) | {abstention.false_abstain} |",
        "",
        f"Threshold: `min_rerank_score = {settings.min_rerank_score}`, calibrated "
        "against measured score distributions rather than guessed.",
        "",
        "## Citation integrity",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| grounded responses | {integrity['grounded']}/{integrity['responses']} |",
        f"| citations verified | {integrity['citations_kept']} |",
        f"| citations dropped as unverifiable | {integrity['citations_dropped']} |",
        f"| answers forced to abstain by verification | {integrity['forced_to_abstain']} |",
        "",
        "Dropped citations are the verification layer working: a quote that does "
        "not appear in the chunk it cites is discarded, and an answer left with "
        "no verified citation abstains.",
        "",
        "## Prompt injection",
        "",
        f"{injection.n} adversarial prompts. Resistance means the system either "
        "abstained or answered with verified citations.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| resistance rate | {injection.resistance_rate:.1%} |",
        f"| chunks flagged for directive language | {injection.flagged_chunks} |",
        "",
    ]

    if faithfulness:
        lines += [
            "## Faithfulness (LLM judge)",
            "",
            f"{faithfulness['judged']} answers judged. The judge is a different "
            "model family than the generator, to avoid self-preference bias.",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| rated fully faithful | {faithfulness['faithful']}/{faithfulness['judged']} |",
            f"| mean supported-claim ratio | {faithfulness['supported_ratio']:.1%} |",
            "",
        ]

    if retrieval.failures:
        lines += ["## Retrieval failures", ""]
        for f in retrieval.failures:
            lines += [
                f"**{f['id']}** — {f['question']}",
                f"- expected: {', '.join(f['expected'])}",
                f"- retrieved: {', '.join(f['got']) or '(nothing)'}",
                f"- phrase recall: {f['text_recall']}",
                "",
            ]

    if abstention.false_answer_details:
        lines += ["## Answered when it should have abstained", ""]
        for f in abstention.false_answer_details:
            lines += [f"**{f['id']}** — {f['question']}",
                      f"- top rerank score: {f['top_rerank']}",
                      f"- answered: {f['answer']}", ""]

    if abstention.false_abstain_details:
        lines += ["## Abstained on a fair question", ""]
        for f in abstention.false_abstain_details:
            lines += [f"**{f['id']}** — {f['question']}",
                      f"- top rerank score: {f['top_rerank']}",
                      f"- reason given: {f['reason']}", ""]

    if injection.breaches:
        lines += ["## Injection breaches", ""]
        for b in injection.breaches:
            lines += [f"**{b['id']}** — {b['question']}", f"- {b['answer']}", ""]

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()