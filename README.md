# BedMed

A prototype AI assistant for hospital patient admission and bed assignment. A nurse describes what a patient needs in plain language; the assistant finds an eligible bed, confirms it, assigns it, and escalates to a human when no bed qualifies.

**The LLM interprets. The workflow decides. Tools act. APIs confirm.**

All patient data is fictional. This is a prototype: it does not process real health information and makes no compliance claims.

## Status

| Part | State |
| --- | --- |
| Backend API (FastAPI + PostgreSQL) | Working |
| Conversational assistant (LangGraph) | Working |
| Web UI (chat, bed board, workflow, logs) | Working |
| Design and scalability notes | Written |
| Policy questions (RAG over fictional policies) | Working |

## Documents

- [Design](docs/design.md): flows, the conversation state machine, intents and entities, and where AI is used.
- [Optimization and scalability](docs/scalability.md): monitoring and evaluation, more workflows, RAG, and robustness.
- [Plan](docs/TODO.md): what was built, in order.

## Run it

Requires Docker and `make`. Run `make help` to list every command; each one is a thin wrapper around `docker compose`, defined in the [Makefile](Makefile).

```bash
cp .env.example .env
```

```bash
make up
```

This starts three services and seeds the database with fictional data on first start:

| Service | Address |
| --- | --- |
| Web UI | <http://localhost:8080> |
| Hospital API, with interactive docs | <http://localhost:8000/docs> |
| PostgreSQL | internal to Docker only |

To reset the database to the seed data:

```bash
make reset
```

To stop everything:

```bash
make down
```

## Use the web UI

Put an [OpenRouter](https://openrouter.ai) key in `.env` as `OPENROUTER_API_KEY`, run `make up`, and open <http://localhost:8080>.

| Page | What it shows |
| --- | --- |
| Demo | The chat beside a live bed board, with waiting patients and escalations. Each reply has a trace of the workflow steps it took, marked LLM, tool or logic |
| Workflow | The state machine, drawn from the running LangGraph definition |
| Design, Scalability, Policies, README | The written deliverables and the policy documents, rendered from the Markdown files in this repository |
| Logs | Recent workflow events |

To demonstrate a conflict: ask for a bed, wait for the proposal, use the menu on that bed to mark it reserved (this plays the part of another hospital system), then say "yes". The assistant reports the conflict and proposes the next bed.

"Reset demo data" restores the seed data and clears the conversation. The UI is a local demonstration and has no login.

## Chat in the terminal

The same assistant is available without the UI:

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
| "find a medical bed for Alvarez, no special needs" | Two patients match, so it shows a numbered list and asks which one; answer "the first one", a name or an ID |
| "who's still waiting for a bed?", "what beds are free?", "show me the occupied beds" | Lists patients or beds without disturbing a request in progress |
| "how long can a bed stay reserved?", "when is a private room required?" | Answers from the policy documents and lists the sections it used |
| "ICU bed for P-1003, on a monitor, no isolation, can share" | No eligible bed, so it escalates and says nothing was assigned |
| "tele bed for P-1002, on a monitor, no isolation, can share", then "actually he needs contact precautions" | The changed need discards the proposal and searches again |

The default model is `anthropic/claude-haiku-5.5`, and policy retrieval uses `openai/text-embedding-3-small`, both through OpenRouter. Set `BEDMED_MODEL` or `BEDMED_EMBEDDING_MODEL` in `.env` to change them; the chat model must support structured outputs.

## Test it

```bash
make test
```

Tests run against a separate `bedmed_test` database in the same PostgreSQL container, so the race-condition test uses real row locks. The workflow tests replace the LLM with a scripted extractor and use the real API, so the suite needs no API key.

Evaluate the LLM steps against labelled cases (this calls the real models, so it needs the API key):

```bash
make eval
```

- **Extraction:** [evals/extraction_cases.jsonl](evals/extraction_cases.jsonl) has 57 messages, each with the conversation context and the exact intent and details expected. Scoring is exact match per field. Current result: 57 of 57.
- **Policy retrieval:** [evals/policy_cases.jsonl](evals/policy_cases.jsonl) has 13 questions, each with the policy section that answers it, or none. Retrieval and answers are scored separately. Current result: the expected section is retrieved for 9 of 10 answerable questions and 12 of 13 answers are correct, so the command reports one failure.

Both sets are small and were written alongside the prompts; they guard against regressions and are not a measure of real-world accuracy.

Lint:

```bash
make lint
```

## Backend

The API owns operational state and validates every request itself; it does not trust what the assistant sends.

| Endpoint | Purpose |
| --- | --- |
| `GET /patients` | List patients; filter by part of a name or by whether they have a bed |
| `GET /patients/{id}` | Patient lookup, including the current bed |
| `GET /beds` | List beds with status and occupant; filter by status |
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
| Word replies about patients and beds | Templates filled from API data | [assistant/messages.py](assistant/messages.py) |
| Answer a policy question | Vector search over [policies/](policies/), then the LLM, limited to the retrieved sections | [assistant/policy.py](assistant/policy.py) |

Every message goes through one LLM step, `interpret`, which only fills in a form. That step cannot choose a bed, call a tool, or write a reply, so it cannot claim an assignment that did not happen. The assistant has no database access.

The one other LLM step answers policy questions. It is read-only, sees only the retrieved policy sections, and its answer is shown only if it cites a section that was actually retrieved. The reply is labelled as policy text and lists its sources.

AI assistants were also used to build this project: the working agreement in `CLAUDE.md` was drafted with ChatGPT, and the code, tests and documents were written with Claude Code under review. The [design document](docs/design.md#where-ai-is-used) has the detail.

**Intents:** request a bed, provide information, confirm, decline, patient status, list patients, list beds, policy question, cancel, unknown.

**Entities:** patient ID or name, ward type, isolation, cardiac monitoring, private room, plus list filters (has a bed, bed status). An unstated detail stays unknown and is asked for; it is never assumed to be "no".

**Names:** a name is only a way to find a patient ID. One match is used and shown back before anything is assigned; no match or several matches are reported and the assistant asks, never choosing for the user. When the user picks from the numbered list ("the second one"), the LLM extracts only the position and the workflow maps it to the patient that was shown.

**Lists:** the bed list shows operational status. An available bed is free, not necessarily eligible for a given patient, and the reply says so.
