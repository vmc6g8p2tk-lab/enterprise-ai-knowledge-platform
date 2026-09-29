# Local-model answer review

This is a **case-by-case content judgment by the assistant**, not an automated metric or an independent human annotation. The reviewer compared the final answer with **every returned citation's actual chunk text** in [local_real_final.json](results/local_real_final.json), which the runner had matched to PostgreSQL rows before cleaning its temporary knowledge base. Another person should independently review these judgments before using them as an external quality claim. The same 20+4 corpus was not edited.

Run reviewed: `local_real_final.json`, `temperature=0`, `seed=42`, BGE-M3 F16 + Qwen2.5 7B Q4_K_M, Top-8/rerank-4, 3200 context characters.

| Case | Answered fact | Is the answer supported by at least one cited chunk? | Do all returned chunks directly support the answered fact? | Review note |
| --- | --- | --- | --- | --- |
| P01 | 18 working days of annual leave | Yes | Yes | Annual-leave chunk states the number and employee scope. |
| P02 | Three-month probation | Yes | Yes | The cited probation chunk states the period. |
| P03 | Up to two remote days after manager approval | Yes | Yes | Both the limit and approval appear in the cited remote-work chunk. |
| P04 | 1200 currency units yearly learning budget | Yes | Yes | The cited learning-budget chunk states amount and scope. |
| P05 | Corporate identity account needs MFA | Yes | Yes | The cited security chunk states the account and requirement. |
| P06 | Review production access every 90 days | Yes | **No** | The first cited chunk states 90 days; the second is an adjacent incident-reporting chunk and does not state the interval. |
| P07 | Report a suspected incident within 30 minutes | Yes | Yes | The cited incident-reporting chunk states the time. |
| P08 | Customer-identifier documents are Confidential | Yes | Yes | The cited data-classification chunk states this directly. |
| P09 | Tuesday/Thursday 10:00–16:00 deployment window | Yes | Yes | The cited operations chunk states the days and hours. |
| P10 | Roll back above 5% errors for ten minutes | Yes | Yes | The cited rollback chunk states both threshold and duration. |

**Judgment on this 10-case sample:** 10/10 answers had at least one supporting citation; 9/10 returned only chunks that directly supported the answered fact. This does **not** imply the other ten positive answers or future user questions have the same quality. Exact database validity and knowledge-base scope are separate automated checks and both passed for 21/21 citations in the final run.

The four unsupported questions returned `abstained=true` and no citations in the final run. This is a finite sample, not a guarantee against hallucination or prompt injection.
