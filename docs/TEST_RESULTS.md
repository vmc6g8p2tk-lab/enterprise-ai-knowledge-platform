# Test results

This file records executed commands and observed outcomes. It will be updated after the Phase 1 verification run. No skipped or unrun test is counted as passed.

| Check | Result | Evidence |
| --- | --- | --- |
| Go unit tests | PASS | `go test -count=1 ./...`: `internal/httpapi` passed; other Go packages have no tests |
| Go vet | PASS | `go vet ./...`: exit 0 |
| Python parser/chunk/provider unit tests | PASS | `pytest -q tests/test_unit.py`: 3 passed in 0.28 s; actual PDF/DOCX/TXT/Markdown fixtures |
| Frontend TypeScript/build | PASS | `npm run build`: TypeScript and Vite production build exit 0 |
| Frontend dependency audit | PASS | `npm audit`: 0 vulnerabilities after updating Vite to 6.4.3 |
| Docker Compose health | Pending | Docker Engine must run |
| PostgreSQL/pgvector integration | Pending | — |
| Full API upload/chat/citation | Pending | — |
