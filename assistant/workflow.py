"""The admission workflow as an explicit LangGraph state machine.

The graph runs once per user message; state persists between messages through the checkpointer.
The LLM is used in exactly one node (`interpret`). Every decision after that is ordinary code,
and every change to hospital state goes through a tool that calls the API.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from assistant import messages
from assistant.extractor import ExtractionContext, ExtractionError, Extractor
from assistant.models import SLOT_NAMES, BedStatus, Extraction, Intent, Slots, WardType
from assistant.policy import PolicyError, PolicyGuide
from assistant.tools import ApiError, ConflictError, HospitalApi, NotFoundError

logger = logging.getLogger("bedmed.workflow")

MIN_NAME_LENGTH = 2


class Stage(StrEnum):
    IDLE = "IDLE"
    COLLECTING = "COLLECTING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"


class Outcome(StrEnum):
    """How a node finished, for the edge that follows it."""

    CONTINUE = "CONTINUE"
    STOP = "STOP"
    CONFLICT = "CONFLICT"


class WorkflowState(TypedDict):
    # This turn
    user_message: str
    extraction: Extraction | None
    outcome: Outcome
    notice: str
    reply: str
    event: str
    # The request in progress
    stage: Stage
    slots: Slots
    patient: dict | None
    candidates: list[dict]
    near_misses: list[dict]
    rejection_summary: dict[str, int]
    proposed_bed_id: str | None
    declined_bed_ids: list[str]
    patient_options: list[dict]


def fresh_request() -> dict:
    """State for a conversation with no request in progress."""
    return {
        "stage": Stage.IDLE,
        "slots": Slots(),
        "patient": None,
        "candidates": [],
        "near_misses": [],
        "rejection_summary": {},
        "proposed_bed_id": None,
        "declined_bed_ids": [],
        "patient_options": [],
    }


def next_candidate(state: WorkflowState) -> dict | None:
    """The best eligible bed the user has not declined."""
    return next((bed for bed in state["candidates"] if bed["id"] not in state["declined_bed_ids"]), None)


# Lookups that answer a question without touching the request in progress.
READ_ONLY_NODES = {
    Intent.PATIENT_STATUS: "patient_status",
    Intent.LIST_PATIENTS: "list_patients",
    Intent.LIST_BEDS: "list_beds",
    Intent.POLICY_QUESTION: "answer_policy",
}


def picks_an_option(state: WorkflowState) -> bool:
    """The user chose by position from the numbered list of patients they were shown."""
    return bool(state["patient_options"]) and state["extraction"].choice_number is not None


def changes_slots(state: WorkflowState) -> bool:
    return state["slots"].merged_with(state["extraction"].slots()) != state["slots"]


# --- Routing: pure functions of state --------------------------------------------------------


def route_after_interpret(state: WorkflowState) -> str:
    extraction = state["extraction"]
    if extraction is None:
        return END
    if extraction.intent is Intent.CANCEL:
        return "cancel"
    if extraction.intent in READ_ONLY_NODES:
        return READ_ONLY_NODES[extraction.intent]
    if changes_slots(state) or extraction.patient_name or picks_an_option(state):
        # New or corrected details always restart the search, even while a bed awaits confirmation.
        return "collect"
    if state["stage"] is Stage.AWAITING_CONFIRMATION:
        if extraction.intent is Intent.CONFIRM:
            return "assign"
        if extraction.intent is Intent.DECLINE:
            return "decline"
    if extraction.intent is Intent.REQUEST_BED:
        return "collect"
    return "fallback"


def route_after_identify(state: WorkflowState) -> str:
    if state["outcome"] is Outcome.STOP:
        return END
    return "ask_missing" if state["slots"].missing() else "search_beds"


def route_to_proposal(state: WorkflowState) -> str:
    if state["outcome"] is Outcome.STOP:
        return END
    return "propose" if next_candidate(state) else "escalate"


def route_after_assign(state: WorkflowState) -> str:
    return "search_beds" if state["outcome"] is Outcome.CONFLICT else END


# --- Nodes ------------------------------------------------------------------------------------


def record(name: str, level: int = logging.INFO, **fields: object) -> str:
    """Log one workflow event and return it as text, so the same line serves the logs and the per-turn trace.
    Fields are identifiers and counts only; patient names are never logged."""
    text = " ".join([name, *(f"{key}={value}" for key, value in fields.items())])
    logger.log(level, text)
    return text


def describe_extraction(extraction: Extraction) -> dict:
    """What the LLM extracted, with the patient name withheld."""
    stated = extraction.model_dump(mode="json", exclude_none=True, exclude={"intent"})
    if "patient_name" in stated:
        stated["patient_name"] = "(given)"
    return stated


class Nodes:
    def __init__(self, extractor: Extractor, api: HospitalApi, policy_guide: PolicyGuide | None = None) -> None:
        self._extractor = extractor
        self._api = api
        self._policy_guide = policy_guide

    def interpret(self, state: WorkflowState) -> dict:
        """The only LLM step: turn the message into an intent and entities."""
        collecting = state["stage"] is Stage.COLLECTING
        context = ExtractionContext(
            stage=state["stage"],
            pending_slots=state["slots"].missing() if collecting else [],
            proposed_bed_id=state["proposed_bed_id"],
            option_count=len(state["patient_options"]),
        )
        try:
            extraction = self._extractor.extract(state["user_message"], context)
        except ExtractionError as error:
            event = record("llm_failure", logging.WARNING, error=error)
            return {"extraction": None, "notice": "", "reply": messages.LLM_FAILURE, "event": event}
        event = record("intent_detected", intent=extraction.intent, **describe_extraction(extraction))
        return {"extraction": extraction, "notice": "", "event": event}

    def collect(self, state: WorkflowState) -> dict:
        """Merge stated details into the slots. Any earlier search or proposal is discarded."""
        slots = state["slots"].merged_with(state["extraction"].slots())
        same_patient = slots.patient_id == state["slots"].patient_id
        known = [name for name in SLOT_NAMES if getattr(slots, name) is not None]
        return {
            **fresh_request(),
            "stage": Stage.COLLECTING,
            "slots": slots,
            "patient": state["patient"] if same_patient else None,
            "patient_options": state["patient_options"],
            "event": record("details_collected", known=",".join(known) or "none"),
        }

    def identify_patient(self, state: WorkflowState) -> dict:
        """Establish exactly which patient this is. A name is only a way to find an ID:
        one match is used; none or several are reported and the user is asked to choose."""
        slots, patient, options, notice = state["slots"], state["patient"], state["patient_options"], ""
        extraction = state["extraction"]
        event = ""
        try:
            if extraction.patient_id:
                pass  # An explicit ID always wins over a name or a list position.
            elif extraction.patient_name:
                matches = self._find_by_name(extraction.patient_name)
                event = record("patient_name_lookup", matches=len(matches))
                patient = matches[0] if len(matches) == 1 else None
                options = matches[: messages.MAX_NAME_MATCHES_SHOWN] if len(matches) > 1 else []
                notice = "" if patient else messages.name_matches(extraction.patient_name, matches)
                slots = slots.model_copy(update={"patient_id": patient["id"] if patient else None})
            elif picks_an_option(state):
                patient = self._chosen_option(options, extraction.choice_number)
                event = record("patient_option_chosen", choice=extraction.choice_number, valid=patient is not None)
                notice = "" if patient else messages.choice_out_of_range(options)
                slots = slots.model_copy(update={"patient_id": patient["id"] if patient else None})
            if slots.patient_id and patient is None:
                patient = self._api.get_patient(slots.patient_id)
        except NotFoundError:
            event = record("patient_not_found", patient=slots.patient_id)
            notice = messages.patient_not_found(slots.patient_id)
            slots = slots.model_copy(update={"patient_id": None})
        except ApiError as error:
            event = record("tool_failure", logging.ERROR, tool="identify_patient", code=error.code)
            return {"reply": messages.BACKEND_UNAVAILABLE, "outcome": Outcome.STOP, "event": event}

        if patient and patient["current_bed_id"]:
            event = record("patient_already_placed", patient=patient["id"], bed=patient["current_bed_id"])
            reply = messages.already_placed(patient)
            return {**fresh_request(), "reply": reply, "outcome": Outcome.STOP, "event": event}
        if patient:
            event = record("patient_identified", patient=patient["id"])
        return {
            "slots": slots,
            "patient": patient,
            "patient_options": [] if patient else options,
            "notice": notice,
            "outcome": Outcome.CONTINUE,
            "event": event or record("patient_not_identified"),
        }

    @staticmethod
    def _chosen_option(options: list[dict], choice_number: int) -> dict | None:
        """Map a list position to the patient shown at that position, or None if it is out of range."""
        return options[choice_number - 1] if 1 <= choice_number <= len(options) else None

    def _find_by_name(self, name: str) -> list[dict]:
        cleaned = name.strip()
        return self._api.find_patients(cleaned) if len(cleaned) >= MIN_NAME_LENGTH else []

    def ask_missing(self, state: WorkflowState) -> dict:
        missing = state["slots"].missing()
        return {
            "reply": messages.join(state["notice"], messages.ask_for_missing(missing)),
            "event": record("slots_missing", missing=",".join(missing)),
        }

    def search_beds(self, state: WorkflowState) -> dict:
        slots = state["slots"]
        try:
            result = self._api.search_beds(slots.patient_id, slots.requirements())
        except ApiError as error:
            event = record("tool_failure", logging.ERROR, tool="search_beds", code=error.code)
            reply = messages.join(state["notice"], messages.BACKEND_UNAVAILABLE)
            return {"reply": reply, "outcome": Outcome.STOP, "event": event}
        return {
            "candidates": result["eligible"],
            "near_misses": result["rejected"],
            "rejection_summary": result["rejection_summary"],
            "outcome": Outcome.CONTINUE,
            "event": record(
                "bed_search",
                patient=slots.patient_id,
                eligible=len(result["eligible"]),
                rejected=len(result["rejected"]),
            ),
        }

    def propose(self, state: WorkflowState) -> dict:
        """Offer the best bed and wait for the user. Nothing is written until they confirm."""
        bed = next_candidate(state)
        remaining = [b for b in state["candidates"] if b["id"] not in state["declined_bed_ids"]]
        proposal = messages.proposal(state["patient"], bed, len(remaining) - 1, state["near_misses"])
        return {
            "stage": Stage.AWAITING_CONFIRMATION,
            "proposed_bed_id": bed["id"],
            "reply": messages.join(state["notice"], proposal),
            "event": record("bed_proposed", bed=bed["id"], awaiting="confirmation"),
        }

    def assign(self, state: WorkflowState) -> dict:
        """Attempt the assignment. Only a success response from the API counts as assigned."""
        slots, bed_id = state["slots"], state["proposed_bed_id"]
        ids = {"patient": slots.patient_id, "bed": bed_id}
        record("assignment_attempted", **ids)
        try:
            assignment = self._api.assign_bed(slots.patient_id, bed_id, slots.requirements())
        except ConflictError as error:
            event = record("assignment_conflicted", **ids, code=error.code, reasons=",".join(error.reasons) or "none")
            if error.code == "PATIENT_ALREADY_ASSIGNED":
                reply = messages.assignment_refused(str(error))
                return {**fresh_request(), "reply": reply, "outcome": Outcome.STOP, "event": event}
            return {
                "stage": Stage.COLLECTING,
                "proposed_bed_id": None,
                "notice": messages.conflict(bed_id, error.reasons),
                "outcome": Outcome.CONFLICT,
                "event": event,
            }
        except ApiError as error:
            event = record("tool_failure", logging.ERROR, tool="assign_bed", code=error.code)
            return {"reply": messages.assignment_unconfirmed(bed_id), "outcome": Outcome.STOP, "event": event}
        event = record("assignment_succeeded", **ids, assignment=assignment["id"])
        reply = messages.assigned(state["patient"], assignment)
        return {**fresh_request(), "reply": reply, "outcome": Outcome.STOP, "event": event}

    def decline(self, state: WorkflowState) -> dict:
        bed_id = state["proposed_bed_id"]
        return {
            "stage": Stage.COLLECTING,
            "proposed_bed_id": None,
            "declined_bed_ids": [*state["declined_bed_ids"], bed_id],
            "notice": messages.declined(bed_id),
            "outcome": Outcome.CONTINUE,
            "event": record("bed_declined", bed=bed_id),
        }

    def escalate(self, state: WorkflowState) -> dict:
        """No eligible bed: hand over to a human. Requirements are never relaxed to force a match."""
        slots = state["slots"]
        reason = messages.escalation_reason(slots, state["rejection_summary"], state["declined_bed_ids"])
        try:
            escalation = self._api.create_escalation(slots.patient_id, slots.requirements(), reason)
        except ApiError as error:
            event = record("tool_failure", logging.ERROR, tool="create_escalation", code=error.code)
            return {"reply": messages.join(state["notice"], messages.escalation_failed(reason)), "event": event}
        event = record("escalation_created", patient=slots.patient_id, escalation=escalation["id"])
        reply = messages.join(state["notice"], messages.escalated(state["patient"], escalation, reason))
        return {**fresh_request(), "reply": reply, "event": event}

    def patient_status(self, state: WorkflowState) -> dict:
        """Read-only lookup by ID or name. It does not disturb a request in progress."""
        extraction = state["extraction"]
        patient_id = extraction.patient_id or (None if extraction.patient_name else state["slots"].patient_id)
        try:
            if patient_id:
                patient = self._api.get_patient(patient_id)
                return {"reply": messages.patient_status(patient), "event": record("patient_status", matches=1)}
            if extraction.patient_name:
                matches = self._find_by_name(extraction.patient_name)
                event = record("patient_status", matches=len(matches))
                if not matches:
                    return {"reply": messages.name_matches(extraction.patient_name, matches), "event": event}
                return {"reply": messages.patient_statuses(matches), "event": event}
        except NotFoundError:
            event = record("patient_not_found", patient=patient_id)
            return {"reply": messages.patient_not_found(patient_id), "event": event}
        except ApiError as error:
            event = record("tool_failure", logging.ERROR, tool="patient_status", code=error.code)
            return {"reply": messages.BACKEND_UNAVAILABLE, "event": event}
        return {"reply": messages.STATUS_NEEDS_PATIENT, "event": record("patient_status", matches=0)}

    def list_patients(self, state: WorkflowState) -> dict:
        has_bed = state["extraction"].has_bed
        try:
            patients = self._api.list_patients(has_bed)
        except ApiError as error:
            event = record("tool_failure", logging.ERROR, tool="list_patients", code=error.code)
            return {"reply": messages.BACKEND_UNAVAILABLE, "event": event}
        event = record("patients_listed", has_bed=has_bed, count=len(patients))
        return {"reply": messages.patient_list(patients, has_bed), "event": event}

    def list_beds(self, state: WorkflowState) -> dict:
        """Lists operational status only. Eligibility for a patient is a different question."""
        status = state["extraction"].bed_status
        try:
            beds = self._api.list_beds(status)
        except ApiError as error:
            event = record("tool_failure", logging.ERROR, tool="list_beds", code=error.code)
            return {"reply": messages.BACKEND_UNAVAILABLE, "event": event}
        event = record("beds_listed", status=status or "any", count=len(beds))
        return {"reply": messages.bed_list(beds, status), "event": event}

    def answer_policy(self, state: WorkflowState) -> dict:
        """Answer a policy question from retrieved policy text. Read-only: it cannot start or change a request."""
        if self._policy_guide is None:
            return {"reply": messages.POLICY_NOT_CONFIGURED, "event": record("policy_not_configured")}
        try:
            result = self._policy_guide.answer(state["user_message"])
        except PolicyError as error:
            event = record("policy_failure", logging.ERROR, error=error)
            return {"reply": messages.POLICY_UNAVAILABLE, "event": event}
        retrieved = ",".join(item.chunk.id for item in result.retrieved)
        cited = ",".join(chunk.id for chunk in result.sources) or "none"
        event = record("policy_answered", found=result.found, retrieved=retrieved, cited=cited)
        if not result.found:
            return {"reply": messages.POLICY_NOT_FOUND, "event": event}
        reply = messages.policy_answer(result.answer, [chunk.citation for chunk in result.sources])
        return {"reply": reply, "event": event}

    def cancel(self, state: WorkflowState) -> dict:
        return {**fresh_request(), "reply": messages.CANCELLED, "event": record("request_cancelled")}

    def fallback(self, state: WorkflowState) -> dict:
        collecting = state["stage"] is Stage.COLLECTING
        reply = messages.fallback(state["proposed_bed_id"], state["slots"].missing(), collecting)
        return {"reply": reply, "event": record("not_understood")}


# --- Graph ------------------------------------------------------------------------------------


def build_graph(extractor: Extractor, api: HospitalApi, policy_guide: PolicyGuide | None = None) -> CompiledStateGraph:
    nodes = Nodes(extractor, api, policy_guide)
    graph = StateGraph(WorkflowState)

    for name in (
        "interpret",
        "collect",
        "identify_patient",
        "ask_missing",
        "search_beds",
        "propose",
        "assign",
        "decline",
        "escalate",
        "patient_status",
        "list_patients",
        "list_beds",
        "answer_policy",
        "cancel",
        "fallback",
    ):
        graph.add_node(name, getattr(nodes, name))

    graph.add_edge(START, "interpret")
    graph.add_conditional_edges(
        "interpret",
        route_after_interpret,
        ["collect", "assign", "decline", *READ_ONLY_NODES.values(), "cancel", "fallback", END],
    )
    graph.add_edge("collect", "identify_patient")
    graph.add_conditional_edges("identify_patient", route_after_identify, ["ask_missing", "search_beds", END])
    graph.add_conditional_edges("search_beds", route_to_proposal, ["propose", "escalate", END])
    graph.add_conditional_edges("assign", route_after_assign, ["search_beds", END])
    graph.add_conditional_edges("decline", route_to_proposal, ["propose", "escalate", END])
    for terminal in ("ask_missing", "propose", "escalate", *READ_ONLY_NODES.values(), "cancel", "fallback"):
        graph.add_edge(terminal, END)

    return graph.compile(checkpointer=InMemorySaver(serde=checkpoint_serializer()))


def checkpoint_serializer() -> JsonPlusSerializer:
    """Allow only this workflow's own types to be restored from saved state."""
    state_types = (Stage, Outcome, Slots, Extraction, Intent, WardType, BedStatus)
    return JsonPlusSerializer(allowed_msgpack_modules=[(cls.__module__, cls.__name__) for cls in state_types])


# --- Conversation -----------------------------------------------------------------------------


# How each node does its work, for anyone reading a trace.
LLM_NODES = {"interpret", "answer_policy"}
TOOL_NODES = {"identify_patient", "search_beds", "assign", "escalate", "patient_status", "list_patients", "list_beds"}


@dataclass(frozen=True)
class Step:
    """One node that ran during a turn, and the event it recorded."""

    node: str
    event: str

    @property
    def kind(self) -> str:
        if self.node in LLM_NODES:
            return "llm"
        return "tool" if self.node in TOOL_NODES else "logic"


@dataclass(frozen=True)
class Turn:
    reply: str
    steps: list[Step]

    @property
    def path(self) -> list[str]:
        return [step.node for step in self.steps]


class Conversation:
    """One user's chat session: feeds each message through the graph and returns the reply."""

    def __init__(self, graph: CompiledStateGraph, thread_id: str = "default") -> None:
        self._graph = graph
        self._config = {"configurable": {"thread_id": thread_id}}

    def send(self, message: str) -> Turn:
        is_first_turn = not self._graph.get_state(self._config).values
        payload = {**(fresh_request() if is_first_turn else {}), "user_message": message}
        steps: list[Step] = []
        for update in self._graph.stream(payload, self._config, stream_mode="updates"):
            steps.extend(Step(node, changes["event"]) for node, changes in update.items())
        return Turn(reply=self.state["reply"], steps=steps)

    @property
    def state(self) -> WorkflowState:
        return self._graph.get_state(self._config).values
