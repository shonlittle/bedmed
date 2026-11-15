# BedMed TODO

Implementation plan. Check items off as they are completed.

## Decisions

- Real backend: FastAPI + PostgreSQL under Docker Compose (not an in-memory stub).
- LLM access through OpenRouter (OpenAI-compatible); the model is set by environment variable.
- Interface is a CLI chat loop.
- Work happens directly on `main`.
- The nurse states placement needs in conversation (slots); the patient record holds identity and sex only.
- The workflow asks the nurse to confirm the proposed bed before any assignment is attempted.
- Trims from the original domain sketch: no reservations table (the `RESERVED` bed status covers it), one shared-room field (`sex`), IDs double as human-readable labels.

## 1. Backend

- [x] Docker Compose with FastAPI and PostgreSQL; `.env.example` covers all config
- [x] SQLAlchemy models: ward, room, bed, patient, assignment, escalation
- [x] Seed data: fictional, with one case per demo scenario and failure path
- [x] Eligibility service: deterministic rules, returns a rejection reason per ineligible bed
- [x] Ranking: simple and deterministic, separate from eligibility
- [x] Endpoints: patient lookup, eligible-bed search, assignment, escalation
- [x] Assignment revalidates eligibility in a transaction and returns `409` on conflict
- [x] Tests: eligibility rules, assignment conflict, escalation
- [x] Ruff clean; single documented test command

## 2. Assistant

- [x] Pydantic models for intent and extracted entities
- [x] Prompts in their own module
- [x] LLM extraction with structured output via OpenRouter
- [x] API tools: thin HTTP clients, no database access
- [x] LangGraph workflow: extract → clarify missing slots → look up patient → find eligible beds → propose → confirm → assign → retry on `409` → escalate
- [x] CLI chat loop
- [x] Structured logging of workflow events
- [x] Workflow tests using a fake extractor (no API key needed)

## 3. Policy RAG (bonus; drop first if time is short)

- [ ] Fictional policy documents in `policies/`
- [ ] Lightweight in-process retrieval
- [ ] Policy-question intent routed to retrieval, answers cite the source document

## 4. Documentation

- [ ] `docs/design.md`: happy path, failure paths, state diagram, intents and entities
- [ ] `docs/scalability.md`: monitoring and evaluation, multi-workflow scaling, RAG, robustness (1–2 pages)
- [ ] `README.md`: overview, setup, test command, demo scenarios, where AI is used (runtime and build)

## Done means

- `docker compose up --build` starts the stack
- The CLI demo completes: happy path, a clarification, a `409` retry, an escalation
- Tests and Ruff pass
- The three documents exist
