# Phase 3 security boundary

## Implemented

- Passwords use bcrypt. JWTs are signed with a random, untracked HS256 secret and verified with a fixed algorithm, issuer/audience and expiry. All public KB routes require authentication and a KB membership role.
- Go forwards only its authenticated user ID to the Python service. A separate internal token is required by Python. Every Agent tool then rechecks membership and document/KB scope in PostgreSQL.
- SQL is parameterized. Tool schemas and the two-tool allowlist are code-owned. No URL fetch, shell, filesystem write or arbitrary SQL tool is reachable from the planner.
- Model instructions are a system message; retrieved document content is placed in lower-trust context. Model-generated citation indices are validated and transformed into server-owned DB row references. `RAG_DEBUG` is intended only for trusted development because its response contains retrieved text.
- Ordinary logs keep IDs, counts and timings. API keys, JWTs, internal tokens, auth headers and complete uploaded documents are not intentionally logged. `.env`, upload volume, model weights, caches and profiles are ignored by Git.

## Actually validated attack cases

Eight parameterized invalid/injected planner plans were rejected: `execute_shell`, `run_sql`, `fetch_url`, wrong KB, empty query, overlong query, an extra `sql` argument and path traversal in document ID. A ninth integration case used a real document ID from another KB and was denied. Full Compose RBAC tests covered anonymous access, viewer upload, editor delete, outsider KB reads, and changed KB IDs on chat/Agent. Real Qwen cases included an instruction to fetch metadata from another KB (HTTP 403), an injected document instruction to run a shell command (only the allowed search tool ran), and an unauthorized user (HTTP 404). See [raw Agent cases](evaluation/results/phase3_agent_local_real_verified.json).

These tests exercise **tool execution and data isolation**, not a complete prompt-injection defense or automatic proof of answer faithfulness.

## Not validated / known limits

There is no login rate limit, email verification, MFA, token revocation list, audit-log storage, public TLS termination, malicious-file scanning, OCR sandbox, independent penetration test, or multi-tenant deployment review. A prompt can still affect generated wording or tool selection within the allowlist. A valid citation only proves provenance; it does not prove all claims in an answer. Local Compose ports bind to loopback; expose them beyond the workstation only after additional controls.
