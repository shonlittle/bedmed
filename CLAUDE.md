# CLAUDE.md

## Project Overview

BedMed is a portfolio-quality prototype of an AI-assisted hospital bed management system.

The application demonstrates how conversational AI can support patient admission, bed matching, bed assignment, escalation, and hospital policy workflows while keeping operational actions deterministic, auditable, and easy to reason about.

The primary goal is to demonstrate:

- Strong architectural thinking
- Practical AI engineering
- Clear separation of concerns
- Safe and deterministic workflow execution
- Production-oriented thinking without overengineering
- Clean, readable, interview-ready code

This is an MVP and portfolio project. Prefer a small, complete, defensible implementation over a large or overly abstract one.

---

## Core Architectural Principle

Follow this principle throughout the project:

**The LLM interprets.  
The workflow decides.  
Tools act.  
APIs confirm.**

The conversational AI layer must not have direct database access.

The LLM must never claim that an operational action succeeded unless the backend API confirms success.

Prefer deterministic application logic for business rules, eligibility decisions, and state transitions. Use an LLM only where natural-language understanding, structured extraction, clarification, policy interpretation, or summarization provides real value.

A key domain principle is:

**A free bed is not necessarily an eligible bed.**

Bed availability and bed eligibility are separate concepts.

---

## Intended Architecture

```text
User
  ↓
Conversational Assistant
  ↓
Intent + Entity Extraction
  ↓
LangGraph Workflow
  ↓
Constrained Tools
  ↓
FastAPI REST API
  ↓
PostgreSQL
```

Hospital policies and other unstructured knowledge may be retrieved through a separate RAG layer.

The assistant should interact with backend systems only through constrained tools that call documented APIs.

---

## Preferred Technology Stack

Use the following technologies unless there is a strong reason to deviate:

- Python
- FastAPI
- PostgreSQL
- SQLAlchemy
- Pydantic
- LangGraph
- Docker / Docker Compose
- pytest
- Ruff
- OpenAI-compatible LLM APIs where needed

Keep dependencies minimal.

Do not introduce additional frameworks, databases, queues, services, or infrastructure unless required by a concrete feature.

---

## Standard Workflow

1. First read `README.md` and the relevant Markdown files in `docs/`.

2. Read the relevant existing code before proposing or making changes.

3. For any multi-step or substantial task, create or update `docs/TODO.md` before implementation to reduce AI drift.

4. The plan in `docs/TODO.md` should contain concise checkbox items that can be marked complete as work progresses.

5. Before beginning a substantial implementation, show me the proposed plan and wait for my approval.

6. Once approved, work through the plan in order and mark completed items as finished.

7. Give me concise, high-level explanations of meaningful changes. Do not narrate every low-level edit.

8. Keep each task and code change as small and simple as reasonably possible.

9. Prefer established engineering practices and industry standards over clever abstractions.

10. Optimize for a working MVP first.

11. As functionality stabilizes, add unit and integration tests for important logic and failure cases.

12. Ensure new code is lint-free before considering a task complete.

13. Review all code for security concerns and accidental exposure of sensitive information.

14. Mark completed tasks in `docs/TODO.md`.

---

## MVP-Focused Development Strategy

This project prioritizes rapid delivery of working, demonstrable features.

Do not overbuild.

### Avoid Overengineering

Do not:

- Create unnecessary abstraction layers.
- Introduce microservices merely for architectural purity.
- Build a multi-agent system unless a concrete use case requires it.
- Add infrastructure for hypothetical future scale.
- Add generic frameworks where straightforward Python is sufficient.
- Build a sophisticated frontend unless explicitly requested.
- Add Kubernetes or orchestration infrastructure for this MVP.
- Add event buses, queues, caches, or distributed systems without a real requirement.

Prefer code that is easy to read, explain, test, and defend in a technical interview.

---

## Separation of Concerns

Maintain clear boundaries between:

- Conversational AI
- Intent and entity extraction
- Workflow orchestration
- Business rules
- Bed eligibility logic
- Tool definitions
- REST API integrations
- Persistence
- Policy / RAG retrieval
- Presentation / UI
- Observability and evaluation

Do not mix business rules into prompts when deterministic code can express them more safely.

Do not allow the AI layer to bypass the API layer and query PostgreSQL directly.

---

## Backend API

FastAPI represents the mock hospital backend.

The API owns operational state, validation, and transactional behavior.

Expected backend capabilities include:

- Patient lookup
- Ward / unit lookup
- Bed availability lookup
- Bed eligibility evaluation
- Bed assignment
- Assignment lookup
- Escalation creation
- Bed status updates

The backend must independently validate requests rather than trusting values supplied by the AI layer.

The API should return explicit errors for invalid or conflicting operations.

For assignment conflicts, use an appropriate response such as HTTP `409 Conflict`.

---

## Bed Assignment Domain Model

BedMed should model hospital placement as a constrained matching problem rather than a simple available / occupied lookup.

### Ward / Unit

A ward or unit should include, at minimum:

- `id`
- `name`
- `ward_type`
- `status`

Example ward types may include:

- `MEDICAL`
- `TELEMETRY`
- `ICU`
- `SURGICAL`

A ward may be unavailable even when individual beds exist.

### Room

A room should include, at minimum:

- `id`
- `ward_id`
- `room_number`
- `capacity`
- `room_type`
- `compatibility_rule`

Recommended room types:

- `PRIVATE`
- `SHARED`

Room-sharing compatibility must be configurable rather than assumed globally.

### Bed

A bed should include, at minimum:

- `id`
- `room_id`
- `bed_number`
- `bed_type`
- `status`
- `isolation_capable`
- `monitoring_capable`

Recommended bed statuses:

- `AVAILABLE`
- `OCCUPIED`
- `CLEANING`
- `RESERVED`
- `OUT_OF_SERVICE`

Do not model availability as a simple boolean.

A bed marked `AVAILABLE` may still be ineligible for a specific patient.

### Patient Placement Requirements

Patient placement data should include the information needed to determine whether a bed is appropriate.

Relevant fields may include:

- `patient_id`
- `required_ward_type`
- `acuity`
- `isolation_required`
- `monitoring_required`
- `sex`
- `gender_identity`
- `requires_private_room`

Use only entirely fictional patient information.

Do not use real patient data or protected health information.

---

## Bed Availability vs. Eligibility

The system must distinguish between:

**Availability** — whether a bed is operationally free to be considered.

**Eligibility** — whether that bed is appropriate for this particular patient.

A bed may be available but ineligible because of:

- Ward / level-of-care mismatch
- Bed operational status
- Reservation state
- Isolation requirements
- Monitoring requirements
- Shared-room compatibility
- Private-room requirements
- Hospital-specific placement policy

Eligibility should be deterministic and policy-driven.

Do not ask an LLM to decide whether a bed is clinically or operationally eligible when deterministic business rules can make the decision.

---

## Shared-Room Compatibility

Shared-room placement rules must be configurable hospital policy.

Do not hard-code universal assumptions about gender or sex.

For the MVP, a mock hospital may use a simple shared-room compatibility rule based on patient sex, but the architecture must treat this as configurable hospital policy rather than a universal medical rule.

Private rooms should not require roommate compatibility checks.

If a shared room is partially occupied, candidate placement should evaluate the configured room-sharing policy against current roommate placement data.

If room-sharing compatibility cannot be safely determined, escalate or require human review rather than guessing.

---

## Bed Eligibility Flow

The expected matching process is:

```text
Find potentially available beds
        ↓
Filter by compatible ward / level of care
        ↓
Filter by operational status
        ↓
Filter by required capabilities
        ↓
Apply room-sharing / placement policy
        ↓
Rank valid candidates
        ↓
Attempt assignment
        ↓
Backend transaction revalidates eligibility
        ↓
Confirm success or return conflict
```

Keep **eligibility** separate from **ranking**.

Eligibility determines whether a bed may be used.

Ranking determines which eligible bed is preferred.

For the MVP, ranking should stay simple and deterministic.

---

## Assignment and Concurrency

The API must revalidate bed eligibility at assignment time.

This is important because system state may change between bed search and assignment.

Example:

1. The assistant finds Bed 5E-103 as available.
2. Another process assigns that bed.
3. BedMed attempts the assignment.
4. The backend returns `409 Conflict`.
5. The assistant refreshes eligible beds.
6. The workflow either proposes another valid bed or escalates.

Never assume that data returned by a previous lookup is still current.

The backend is the source of truth for whether an assignment actually succeeded.

---

## Escalation

If no eligible bed exists, the system must escalate rather than silently relaxing requirements.

Possible escalation reasons include:

- No compatible ward capacity
- No isolation-capable bed
- No monitoring-capable bed
- Shared-room incompatibility
- Private-room requirement cannot be met
- All otherwise suitable beds are occupied
- Beds are reserved
- Beds are cleaning
- Beds are out of service
- Ward is unavailable
- Backend system is unavailable
- Placement policy requires human review

Human escalation should be an explicit workflow state.

The assistant should clearly communicate that no assignment has occurred unless the backend confirms one.

---

## AI Usage

Be explicit about where AI is used and where deterministic logic is used.

Good AI use cases include:

- Intent recognition
- Structured entity extraction
- Natural-language clarification
- Interpreting user phrasing
- Policy retrieval
- Policy summarization

Prefer structured model output using Pydantic models whenever practical.

Do not rely on free-form LLM output when structured output can be used.

Do not place critical business rules solely inside prompts.

---

## LangGraph

Use LangGraph for explicit workflow state and transitions.

Favor a small, understandable graph over complex autonomous behavior.

Workflow state should make it clear:

- What the user is trying to do
- Which required entities have been collected
- Which entities are still missing
- Which backend operations have occurred
- Which tool calls succeeded or failed
- Whether an assignment has been confirmed
- Whether escalation is required
- Why escalation was required

Avoid unnecessary autonomous planning.

The workflow should be explainable visually as a state machine or graph.

---

## RAG and Policy Retrieval

RAG may be used for unstructured hospital policies and guidelines.

Examples include:

- Bed assignment policy
- Isolation policy
- Capacity escalation policy
- Room-sharing policy
- Unit placement guidance

Use RAG for unstructured knowledge.

Do not use RAG as the source of truth for real-time operational state such as:

- Bed availability
- Bed status
- Patient assignments
- Current ward capacity

Those values belong in backend APIs and PostgreSQL.

Keep the RAG implementation lightweight for the MVP.

---

## Failure Handling

Treat failure paths as first-class behavior.

At minimum, consider:

- Missing required information
- Ambiguous user input
- Contradictory input
- Unknown patient
- Bed exists but is not eligible
- Ward is incompatible
- Bed is occupied
- Bed is cleaning
- Bed is reserved
- Bed is out of service
- Isolation requirements cannot be met
- Monitoring requirements cannot be met
- Shared-room placement policy rejects the bed
- No eligible beds exist
- Bed becomes unavailable between search and assignment
- Backend API failure
- Database failure
- LLM failure
- Tool invocation failure
- Policy retrieval failure

Never fabricate success.

Never imply that an action happened when it did not.

When a backend action fails, communicate the failure accurately and preserve safe workflow state.

---

## Database

PostgreSQL is the system of record for mock operational data.

Use realistic but entirely fictional data.

The database may include entities such as:

- Patients
- Wards
- Rooms
- Beds
- Bed assignments
- Reservations
- Escalations

Keep the schema appropriate for the MVP.

Do not build unnecessary healthcare-domain complexity.

Database access belongs behind the FastAPI backend.

The conversational AI layer must not query PostgreSQL directly.

---

## Docker

The development environment should be easy to start locally.

Prefer:

```bash
docker compose up --build
```

for infrastructure such as FastAPI and PostgreSQL.

Keep Docker configuration understandable and lightweight.

Do not introduce production orchestration infrastructure for this prototype.

---

## Testing Strategy

Build the MVP first, but include tests for important business logic as functionality stabilizes.

Prioritize tests for:

- API behavior
- Patient lookup
- Ward compatibility
- Bed status filtering
- Bed availability
- Bed eligibility
- Isolation eligibility
- Monitoring eligibility
- Shared-room compatibility
- Private-room placement
- Bed assignment
- Invalid assignments
- Reservation conflicts
- Out-of-service beds
- No eligible bed leading to escalation
- Race conditions during assignment
- Workflow transitions
- Missing information
- Ambiguous information
- Escalation paths

Use `pytest`.

### Manual Verification

Manual verification may use:

- FastAPI Swagger / OpenAPI interface
- `curl`
- Postman or equivalent API clients
- Application UI
- CLI interaction

Document meaningful manual test scenarios when appropriate.

Do not use Django-specific testing or admin workflows for this project.

---

## Code Quality

All new code should:

- Be readable
- Be appropriately typed
- Follow normal Python conventions
- Pass Ruff linting
- Avoid unnecessary duplication
- Use descriptive names
- Handle errors explicitly
- Prefer small functions with clear responsibilities

Do not suppress linting or type issues merely to make checks pass unless there is a documented reason.

Prefer clarity over cleverness.

---

## Security and Privacy

Review code for security and privacy concerns as part of every implementation.

In particular:

- Never commit `.env` files.
- Never commit secrets.
- Never hard-code API keys.
- Never expose database credentials to client-side code.
- Validate external input.
- Use parameterized database access through SQLAlchemy.
- Avoid logging sensitive information unnecessarily.
- Use only fictional patient data.
- Do not include real PHI.
- Treat tool calls as constrained privileged operations.
- Avoid exposing raw internal exceptions through public API responses.
- Keep operational actions behind backend validation.

This is a healthcare-themed prototype.

It does **not** process real PHI and must not claim HIPAA compliance.

Security practices should be sensible and production-oriented without pretending this prototype has completed regulatory certification or compliance review.

---

## Observability and Evaluation

Design the system so that important workflow events can be observed and evaluated.

Useful operational signals may include:

- Workflow started
- Intent detected
- Required information missing
- Bed search performed
- Number of eligible beds found
- Assignment attempted
- Assignment succeeded
- Assignment conflicted
- Escalation created
- Tool failure
- LLM failure

Avoid logging sensitive patient information when an identifier is sufficient.

Future AI evaluation may consider:

- Intent classification accuracy
- Entity extraction accuracy
- Clarification effectiveness
- Tool selection accuracy
- Hallucination rate
- Workflow completion rate

Business-oriented metrics may include:

- Time from admission request to bed assignment
- Successful assignment rate
- Escalation rate
- Manual coordination effort
- Number of conversational turns required

Do not overbuild observability infrastructure for the MVP.

---

## Project Structure

Prefer a structure similar to:

```text
bedmed/
├── README.md
├── CLAUDE.md
├── docker-compose.yml
├── .env.example
│
├── api/
│   ├── main.py
│   ├── database.py
│   ├── models/
│   ├── schemas/
│   ├── routes/
│   ├── services/
│   └── Dockerfile
│
├── assistant/
│   ├── main.py
│   ├── agents/
│   ├── models/
│   ├── prompts/
│   ├── tools/
│   ├── workflows/
│   └── knowledge/
│
├── db/
│   └── seed/
│
├── policies/
│
├── docs/
│   └── TODO.md
│
└── tests/
```

This structure is guidance, not an immutable requirement.

Do not create empty folders merely to match the diagram.

Create structure as features require it.

---

## Documentation

Update documentation when behavior or architecture materially changes.

`README.md` should eventually explain:

- What BedMed does
- Architecture
- Technology stack
- Setup instructions
- Running the application
- Demo scenarios
- Where AI is used
- Where deterministic logic is used
- Testing
- Design tradeoffs
- Known limitations
- Future extensions

Use `docs/TODO.md` for implementation planning rather than long-term product documentation.

Do not copy proprietary interview instructions or company-specific material into the public repository.

BedMed should stand on its own as an independent healthcare operations portfolio project.

---

## Definition of Done

Before considering a task complete:

1. The requested behavior works.
2. Important failure cases are handled.
3. Relevant tests pass.
4. New code passes linting.
5. No secrets or sensitive information were introduced.
6. Backend actions are validated server-side.
7. The assistant does not claim unconfirmed actions succeeded.
8. Documentation is updated when necessary.
9. Completed tasks are checked off in `docs/TODO.md`.

---

## Final Development Reminder

Keep BedMed simple enough to understand completely.

The goal is not to demonstrate how many technologies can be added.

The goal is to demonstrate strong engineering judgment:

- AI where AI adds value
- Deterministic logic where reliability matters
- Explicit workflow state
- Constrained tools
- Validated APIs
- Durable operational state
- Clear failure handling
- Thoughtful healthcare-domain modeling
- Clean, explainable code
