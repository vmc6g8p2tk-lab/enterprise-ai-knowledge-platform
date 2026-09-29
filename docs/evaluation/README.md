# Versioned evaluation fixtures

The five Markdown documents here are synthetic examples, not user uploads. Each of the 20 positive questions in cases.json names its source document and an exact reference substring. The four N cases are intentionally unanswered by the corpus. The runner checks references against source files and stored chunks; see [RAG_EVALUATION.md](../RAG_EVALUATION.md) for metric definitions and limitations.

Saved JSON results are public only because this corpus is synthetic. Do not save real user prompts, full private chunks, API headers or keys into this directory.

`results/baseline_hash.json` and `results/after_evidence_gate_hash.json` use **development providers**. Files beginning `local_real_` use **actual local GPU models**, and `local_real_final.json` is the fixed-configuration result referenced by the Phase 2.5 report. The intermediate local files preserve observed extra citations instead of hiding them. `local_real_gpu_samples.csv` contains whole-GPU samples from an earlier local run, not the final latency run. [MANUAL_REVIEW_LOCAL.md](MANUAL_REVIEW_LOCAL.md) records the assistant's separate content judgments on ten final-run answers.
