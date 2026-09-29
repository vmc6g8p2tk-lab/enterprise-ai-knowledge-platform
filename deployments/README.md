Run from the repository root: `docker compose -f deployments/compose.yaml up --build`.
Compose uses a local development password by default. For any shared environment, copy `.env.example` to `.env`, replace it, and keep `.env` untracked.
The application Dockerfiles use Google's public mirror for official Docker base images because Docker Hub token requests timed out in the development environment. The pgvector image remains `pgvector/pgvector:pg17`.
