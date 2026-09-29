# Safe embedding model or dimension change

The database stores one active vector space at a time. The singleton embedding profile records provider, model identity and dimensions. Ingestion and retrieval compare that identity plus the pgvector column dimension before proceeding. Switching the environment variable alone is rejected when stored vectors exist, even if the new model also produces 1536 dimensions.

## Maintenance workflow

1. Back up PostgreSQL and the uploads volume. Verify the backup can be read.
2. Put the target provider/model/dimensions and API key in an untracked .env file. Keep the key out of commands, logs and Git.
3. Stop frontend, backend and ai-service; leave PostgreSQL running. No writes should occur during the rebuild.
4. Dry-run the reindex command. It checks ready documents, originals, active profile and target dimensions without changing rows.
5. Run the build command. It creates a separate document_chunks_stage table with the target vector dimension, parses all ready originals and batches embeddings into that table. The active table remains untouched while model calls run.
6. The command checks staged row count, then atomically renames the active table to document_chunks_backup, renames the stage to document_chunks, and updates the active profile and document chunk counts in one database transaction. The previous profile is saved in embedding_profile_backup.
7. Start the services with the target provider. Verify health, a few known queries and the evaluation runner before finalizing.
8. If validation fails, stop services and run rollback. It restores the previous vector table and profile in one transaction. Restore the old provider environment and restart.
9. If validation succeeds, stop services, run finalize to drop the backup table and old profile record, then restart.

Commands from the repository root, using the appropriate .env values:

    docker compose -f deployments/compose.yaml stop frontend backend ai-service
    docker compose -f deployments/compose.yaml run --rm --no-deps ai-service python -m app.reindex --mode dry-run --confirm-maintenance
    docker compose -f deployments/compose.yaml run --rm --no-deps ai-service python -m app.reindex --mode build --confirm-maintenance

For rollback or finalization, substitute --mode rollback or --mode finalize in the last command. Then start with:

    docker compose -f deployments/compose.yaml up -d

The code refuses a new build if an earlier stage or backup still exists; inspect it before taking any manual database action. An interrupted build leaves the old active table intact. An interrupted cutover transaction rolls back. The database operations were tested with a 1536-to-64-dimensional hash-provider rebuild, rollback and finalize in an isolated real pgvector database. A real paid-model rebuild has **not** been run without an API key. Model changes may incur substantial API cost; use a small isolated corpus first.

This Phase 2 workflow is intentionally a maintenance operation. It is not an online dual-write, zero-downtime migration.

## Empty database and local model acceptance

If the database has **zero documents and zero chunks**, the same confirmed maintenance command atomically changes the empty pgvector column dimension and active provider profile. No old vectors exist to rebuild, and no backup table is needed. It refuses this shortcut if any document or chunk exists. In Phase 2.5, the main test database was observed empty before switching from hash-v1/vector(1536) to BGE-M3/vector(1024); both schema and profile were checked directly afterward.

The separate `ai-service/scripts/check_local_reindex.py` acceptance command creates a temporary PostgreSQL database, indexes one public corpus document with hash vectors, rebuilds its four chunks with the **running local BGE-M3 model**, checks vector(1024), rolls back to the original 1536-dimensional hash vectors, and removes the temporary database. It verifies the nonempty rebuild path without touching the main database. Run it from `ai-service/` with `TEST_DATABASE_URL` pointing to a local admin-capable test database and Ollama available at `http://127.0.0.1:11434/v1`:

    python -m scripts.check_local_reindex

The embedding profile includes a hash of the provider base URL. A host address and the Docker-network address therefore produce different identities; run production reindex from the same endpoint configuration the serving AI container will use.
