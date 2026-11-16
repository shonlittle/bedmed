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

## 2a. Names and lists

- [x] API: `GET /patients` (name and has-bed filters) and `GET /beds` (status filter)
- [x] Identify a patient by name; ask when no patient or several patients match
- [x] Patient status by name
- [x] Pick a patient by position from the numbered list of matches ("the first one")
- [x] List patients (all, with a bed, without a bed) and beds (all, by status)
- [x] Seed patient sharing a surname, to demonstrate an ambiguous name
- [x] Tests for the endpoints, name lookup and lists

## 2b. Web UI

- [x] Workflow records one event per step, used for both the logs and a per-reply trace
- [x] Assistant web server, separate from the hospital API: chat, board, workflow, documents, logs
- [x] Demo page: chat beside a live bed board, waiting patients and escalations
- [x] Demo controls: change a bed's status as another system would; reset to seed data
- [x] Workflow page drawn from the running graph
- [x] Deliverables rendered from the repository's Markdown files
- [x] Logs page
- [x] API: `GET /escalations`, and `POST /demo/reset` behind `BEDMED_DEMO_MODE`
- [x] Tests for the web endpoints

## 2c. Extraction evaluation

- [x] Labelled cases covering every intent and conversation stage (`evals/extraction_cases.jsonl`)
- [x] Runner that scores the live model per field and can fail below a threshold (`make eval`)
- [x] Tests for the case file and the scoring

## 3. Policy RAG

- [x] Fictional policy documents in `policies/` (5 documents, 24 sections)
- [x] In-process retrieval: sections embedded once, cosine similarity, top three
- [x] Policy-question intent routed to a read-only node; answers cite the source sections
- [x] Citations checked in code; uncited or unsupported answers are replaced by "not found"
- [x] Policy evaluation set scoring retrieval and answers separately (`make eval`)
- [x] LLM calls cap their output size and report the HTTP status on failure

## 4. Documentation

- [x] `docs/design.md`: happy path, failure paths, state diagram, intents and entities
- [x] `docs/scalability.md`: monitoring and evaluation, multi-workflow scaling, RAG, robustness (1–2 pages)
- [x] `README.md`: overview, setup, test command, demo scenarios, where AI is used (runtime and build)

## Done means

- `make up` starts the stack
- The demo (web UI or CLI) completes: happy path, a clarification, a `409` retry, an escalation
- Tests and Ruff pass
- The three documents exist
