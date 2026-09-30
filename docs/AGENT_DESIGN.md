# Read-only knowledge-base Agent

## Implemented

`POST /knowledge-bases/{id}/agent` takes `{"question":"..."}` under a verified Bearer token. Go verifies KB membership and forwards a server-derived `user_id`, KB ID and request ID over a token-protected internal endpoint. Python uses `AgentPlanner` behind an interface. With the local Qwen model it requests strict JSON planning over the existing OpenAI-compatible API. In the development provider mode, a deterministic rule planner is explicitly labeled as a test stub. The Qwen path is **not native function calling**.

Tool schemas are allowlisted in `ai-service/app/agent.py`:

| Tool | Input | Output | Permission |
| --- | --- | --- | --- |
| `search_knowledge_base` | Exact selected KB UUID and 1–2000-character query | Up to four reranked real chunk rows, vector trace and timing | Membership rechecked; SQL filters this KB before Top-K |
| `get_document_metadata` | Document UUID | Real document ID, name, status, chunk count and creation time | Membership rechecked; document must belong to selected KB |

Unknown names, extra/missing keys, cross-KB IDs, bad UUIDs and oversized queries are rejected before execution. There is no arbitrary URL, shell or model-generated SQL tool. Each request currently executes at most **one** tool, below a three-iteration guard, then calls the configured answer provider with tool results as untrusted context. A `finish` plan performs no tool and currently abstains because the answer provider requires a grounded citation. Only indices into actual tool-result rows can become citations. Chunk citations match a `document_chunks` row; a metadata citation is marked `source_type=document_metadata` and matches a `documents` row.

Logs record request ID, tool name, latency and success/failure, without question text, document content, API Key or Authorization header. Exposed metrics aggregate counts and durations.

## Actually validated

The local real-model run used `bge-m3:567m-fp16` and `qwen2.5:7b-instruct-q4_K_M` in the same Compose stack. Twelve public synthetic cases include five KB searches, two metadata questions, a no-tool greeting, an unsupported question, an unauthorized user, a cross-KB injection request and an injected document. The latest run had ten HTTP 200 responses, one 404 and one 403. Eight returned citations were checked against in-scope database rows; the two metadata citations were also checked field-for-field against `documents`. The first run's two metadata answers abstained; including the document UUID in the metadata tool result fixed both on the unchanged case list. [Before raw JSON](evaluation/results/phase3_agent_local_real_before.json), [first after raw JSON](evaluation/results/phase3_agent_local_real_after.json) and [strict DB recheck](evaluation/results/phase3_agent_local_real_verified.json) retain the observations.

The eight invalid-plan tests cover shell, SQL, URL fetch, cross-KB search, empty query, oversized query, injected extra argument and path traversal. Real-model injection cases show no arbitrary tool execution. They are coverage of these cases only.

An assistant review of the eight cited answers in the strict DB recheck found their facts in the cited passages. One answer to the incident-reporting question was only `30`, omitting the `minutes` unit present in its citation. That is a real answer-quality limitation and is not counted as a fully self-contained answer. This review is a subjective case check, not an automatic semantic-quality score or independent human annotation.

## Not validated / limitations

Qwen can choose a poor tool or abstain. Greeting without a knowledge-base source abstained rather than conversing. The one-tool cap does not support multi-step plans or document lookup by name. Metadata citations prove the DB row is real, but an answer could still misstate its fields; no independent semantic judge was used. A deterministic rule planner is for tests and must not be reported as real LLM tool calling. A broader prompt-injection campaign, native function calling and concurrent Agent load remain unverified.
