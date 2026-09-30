# Run the Phase 2.5 local models

This opt-in profile uses the existing OpenAI-compatible provider interfaces. It does not change the default hash/extractive development stack. On the tested machine, Docker Desktop passed a GeForce RTX 4080 through to the Ollama container. The model cache lives in a Docker volume, outside Git; the service port binds only to `127.0.0.1`.

## Tested models and configuration

- Ollama 0.34.4, container image pinned in `deployments/compose.local-model.yaml`.
- Embedding: `bge-m3:567m-fp16`, F16, 1024 output dimensions, batch size 16.
- Answer: `qwen2.5:7b-instruct-q4_K_M`, Q4_K_M, JSON completion, temperature 0 and seed 42.
- One model request at a time, at most two models loaded, 30-minute keep-alive, 180-second per-attempt timeout and one retry.
- `OPENAI_API_KEY=ollama` is a **non-secret placeholder** accepted and ignored by the local Ollama API. Do not put a real cloud key into the Compose override.

The Ollama [model library](https://ollama.com/library/bge-m3) lists BGE-M3 as multilingual, and its [OpenAI-compatible endpoint](https://docs.ollama.com/api/openai-compatibility) accepts batch embedding inputs and JSON chat responses. The exact 1024 dimensions, model IDs, formats and GPU placement were also checked on the local runtime; see [RAG_EVALUATION.md](RAG_EVALUATION.md).

## Start from an existing default-stack database

Run these from the repository root in PowerShell. Docker Desktop must have a running Engine and NVIDIA GPU support. A first-time clone should start the default stack once so Go applies the versioned migrations:

```powershell
docker compose --env-file .env -f deployments/compose.yaml up -d --build
docker compose --env-file .env -f deployments/compose.yaml -f deployments/compose.local-model.yaml up -d ollama
docker compose --env-file .env -f deployments/compose.yaml -f deployments/compose.local-model.yaml exec -T ollama ollama pull bge-m3:567m-fp16
docker compose --env-file .env -f deployments/compose.yaml -f deployments/compose.local-model.yaml exec -T ollama ollama pull qwen2.5:7b-instruct-q4_K_M
docker compose --env-file .env -f deployments/compose.yaml stop backend ai-service
docker compose --env-file .env -f deployments/compose.yaml -f deployments/compose.local-model.yaml run --rm --no-deps ai-service python -m app.reindex --mode dry-run --confirm-maintenance
```

Read the dry-run summary. If the database is empty, `--mode build` atomically changes its empty vector column and model profile; it has no old vectors to rebuild. If it has ready documents, the same command stages new vectors from saved originals and retains a rollback table. Back up PostgreSQL and uploads first for the nonempty path, following [REINDEX.md](REINDEX.md). Then:

```powershell
docker compose --env-file .env -f deployments/compose.yaml -f deployments/compose.local-model.yaml run --rm --no-deps ai-service python -m app.reindex --mode build --confirm-maintenance
docker compose --env-file .env -f deployments/compose.yaml -f deployments/compose.local-model.yaml up -d --build
docker compose --env-file .env -f deployments/compose.yaml -f deployments/compose.local-model.yaml ps
```

After a **nonempty** staged reindex, validate known queries and the evaluation run before using `--mode finalize`; use `--mode rollback` if validation fails. An empty switch creates no backup and needs neither action. Running the default hash AI service against a 1024-dimensional BGE-M3 database will deliberately fail its health guard until an explicit reverse migration.

## Repeat the real evaluation and regression

The AI container mounts only the public synthetic evaluation corpus at `/evaluation`. It writes the JSON result into `/tmp` inside the running container. For example:

```powershell
docker compose --env-file .env -f deployments/compose.yaml -f deployments/compose.local-model.yaml exec -T ai-service python -m app.evaluate --corpus /evaluation --output /tmp/local_real_recheck.json --top-k 8 --rerank-k 4 --context-chars 3200
$containerId = docker compose --env-file .env -f deployments/compose.yaml -f deployments/compose.local-model.yaml ps -q ai-service
docker cp "$($containerId):/tmp/local_real_recheck.json" docs/evaluation/results/local_real_recheck.json
```

Run `python -m scripts.check_local_reindex` from `ai-service/` with `TEST_DATABASE_URL` pointing to the dedicated local test database to verify a **nonempty** hash-to-BGE-M3 rebuild and rollback in a temporary database. It uses the loopback Ollama endpoint and public corpus file. It never changes the main database.

The development-provider tests need their own `knowledge_test` database because hash vectors use 1536 dimensions while the local real stack uses 1024. From `ai-service/`, set `TEST_DATABASE_URL` to `postgresql://knowledge:<local-password>@localhost:55432/knowledge_test`, run `python -m scripts.prepare_hash_test_db`, set `E2E_DATABASE_URL` to the main `knowledge` database and `E2E_API_URL=http://localhost:18080`, then run `python -m pytest -q tests`. Use your actual local `.env` password if changed. No test is silently skipped for missing services.

Model downloads, warm-up and first load can take substantially longer than the warm per-request numbers. The saved measurements are a single-workstation baseline, not a capacity claim.
