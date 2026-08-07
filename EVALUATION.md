# Evaluation

Generated 2026-08-06 18:15 UTC · mode `hybrid_rerank` · top_k 5 · provider `mock` · 36.6s

Regenerate with `python -m eval.run_eval`. Retrieval and abstention metrics require no API key.

## Retrieval

Measured over 15 answerable questions. A hit means a result came from a document that actually contains the answer.

| Metric | Value |
|---|---|
| hit@1 | 93.3% |
| hit@3 | 100.0% |
| hit@5 | 100.0% |
| MRR | 0.967 |
| phrase recall@5 | 0.0% |

## Abstention

15 answerable and 10 deliberately out-of-scope questions. Abstention is the positive class.

| Metric | Value |
|---|---|
| precision | 100.0% |
| recall | 100.0% |
| F1 | 100.0% |
| false answers (answered when it should not) | 0 |
| false abstentions (refused a fair question) | 0 |

Threshold: `min_rerank_score = -3.0`, calibrated against measured score distributions rather than guessed.

## Citation integrity

| Metric | Value |
|---|---|
| grounded responses | 15/30 |
| citations verified | 15 |
| citations dropped as unverifiable | 0 |
| answers forced to abstain by verification | 0 |

Dropped citations are the verification layer working: a quote that does not appear in the chunk it cites is discarded, and an answer left with no verified citation abstains.

## Prompt injection

5 adversarial prompts. Resistance means the system either abstained or answered with verified citations.

| Metric | Value |
|---|---|
| resistance rate | 100.0% |
| chunks flagged for directive language | 0 |

## Retrieval failures

**a01** — How long is product usage event data retained?
- expected: gitlab-privacy-product-usage-events-faq
- retrieved: gitlab-privacy-product-usage-events-faq, gitlab-privacy-product-usage-events-faq, gitlab-privacy-product-usage-events-faq, gitlab-privacy-customer-product-usage-information, gitlab-privacy-employee-privacy-policy
- phrase recall: 0.0

**a02** — How long does GitLab preserve user information after a law enforcement preservation request?
- expected: gitlab-privacy-law-enforcement-guidelines
- retrieved: gitlab-privacy-law-enforcement-guidelines, gitlab-privacy-law-enforcement-guidelines, gitlab-privacy-transparency-reports, gitlab-privacy-transparency-reports, gitlab-privacy-transparency-reports
- phrase recall: 0.0

**a03** — Can law enforcement extend a preservation request, and by how long?
- expected: gitlab-privacy-law-enforcement-guidelines
- retrieved: gitlab-privacy-law-enforcement-guidelines, gitlab-privacy-law-enforcement-guidelines, gitlab-privacy-law-enforcement-guidelines, gitlab-privacy-transparency-reports, gitlab-privacy-transparency-reports
- phrase recall: 0.0

**a04** — How many US law enforcement requests did GitLab receive in 2025?
- expected: gitlab-privacy-transparency-reports
- retrieved: gitlab-privacy-transparency-reports, gitlab-privacy-transparency-reports, gitlab-privacy-transparency-reports, gitlab-privacy-transparency-reports, gitlab-privacy-transparency-reports
- phrase recall: 0.0

**a05** — What does control PRV-06 require?
- expected: sans-privacy-management-policy-feb2026
- retrieved: sans-privacy-management-policy-feb2026, sans-privacy-management-policy-feb2026, sans-privacy-management-policy-feb2026, sans-privacy-management-policy-feb2026, sans-safeguard-validation-management-policy-feb2026
- phrase recall: 0.0

**a06** — What is the purpose of the privacy management policy?
- expected: sans-privacy-management-policy-feb2026
- retrieved: sans-privacy-management-policy-feb2026, sans-privacy-management-policy-feb2026, gitlab-privacy-employee-privacy-policy, sans-privacy-management-policy-feb2026, gitlab-privacy-employee-privacy-policy
- phrase recall: 0.0

**a07** — What is the purpose of the software development management policy?
- expected: sans-software-development-management-policy-feb2026
- retrieved: sans-software-development-management-policy-feb2026, sans-software-development-management-policy-feb2026, sans-software-development-management-policy-feb2026, sans-software-development-management-policy-feb2026, sans-safeguard-selection-management-policy-feb2026
- phrase recall: 0.0

**a08** — What happens if someone does not comply with the privacy policy?
- expected: sans-privacy-management-policy-feb2026, sans-risk-communication-management-policy-feb2026, sans-safeguard-selection-management-policy-feb2026, sans-safeguard-validation-management-policy-feb2026, sans-software-development-management-policy-feb2026
- retrieved: sans-privacy-management-policy-feb2026, sans-risk-communication-management-policy-feb2026, sans-software-development-management-policy-feb2026, sans-safeguard-validation-management-policy-feb2026, sans-safeguard-selection-management-policy-feb2026
- phrase recall: 0.0

**a09** — How do I request deletion of my personal data?
- expected: gitlab-privacy-gdpr
- retrieved: gitlab-privacy-employee-privacy-policy, gitlab-privacy-gdpr, gitlab-privacy-gdpr, gitlab-privacy-employee-privacy-policy, gitlab-privacy-employee-privacy-policy
- phrase recall: 0.0

**a10** — When is a Data Protection Impact Assessment required?
- expected: gitlab-privacy-dpia
- retrieved: gitlab-privacy-dpia, gitlab-privacy-dpia, sans-privacy-management-policy-feb2026, gitlab-privacy-employee-privacy-policy, sans-risk-communication-management-policy-feb2026
- phrase recall: 0.0

**a11** — Who can team members contact to withdraw consent for processing sensitive personal data?
- expected: gitlab-privacy-employee-privacy-policy
- retrieved: gitlab-privacy-employee-privacy-policy, gitlab-privacy-employee-privacy-policy, gitlab-privacy-employee-privacy-policy, gitlab-privacy-employee-privacy-policy, gitlab-privacy-employee-privacy-policy
- phrase recall: 0.0

**a12** — Can a team member correct inaccurate personal data held about them?
- expected: gitlab-privacy-employee-privacy-policy
- retrieved: gitlab-privacy-employee-privacy-policy, gitlab-privacy-employee-privacy-policy, gitlab-privacy-employee-privacy-policy, gitlab-privacy-employee-privacy-policy, gitlab-privacy-employee-privacy-policy
- phrase recall: 0.0

**a13** — What metrics does GitLab collect about how the software is used?
- expected: gitlab-privacy-customer-product-usage-information
- retrieved: gitlab-privacy-customer-product-usage-information, gitlab-privacy-customer-product-usage-information, gitlab-privacy-customer-product-usage-information, gitlab-privacy-customer-product-usage-information, gitlab-privacy-customer-product-usage-information
- phrase recall: 0.0

**a14** — What form must a law enforcement preservation request take?
- expected: gitlab-privacy-law-enforcement-guidelines
- retrieved: gitlab-privacy-law-enforcement-guidelines, gitlab-privacy-law-enforcement-guidelines, gitlab-privacy-law-enforcement-guidelines, gitlab-privacy-transparency-reports, gitlab-privacy-transparency-reports
- phrase recall: 0.0

**a15** — What does the safeguard validation policy cover?
- expected: sans-safeguard-validation-management-policy-feb2026
- retrieved: sans-safeguard-validation-management-policy-feb2026, sans-safeguard-validation-management-policy-feb2026, sans-safeguard-validation-management-policy-feb2026, sans-safeguard-selection-management-policy-feb2026, sans-safeguard-validation-management-policy-feb2026
- phrase recall: 0.0

