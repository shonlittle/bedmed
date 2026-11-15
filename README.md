# BedMed

A prototype AI assistant for hospital patient admission and bed assignment. A nurse describes what a patient needs in plain language; the assistant finds an eligible bed, confirms it, assigns it, and escalates to a human when no bed qualifies.

**The LLM interprets. The workflow decides. Tools act. APIs confirm.**

All patient data is fictional. This is a prototype: it does not process real health information and makes no compliance claims.

## Status

| Part | State |
| --- | --- |
| Backend API (FastAPI + PostgreSQL) | Working |
| Conversational assistant (LangGraph) | Working |
| Policy retrieval (RAG) | Not started |
| Design and scalability notes | Not started |

The plan is tracked in [docs/TODO.md](docs/TODO.md).

## Run it

Requires Docker and `make`. Run `make help` to list every command; each one is a thin wrapper around `docker compose`, defined in the [Makefile](Makefile).

```bash
cp .env.example .env
```

```bash
make up
```

The API is at <http://localhost:8000>, with interactive docs at <http://localhost:8000/docs>. The database is seeded with fictional data on first start.

To reset the database to the seed data:

```bash
make reset
```

To stop everything:

```bash
make down
```

## Chat with the assistant

Put an [OpenRouter](https://openrouter.ai) key in `.env` as `OPENROUTER_API_KEY`, then:

```bash
make chat
```

Use the trace mode to see which workflow nodes ran and what each tool call did:

```bash
make trace
```

Things to try (reset the database between runs):

| Say | What happens |
| --- | --- |
| "I need a bed for patient 1001" then "medical floor, nothing special" | Asks for the missing details, proposes `4M-102B`, and explains why the free bed `4M-103B` is not eligible |
| "yes" | Assigns the bed and reports the API's confirmation |
| "ICU bed for P-1003, on a monitor, no isolation, can share" | No eligible bed, so it escalates and says nothing was assigned |
| "tele bed for P-1002, on a monitor, no isolation, can share", then "actually he needs contact precautions" | The changed need discards the proposal and searches again |

The default model is `anthropic/claude-haiku-5.5`. Set `BEDMED_MODEL` in `.env` to use any other OpenRouter model that supports structured outputs.

## Test it

```bash
make test
```

Tests run against a separate `bedmed_test` database in the same PostgreSQL container, so the race-condition test uses real row locks. The workflow tests replace the LLM with a scripted extractor and use the real API, so the suite needs no API key.

Lint:

```bash
make lint
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

## Assistant

The assistant is a LangGraph state machine in [assistant/workflow.py](assistant/workflow.py). The graph runs once per message, and conversation state persists between messages.

| Step | Done by | Where |
| --- | --- | --- |
| Understand the message: intent and details | LLM, schema-constrained output | [assistant/extractor.py](assistant/extractor.py), [assistant/prompts.py](assistant/prompts.py) |
| Decide what to do next | Code: routing functions over explicit state | [assistant/workflow.py](assistant/workflow.py) |
| Read or change hospital state | Tools that call the API | [assistant/tools.py](assistant/tools.py) |
| Decide eligibility, confirm assignments | The API | [api/](api/) |
| Word the reply | Templates filled from API data | [assistant/messages.py](assistant/messages.py) |

The LLM is called in exactly one node. It cannot choose a bed, call a tool, or write a reply, so it cannot claim an assignment that did not happen. The assistant has no database access.

**Intents:** request a bed, provide information, confirm, decline, patient status, cancel, unknown.

**Entities:** patient ID, ward type, isolation, cardiac monitoring, private room. An unstated detail stays unknown and is asked for; it is never assumed to be "no".
