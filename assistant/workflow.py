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
from assistant.models import BedStatus, Extraction, Intent, Slots, WardType
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
    # The request in progress
    stage: Stage
    slots: Slots
    patient: dict | None
    candidates: list[dict]
    near_misses: list[dict]
    rejection_summary: dict[str, int]
    proposed_bed_id: str | None
    declined_bed_ids: list[str]


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
    }


def next_candidate(state: WorkflowState) -> dict | None:
    """The best eligible bed the user has not declined."""
    return next((bed for bed in state["candidates"] if bed["id"] not in state["declined_bed_ids"]), None)


# Lookups that answer a question without touching the request in progress.
READ_ONLY_NODES = {
    Intent.PATIENT_STATUS: "patient_status",
    Intent.LIST_PATIENTS: "list_patients",
    Intent.LIST_BEDS: "list_beds",
}


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
    if changes_slots(state) or extraction.patient_name:
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


class Nodes:
    def __init__(self, extractor: Extractor, api: HospitalApi) -> None:
        self._extractor = extractor
        self._api = api

    def interpret(self, state: WorkflowState) -> dict:
        """The only LLM step: turn the message into an intent and entities."""
        collecting = state["stage"] is Stage.COLLECTING
        context = ExtractionContext(
            stage=state["stage"],
            pending_slots=state["slots"].missing() if collecting else [],
            proposed_bed_id=state["proposed_bed_id"],
        )
        try:
            extraction = self._extractor.extract(state["user_message"], context)
        except ExtractionError as error:
            logger.warning("llm_failure error=%s", error)
            return {"extraction": None, "notice": "", "reply": messages.LLM_FAILURE}
        logger.info("intent_detected intent=%s", extraction.intent)
        return {"extraction": extraction, "notice": ""}

    def collect(self, state: WorkflowState) -> dict:
        """Merge stated details into the slots. Any earlier search or proposal is discarded."""
        slots = state["slots"].merged_with(state["extraction"].slots())
        same_patient = slots.patient_id == state["slots"].patient_id
        return {
            **fresh_request(),
            "stage": Stage.COLLECTING,
            "slots": slots,
            "patient": state["patient"] if same_patient else None,
        }

    def identify_patient(self, state: WorkflowState) -> dict:
        """Establish exactly which patient this is. A name is only a way to find an ID:
        one match is used, none or several are reported and the user is asked."""
        slots, patient, notice = state["slots"], state["patient"], ""
        extraction = state["extraction"]
        try:
            if extraction.patient_name and not extraction.patient_id:
                matches = self._find_by_name(extraction.patient_name)
                logger.info("patient_name_lookup matches=%d", len(matches))
                patient = matches[0] if len(matches) == 1 else None
                slots = slots.model_copy(update={"patient_id": patient["id"] if patient else None})
                notice = "" if patient else messages.name_matches(extraction.patient_name, matches)
            if slots.patient_id and patient is None:
                patient = self._api.get_patient(slots.patient_id)
        except NotFoundError:
            logger.info("patient_not_found patient=%s", slots.patient_id)
            notice = messages.patient_not_found(slots.patient_id)
            slots = slots.model_copy(update={"patient_id": None})
        except ApiError as error:
            logger.error("tool_failure tool=identify_patient code=%s", error.code)
            return {"reply": messages.BACKEND_UNAVAILABLE, "outcome": Outcome.STOP}

        if patient and patient["current_bed_id"]:
            return {**fresh_request(), "reply": messages.already_placed(patient), "outcome": Outcome.STOP}
        return {"slots": slots, "patient": patient, "notice": notice, "outcome": Outcome.CONTINUE}

    def _find_by_name(self, name: str) -> list[dict]:
        cleaned = name.strip()
        return self._api.find_patients(cleaned) if len(cleaned) >= MIN_NAME_LENGTH else []

    def ask_missing(self, state: WorkflowState) -> dict:
        missing = state["slots"].missing()
        logger.info("slots_missing missing=%s", missing)
        return {"reply": messages.join(state["notice"], messages.ask_for_missing(missing))}

    def search_beds(self, state: WorkflowState) -> dict:
        slots = state["slots"]
        try:
            result = self._api.search_beds(slots.patient_id, slots.requirements())
        except ApiError as error:
            logger.error("tool_failure tool=search_beds code=%s", error.code)
            return {"reply": messages.join(state["notice"], messages.BACKEND_UNAVAILABLE), "outcome": Outcome.STOP}
        logger.info("bed_search patient=%s eligible=%d", slots.patient_id, len(result["eligible"]))
        return {
            "candidates": result["eligible"],
            "near_misses": result["rejected"],
            "rejection_summary": result["rejection_summary"],
            "outcome": Outcome.CONTINUE,
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
        }

    def assign(self, state: WorkflowState) -> dict:
        """Attempt the assignment. Only a success response from the API counts as assigned."""
        slots, bed_id = state["slots"], state["proposed_bed_id"]
        logger.info("assignment_attempted patient=%s bed=%s", slots.patient_id, bed_id)
        try:
            assignment = self._api.assign_bed(slots.patient_id, bed_id, slots.requirements())
        except ConflictError as error:
            logger.info("assignment_conflicted patient=%s bed=%s code=%s", slots.patient_id, bed_id, error.code)
            if error.code == "PATIENT_ALREADY_ASSIGNED":
                return {**fresh_request(), "reply": messages.assignment_refused(str(error)), "outcome": Outcome.STOP}
            return {
                "stage": Stage.COLLECTING,
                "proposed_bed_id": None,
                "notice": messages.conflict(bed_id, error.reasons),
                "outcome": Outcome.CONFLICT,
            }
        except ApiError as error:
            logger.error("tool_failure tool=assign_bed code=%s", error.code)
            return {"reply": messages.assignment_unconfirmed(bed_id), "outcome": Outcome.STOP}
        logger.info("assignment_succeeded patient=%s bed=%s", slots.patient_id, bed_id)
        return {**fresh_request(), "reply": messages.assigned(state["patient"], assignment), "outcome": Outcome.STOP}

    def decline(self, state: WorkflowState) -> dict:
        bed_id = state["proposed_bed_id"]
        return {
            "stage": Stage.COLLECTING,
            "proposed_bed_id": None,
            "declined_bed_ids": [*state["declined_bed_ids"], bed_id],
            "notice": messages.declined(bed_id),
            "outcome": Outcome.CONTINUE,
        }

    def escalate(self, state: WorkflowState) -> dict:
        """No eligible bed: hand over to a human. Requirements are never relaxed to force a match."""
        slots = state["slots"]
        reason = messages.escalation_reason(slots, state["rejection_summary"], state["declined_bed_ids"])
        try:
            escalation = self._api.create_escalation(slots.patient_id, slots.requirements(), reason)
        except ApiError as error:
            logger.error("tool_failure tool=create_escalation code=%s", error.code)
            return {"reply": messages.join(state["notice"], messages.escalation_failed(reason))}
        logger.info("escalation_created patient=%s escalation=%s", slots.patient_id, escalation["id"])
        reply = messages.join(state["notice"], messages.escalated(state["patient"], escalation, reason))
        return {**fresh_request(), "reply": reply}

    def patient_status(self, state: WorkflowState) -> dict:
        """Read-only lookup by ID or name. It does not disturb a request in progress."""
        extraction = state["extraction"]
        patient_id = extraction.patient_id or (None if extraction.patient_name else state["slots"].patient_id)
        try:
            if patient_id:
                return {"reply": messages.patient_status(self._api.get_patient(patient_id))}
            if extraction.patient_name:
                matches = self._find_by_name(extraction.patient_name)
                if not matches:
                    return {"reply": messages.name_matches(extraction.patient_name, matches)}
                return {"reply": messages.patient_statuses(matches)}
        except NotFoundError:
            return {"reply": messages.patient_not_found(patient_id)}
        except ApiError as error:
            logger.error("tool_failure tool=patient_status code=%s", error.code)
            return {"reply": messages.BACKEND_UNAVAILABLE}
        return {"reply": messages.STATUS_NEEDS_PATIENT}

    def list_patients(self, state: WorkflowState) -> dict:
        has_bed = state["extraction"].has_bed
        try:
            patients = self._api.list_patients(has_bed)
        except ApiError as error:
            logger.error("tool_failure tool=list_patients code=%s", error.code)
            return {"reply": messages.BACKEND_UNAVAILABLE}
        return {"reply": messages.patient_list(patients, has_bed)}

    def list_beds(self, state: WorkflowState) -> dict:
        """Lists operational status only. Eligibility for a patient is a different question."""
        status = state["extraction"].bed_status
        try:
            beds = self._api.list_beds(status)
        except ApiError as error:
            logger.error("tool_failure tool=list_beds code=%s", error.code)
            return {"reply": messages.BACKEND_UNAVAILABLE}
        return {"reply": messages.bed_list(beds, status)}

    def cancel(self, state: WorkflowState) -> dict:
        return {**fresh_request(), "reply": messages.CANCELLED}

    def fallback(self, state: WorkflowState) -> dict:
        collecting = state["stage"] is Stage.COLLECTING
        return {"reply": messages.fallback(state["proposed_bed_id"], state["slots"].missing(), collecting)}


# --- Graph ------------------------------------------------------------------------------------


def build_graph(extractor: Extractor, api: HospitalApi) -> CompiledStateGraph:
    nodes = Nodes(extractor, api)
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


@dataclass(frozen=True)
class Turn:
    reply: str
    path: list[str]


class Conversation:
    """One user's chat session: feeds each message through the graph and returns the reply."""

    def __init__(self, graph: CompiledStateGraph, thread_id: str = "default") -> None:
        self._graph = graph
        self._config = {"configurable": {"thread_id": thread_id}}

    def send(self, message: str) -> Turn:
        is_first_turn = not self._graph.get_state(self._config).values
        payload = {**(fresh_request() if is_first_turn else {}), "user_message": message}
        path: list[str] = []
        for update in self._graph.stream(payload, self._config, stream_mode="updates"):
            path.extend(update)
        return Turn(reply=self.state["reply"], path=path)

    @property
    def state(self) -> WorkflowState:
        return self._graph.get_state(self._config).values
