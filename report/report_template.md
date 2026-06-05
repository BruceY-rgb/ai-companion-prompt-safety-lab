# Project 6 Report Template

## 1. Introduction

Describe the research question: whether user-defined AI companion system prompts introduce additional safety risks for general-purpose LLMs.

## 2. Background

Summarize AI companion scenarios, user-defined personas, and why private system prompts are difficult to observe directly.

## 3. Data Collection

Explain Arctic Shift usage, target subreddits, keywords, filtering criteria, and final sample count. Include the ethical handling policy: public data only, username anonymization, and no unnecessary reproduction of sensitive content.

## 4. System Prompt Conversion

Describe the conversion prompt, extracted fields, manual quality check process, and examples of high-level persona attributes without exposing private details.

## 5. Security Testing

Define control condition, companion condition, test model, temperature, safety question categories, and single-turn independent session setup.

## 6. Evaluation

Define labels: `safe_refusal`, `unsafe_compliance`, `partial_compliance`, and `needs_review`. Explain the automatic rubric and the manual review protocol.

## 7. Results

Use `report/summary.md`, `report/analysis_by_condition.csv`, `report/analysis_by_category.csv`, and `report/figures/` to report unsafe rate, refusal rate, category-level differences, and high-risk prompt patterns.

## 8. Discussion

Discuss which persona features appear correlated with risk, what the results imply for AI companion safety, and why user-defined system prompts need moderation or runtime safety controls.

## 9. Limitations

Mention sample size, public-data bias, prompt reconstruction uncertainty, single-model dependency, and heuristic judge limitations.

## 10. Mitigation Recommendations

Recommend persona prompt screening, safety-preserving prompt rewriting, runtime refusal policies, dependency-risk detection, and human review for high-risk companion settings.
