# BedMed

A prototype AI assistant for hospital patient admission and bed assignment. A nurse describes what a patient needs in plain language; the assistant finds an eligible bed, confirms it, assigns it, and escalates to a human when no bed qualifies.

**The LLM interprets. The workflow decides. Tools act. APIs confirm.**

All patient data is fictional. This is a prototype: it does not process real health information and makes no compliance claims.

## Status

| Part | State |
| --- | --- |
| Backend API (FastAPI + PostgreSQL) | Working |
| Conversational assistant (LangGraph) | Not started |
| Policy retrieval (RAG) | Not started |
| Design and scalability notes | Not started |

The plan is tracked in [docs/TODO.md](docs/TODO.md).

## Run it

Requires Docker.

```bash
cp .env.example .env
```

```bash
docker compose up --build
```

The API is at <http://localhost:8000>, with interactive docs at <http://localhost:8000/docs>. The database is seeded with fictional data on first start.

To reset the database to the seed data:

```bash
docker compose run --rm api python -m api.seed
```

## Test it

```bash
docker compose run --rm api pytest
```

Tests run against a separate `bedmed_test` database in the same PostgreSQL container, so the race-condition test uses real row locks.

Lint:

```bash
docker compose run --rm api ruff check .
```

## Backend

The API owns operational state and validates every request itself; it does not trust what the assistant sends.

| Endpoint | Purpose |
| --- | --- |
| `GET /patients/{id}` | Patient lookup, including the current bed |
| `POST /beds/search` | Eligible beds for a patient, best first, plus why near misses were rejected |
| `POST /assignments` | Assign a bed; re-checks eligibility in the same transaction, `409` on conflict |
| `POST /escalations` | Hand a placement to a human |
| `PATCH /beds/{id}/status` | Change a bed's operational status |

Two ideas shape it:

- **A free bed is not necessarily an eligible bed.** [api/eligibility.py](api/eligibility.py) checks ward type, ward status, bed status, isolation, monitoring, private-room need and the room-sharing policy, and returns every reason a bed was rejected. Ranking is a separate step.
- **A search result can go stale.** [api/placement.py](api/placement.py) locks the room's beds and re-evaluates eligibility before writing an assignment, so a bed taken between search and assignment produces a `409`, never a double booking.

The seed data in [api/seed.py](api/seed.py) is built around demo scenarios: a clean match, a free bed rejected by room-sharing policy, a conflict with a fallback bed, and a full ICU that forces escalation.
