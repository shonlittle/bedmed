# BedMed: Optimization and Scalability

How this prototype would be measured, improved and grown. Each section says what exists today and what comes next.

## 1. Monitoring and improving performance

**Today**

- Every workflow step records one structured event, such as intent detected, bed search with counts, assignment conflicted, escalation created, or LLM failure. Events carry identifiers and counts, never patient names. They feed the Logs page and the trace under each reply.
- 176 automated tests cover the eligibility rules, the API, a real two-request race for one bed, and the workflow's paths. Workflow tests replace the LLM with a scripted stand-in, so they are fast, free and repeatable.
- The LLM step is evaluated on its own. `make eval` runs 57 labelled messages through the real model and scores each field by exact match. The current prompt and model score 57 of 57. The set is small and was written alongside the prompt, so this shows the loop works; it is not a measure of accuracy on real nurse phrasing.

**Next**

- **Grow the evaluation set from real use,** especially misunderstood phrasings, and run it on every prompt or model change.
- **Track four kinds of metric:** model (intent and per-detail accuracy), conversation (turns per request, clarification rate), operations (time to assignment, conflict and escalation rates), and reliability and cost (latency, errors, tokens per turn).
- **Ship on evidence.** A prompt change ships only if the evaluation set does not regress.
- **Trace across services** with a correlation ID from the UI to the API, exported through OpenTelemetry.
- **Roll out carefully:** version the prompt, pin the model, and try changes on a share of traffic first.

## 2. Scaling to more workflows

The code already separates what is general from what is specific to bed assignment.

| Reusable as it is | Specific to this workflow |
| --- | --- |
| Extract, route, act, reply | The intents and details |
| Schema-constrained extraction | The prompt and its examples |
| Tool client with typed errors | The API endpoints called |
| Clarify, confirm before writing, retry on conflict, escalate | The eligibility rules |
| Events, trace, test approach | The reply templates |

**Next**

- **One graph per workflow, one router in front.** Operating-room scheduling or pharmacy refills each become a small graph with their own details, prompt, tools and templates. A first step classifies the message and hands it to the right graph.
- **Share the pattern, not the rules.** "Collect details, propose, confirm, act, handle conflict, escalate" becomes a shared building block. Business rules stay with each backend.
- **Make the service stateless.** Move conversation state from memory to a durable store (LangGraph has PostgreSQL and Redis checkpointers), so any instance can serve any turn.
- **Configure per hospital.** Room-sharing policy is already data, not code. Ward types, required details and escalation routes would move the same way.
- **Add what a deployment needs:** user identity and roles, an audit record of who confirmed what, rate limits, and idempotency keys so a retried request cannot assign twice.

## 3. Using RAG for policies and guidelines

**Today**

- **A strict boundary.** Retrieval answers questions about written policy: five fictional documents, one chunk per section, 24 in all. Live facts such as bed status always come from the API.
- **A separate, read-only intent.** A policy question cannot start or change a placement.
- **Retrieve, answer, verify.** The three closest sections go to the LLM, which must answer from them alone and name the ones it used. Code drops any citation that was not retrieved; an answer with no valid citation becomes "I couldn't find that".
- **Labelled output.** Every policy answer lists its sources and states that it is not live bed status.
- **Evaluated separately.** `make eval` runs 13 policy questions and scores retrieval and answers apart. The expected section is retrieved for 9 of 10 answerable questions, and 12 of 13 answers are correct, including three uncovered questions that are correctly declined. The one miss is a retrieval miss: a question about dropping a monitoring requirement finds the monitoring policy, not the "requirements are never relaxed" section.

**Next**

- **Hybrid search.** Combine keyword and vector search, since exact terms matter. This is the likely fix for the miss above.
- **A real store.** PostgreSQL with pgvector is enough for one hospital and avoids another system.
- **Versions and dates.** Filter by hospital and effective date so a superseded policy is never quoted.
- **Policy informs; code decides.** Retrieved text explains a rule. It must never override the eligibility rules in the API.

## 4. Robustness to ambiguous or incomplete input

**Today**

- **Unknown is not "no".** Each detail starts unknown. The assistant asks for whatever is missing, and the API refuses a search that omits a need.
- **Context for short answers.** The model is told what was just asked, so "no" or "the first one" is read correctly. A name matching several patients produces a numbered list, never a guess.
- **Verify, don't trust.** Patient IDs are checked with the API, and eligibility is re-checked when the assignment is written.
- **Confirm before any write.** A correction after a proposal discards it; a reply that both confirms and changes a detail does not assign.
- **Fail closed.** Invalid model output, or an unavailable LLM or API, produces a clear message and leaves state untouched. Success is reported only from an API success response.
- **Manipulation.** Text that tries to override the rules is treated as not understood, and the model cannot assign a bed.

**Next**

- **Confidence and contradictions:** have the model flag uncertain or conflicting details so the assistant asks.
- **Degrade gracefully:** if the LLM is down, offer a structured form for the same details.
- **A way out:** hand off to a person after repeated failed clarifications.
