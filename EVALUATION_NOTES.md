# Evaluation notes

Interpretation of the generated metrics in [`EVALUATION.md`](EVALUATION.md), which
is rewritten by `python -m eval.run_eval` and contains no hand-written content.

## Mode comparison

| Mode | hit@5 | MRR | Abstention F1 | Injection |
|---|---|---|---|---|
| dense | 100% | **1.000** | 0% | 100% |
| sparse | 93% | 0.856 | 0% | 100% |
| hybrid (RRF) | 100% | 0.913 | 0% | 100% |
| **hybrid + rerank** | 100% | 0.967 | **100%** | 100% |

**Dense-only ranked best.** MRR 1.000 versus 0.967 for the production
configuration. Fusion can demote a chunk that dense ranked first when BM25
disagrees, and on a corpus this small and this tightly scoped, dense retrieval
alone is close to ceiling. Reported as measured rather than reframed: the
justification for the production default is not ranking quality.

**Only reranking enables abstention.** Three modes score 0% abstention F1. Sparse
mode produces no distance signal at all. Dense and hybrid do — in-scope queries
land around 0.30 cosine distance and out-of-scope around 0.51–0.59 — but ~0.22 of
separation on a bounded 0–1 scale is too narrow for a durable threshold. The
cross-encoder separates the same queries by ~15 logits out of a ~22-point range.

This is the substantive finding of the evaluation, and it follows from what the
two model types can compute. A bi-encoder encodes query and passage
independently, so it can only rank by proximity — and something is always
nearest. A cross-encoder attends across both and can judge whether a passage
addresses a question at all.

**Sparse missed one answerable question entirely.** BM25 scores of zero are
filtered out, so a question sharing no vocabulary with its answer retrieves
nothing. This is the complementary failure mode that motivates fusion.

## Abstention calibration

Measured top cross-encoder scores on the production corpus:

| Query | Top score |
|---|---|
| how long is personal data retained | +4.58 |
| what are the sanctions for non-compliance | +5.70 |
| how do I submit a data deletion request | +7.41 |
| what is the dental reimbursement cap | −10.37 |
| what are the parental leave entitlements | −10.81 |
| how much equity do engineers receive | −11.26 |

`min_rerank_score = -3.0`, roughly centred in the 15-point gap: 7.6 points of
margin to the worst in-scope query, 7.4 to the best out-of-scope one. Erring
slightly negative picks the safer failure — a marginal question is answered with
a citation the user can inspect rather than refused outright.

## What the generated numbers do not measure

The default provider is a deterministic `MockProvider` that quotes the first
sentence of the top-ranked chunk. Retrieval, abstention, injection, and citation
integrity metrics are unaffected by generation quality — they measure what was
retrieved and what survived verification. Answer *quality* requires a real
provider and the `--judge` flag.

Observed limitation of the mock: on "how long must personal data be retained?"
retrieval correctly surfaced both the seven-year retention clause and a
three-year event-data clause, but the mock quoted a definition of "record" from
the first sentence of the top chunk instead. Retrieval found the right material;
the mock wasted it.

## Reproducing

```bash
cd backend
python -m eval.run_eval                    # production config
python -m eval.run_eval --mode dense       # per-mode comparison
python -m eval.run_eval --judge            # add faithfulness (needs a judge key)
```

Ground truth is verifiable against the corpus:

```bash
python -c "import json; from app.core.registry import read_normalized; \
g=json.load(open('eval/golden_set.json')); \
[print('OK' if all(t.lower() in ' '.join(read_normalized(d) for d in i['expect_doc_ids']).lower() \
for t in i['expect_text']) else 'FIX', i['id']) for i in g['answerable']]"
```

All 15 answerable items pass this check.