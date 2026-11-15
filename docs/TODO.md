# BedMed TODO

Implementation plan. Check items off as they are completed.

## Decisions

- Real backend: FastAPI + PostgreSQL under Docker Compose (not an in-memory stub).
- LLM access through OpenRouter (OpenAI-compatible); the model is set by environment variable.
- Interface is a CLI chat loop.
- Work happens directly on `main`.
- Pending confirmation: the nurse states placement needs in conversation (slots); the patient record holds identity only.
- Pending confirmation: the workflow asks the nurse to confirm the proposed bed before any assignment is attempted.

## 1. Backend

- [ ] Docker Compose with FastAPI and PostgreSQL; `.env.example` covers all config
- [ ] SQLAlchemy models: ward, room, bed, patient, assignment, escalation
- [ ] Seed data: fictional, with one case per demo scenario and failure path
- [ ] Eligibility service: deterministic rules, returns a rejection reason per ineligible bed
- [ ] Ranking: simple and deterministic, separate from eligibility
- [ ] Endpoints: patient lookup, eligible-bed search, assignment, escalation
- [ ] Assignment revalidates eligibility in a transaction and returns `409` on conflict
- [ ] Tests: eligibility rules, assignment conflict, escalation
- [ ] Ruff clean; single documented test command

## 2. Assistant

- [ ] Pydantic models for intent and extracted entities
- [ ] Prompts in their own module
- [ ] LLM extraction with structured output via OpenRouter
- [ ] API tools: thin HTTP clients, no database access
- [ ] LangGraph workflow: extract → clarify missing slots → look up patient → find eligible beds → propose → confirm → assign → retry on `409` → escalate
- [ ] CLI chat loop
- [ ] Structured logging of workflow events
- [ ] Workflow tests using a fake extractor (no API key needed)

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
