# BedMed: Optimization and Scalability

How this prototype would be measured, improved and grown. Each section says what exists today and what comes next.

## 1. Monitoring and improving performance

**Today**

- Every workflow step records one structured event: intent detected, details missing, bed search with counts, assignment attempted, succeeded or conflicted, escalation created, tool failure, LLM failure. Events carry identifiers and counts, never patient names.
- The same events feed the Logs page and the trace shown under each reply.
- 133 automated tests cover the eligibility rules, the API, a real two-request race for one bed, and the workflow's paths. Workflow tests replace the LLM with a scripted stand-in, so they are fast, free and repeatable.

**Next**

- **Evaluate the LLM step on its own.** Build a labelled set of real phrasings with the expected intent and details, and score it on every prompt or model change. The output is a small schema, so scoring is exact match per field; no judge model is needed.
- **Track four kinds of metric:**
  - *Model:* intent accuracy, per-detail accuracy, rate of "not understood".
  - *Conversation:* turns per completed request, clarification rate, abandoned requests.
  - *Operations:* time from request to assignment, conflict rate, escalation rate and reasons.
  - *Reliability and cost:* latency, error rate and tokens per turn.
- **Close the loop.** Review misunderstood turns and nurse corrections, add them to the evaluation set, then change the prompt. A change ships only if the set does not regress.
- **Trace across services** with a correlation ID from the UI through the workflow to the API, exported to a standard collector such as OpenTelemetry.
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

- **One graph per workflow, one router in front.** Operating-room scheduling and pharmacy refills each become a small graph with their own details, prompt, tools and templates. A first step classifies the message and hands it to the right graph, so each workflow stays small enough to test fully.
- **Share the pattern, not the rules.** "Collect details, propose, confirm, act, handle conflict, escalate" has the same shape in all three scenarios and becomes a shared building block. Business rules stay with each backend.
- **Make the service stateless.** Move conversation state from memory to a durable store (LangGraph has PostgreSQL and Redis checkpointers), so any instance can serve any turn.
- **Configure per hospital.** Room-sharing policy is already data, not code. Ward types, required details and escalation routes would move the same way.
- **Add what a deployment needs:** user identity and roles, an audit record of who confirmed what, rate limits, and idempotency keys so a retried request cannot assign twice.

## 3. Using RAG for policies and guidelines

Retrieval is not built in this prototype. This is how it would be added.

- **A strict boundary.** Retrieval answers questions about written policy ("when is a private room required?"). Live facts such as bed status always come from the API.
- **A separate intent.** A policy question routes to its own node and cannot trigger an assignment.
- **Ingest:** split documents by section, keeping title, version and effective date with each chunk.
- **Store:** PostgreSQL with pgvector is enough at this scale and avoids another system. A dedicated vector database earns its place with many hospitals or millions of chunks.
- **Retrieve:** combine keyword and vector search, because policy numbers and exact terms matter. Filter by hospital and effective date so a superseded policy is never quoted.
- **Answer:** only from the retrieved passages, citing document and section, and saying so when nothing relevant was found.
- **Evaluate separately:** was the right passage retrieved, and is the answer supported by it?
- **Policy informs; code decides.** Retrieved text can explain a rule. It never overrides the eligibility rules in the API.

## 4. Robustness to ambiguous or incomplete input

**Today**

- **Unknown is not "no".** Each detail starts unknown. The assistant asks for whatever is missing, and the API refuses a search that omits a need.
- **Context for short answers.** The model is told what was just asked, so "no" or "the first one" is read correctly.
- **Never choose for the user.** A name matching several patients produces a numbered list and a question.
- **Verify, don't trust.** Patient IDs are checked with the API, and eligibility is re-checked when the assignment is written.
- **Confirm before any write.** A correction after a proposal discards it; a reply that both confirms and changes a detail does not assign.
- **Fail closed.** Invalid model output, or an unavailable LLM or API, produces a clear message and leaves state untouched. Success is reported only from an API success response.
- **Manipulation.** Text that tries to override the rules is treated as not understood, and the model has no ability to assign a bed or write a reply.

**Next**

- **Confidence:** have the model flag uncertain details so the assistant confirms them instead of accepting them silently.
- **Contradictions:** detect conflicting statements in one message and ask.
- **Vocabulary:** map local abbreviations per hospital and grow the examples from real transcripts.
- **Degrade gracefully:** if the LLM is down, offer a structured form for the same details.
- **A way out:** hand off to a person after repeated failed clarifications.
