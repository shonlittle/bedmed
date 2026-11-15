"""Workflow tests. The LLM is replaced by a scripted extractor; the API and database are real.

Each test states what the LLM "understood" and checks what the workflow then decided and did.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant.extractor import ExtractionError
from assistant.models import Extraction, Intent, Slots
from assistant.tools import HospitalApi
from assistant.workflow import Conversation, Stage, Turn, build_graph

MEDICAL = {
    "ward_type": "MEDICAL",
    "isolation_required": False,
    "monitoring_required": False,
    "requires_private_room": False,
}
TELEMETRY = {**MEDICAL, "ward_type": "TELEMETRY", "monitoring_required": True}
ICU = {**MEDICAL, "ward_type": "ICU", "monitoring_required": True}


class ScriptedExtractor:
    """Stands in for the LLM: returns whatever the test says the message meant."""

    def __init__(self) -> None:
        self.result: Extraction | Exception | None = None
        self.contexts = []

    def extract(self, message, context):
        self.contexts.append(context)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Chat:
    def __init__(self, api: HospitalApi) -> None:
        self.extractor = ScriptedExtractor()
        self.conversation = Conversation(build_graph(self.extractor, api))

    def send(self, intent: Intent, **slots) -> Turn:
        self.extractor.result = Extraction(intent=intent, **slots)
        return self.conversation.send("(scripted)")

    def send_with_llm_down(self) -> Turn:
        self.extractor.result = ExtractionError("LLM unavailable")
        return self.conversation.send("(scripted)")

    @property
    def state(self):
        return self.conversation.state


@pytest.fixture
def chat(client: TestClient) -> Chat:
    return Chat(HospitalApi(client))


def unreachable_api() -> HospitalApi:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return HospitalApi(httpx.Client(base_url="http://api.invalid", transport=httpx.MockTransport(refuse)))


def current_bed(client: TestClient, patient_id: str) -> str | None:
    return client.get(f"/patients/{patient_id}").json()["current_bed_id"]


def test_happy_path_proposes_then_assigns_after_confirmation(chat: Chat, client: TestClient):
    turn = chat.send(Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)

    assert turn.path == ["interpret", "collect", "identify_patient", "search_beds", "propose"]
    assert "Bed 4M-102B is eligible for Maria Alvarez (P-1001)" in turn.reply
    assert "Free but not eligible: 4M-103B (not allowed by the room-sharing policy)" in turn.reply
    assert chat.state["stage"] is Stage.AWAITING_CONFIRMATION
    assert current_bed(client, "P-1001") is None, "nothing may be written before the user confirms"

    turn = chat.send(Intent.CONFIRM)

    assert turn.path == ["interpret", "assign"]
    assert "Confirmed by the hospital system" in turn.reply
    assert "4M-102B" in turn.reply
    assert current_bed(client, "P-1001") == "4M-102B"
    assert chat.state["stage"] is Stage.IDLE


def test_missing_details_are_asked_for_before_any_search(chat: Chat):
    turn = chat.send(Intent.REQUEST_BED, patient_id="P-1001", ward_type="MEDICAL")

    assert turn.path == ["interpret", "collect", "identify_patient", "ask_missing"]
    assert "Do they need isolation?" in turn.reply
    assert "Do they need cardiac monitoring?" in turn.reply
    assert "Do they need a private room?" in turn.reply
    assert "ward" not in turn.reply.lower()

    turn = chat.send(Intent.PROVIDE_INFO, isolation_required=False, monitoring_required=False)
    assert turn.reply == "Do they need a private room?"

    turn = chat.send(Intent.PROVIDE_INFO, requires_private_room=False)
    assert turn.path[-1] == "propose"


def test_extractor_is_told_which_details_are_pending(chat: Chat):
    chat.send(Intent.REQUEST_BED, patient_id="P-1001", ward_type="MEDICAL", isolation_required=False)
    chat.send(Intent.UNKNOWN)

    first, second = chat.extractor.contexts
    assert first.pending_slots == []
    assert second.pending_slots == ["monitoring_required", "requires_private_room"]


def test_request_without_details_asks_for_everything(chat: Chat):
    turn = chat.send(Intent.REQUEST_BED)
    assert turn.path == ["interpret", "collect", "identify_patient", "ask_missing"]
    assert "Which patient is this for?" in turn.reply


def test_unknown_patient_is_reported_and_asked_for_again(chat: Chat):
    turn = chat.send(Intent.REQUEST_BED, patient_id="P-9999", **MEDICAL)

    assert turn.path[-1] == "ask_missing"
    assert "I couldn't find a patient with ID P-9999." in turn.reply
    assert "Which patient is this for?" in turn.reply

    turn = chat.send(Intent.PROVIDE_INFO, patient_id="1001")
    assert "Maria Alvarez (P-1001)" in turn.reply


def test_patient_who_already_has_a_bed_is_not_reassigned(chat: Chat):
    turn = chat.send(Intent.REQUEST_BED, patient_id="P-2001", **MEDICAL)
    assert turn.path == ["interpret", "collect", "identify_patient"]
    assert "already in bed 4M-102A" in turn.reply
    assert chat.state["stage"] is Stage.IDLE


def test_no_eligible_bed_escalates_without_assigning(chat: Chat, client: TestClient):
    turn = chat.send(Intent.REQUEST_BED, patient_id="P-1003", **ICU)

    assert turn.path == ["interpret", "collect", "identify_patient", "search_beds", "escalate"]
    assert "No bed has been assigned for Priya Nair (P-1003)" in turn.reply
    assert "escalation #" in turn.reply
    assert "occupied x2, being cleaned x1" in turn.reply
    assert current_bed(client, "P-1003") is None
    assert chat.state["stage"] is Stage.IDLE


def test_conflict_at_assignment_refreshes_and_proposes_another_bed(chat: Chat, client: TestClient):
    turn = chat.send(Intent.REQUEST_BED, patient_id="P-1002", **TELEMETRY)
    assert "Shall I assign 5T-203A?" in turn.reply

    # Another process takes the bed while the nurse is deciding.
    client.patch("/beds/5T-203A/status", json={"status": "RESERVED"})

    turn = chat.send(Intent.CONFIRM)

    assert turn.path == ["interpret", "assign", "search_beds", "propose"]
    assert "Bed 5T-203A could not be assigned: it is no longer eligible (reserved)." in turn.reply
    assert "No assignment was made." in turn.reply
    assert "Shall I assign 5T-201?" in turn.reply
    assert current_bed(client, "P-1002") is None

    turn = chat.send(Intent.CONFIRM)
    assert "Confirmed by the hospital system" in turn.reply
    assert current_bed(client, "P-1002") == "5T-201"


def test_conflict_with_no_bed_left_escalates(chat: Chat, client: TestClient):
    chat.send(Intent.REQUEST_BED, patient_id="P-1002", **TELEMETRY)
    client.patch("/beds/5T-203A/status", json={"status": "RESERVED"})
    client.patch("/beds/5T-201/status", json={"status": "CLEANING"})

    turn = chat.send(Intent.CONFIRM)

    assert turn.path == ["interpret", "assign", "search_beds", "escalate"]
    assert "Bed 5T-203A could not be assigned" in turn.reply
    assert "escalated this to bed management" in turn.reply
    assert current_bed(client, "P-1002") is None


def test_declining_offers_the_next_bed_then_escalates(chat: Chat, client: TestClient):
    chat.send(Intent.REQUEST_BED, patient_id="P-1002", **TELEMETRY)

    turn = chat.send(Intent.DECLINE)
    assert turn.path == ["interpret", "decline", "propose"]
    assert "Okay, not 5T-203A." in turn.reply
    assert "Shall I assign 5T-201?" in turn.reply

    turn = chat.send(Intent.DECLINE)
    assert turn.path == ["interpret", "decline", "escalate"]
    assert "Requester declined every eligible TELEMETRY bed" in turn.reply
    assert current_bed(client, "P-1002") is None


def test_changed_requirement_discards_the_proposal_and_searches_again(chat: Chat, client: TestClient):
    turn = chat.send(Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)
    assert "Shall I assign 4M-102B?" in turn.reply

    turn = chat.send(Intent.PROVIDE_INFO, isolation_required=True)

    assert turn.path == ["interpret", "collect", "identify_patient", "search_beds", "propose"]
    assert "Shall I assign 4M-101?" in turn.reply
    assert current_bed(client, "P-1001") is None


def test_confirmation_that_also_changes_a_detail_does_not_assign(chat: Chat, client: TestClient):
    chat.send(Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)
    turn = chat.send(Intent.CONFIRM, requires_private_room=True)
    assert "assign" not in turn.path
    assert current_bed(client, "P-1001") is None


def test_confirm_with_nothing_proposed_does_nothing(chat: Chat):
    turn = chat.send(Intent.CONFIRM)
    assert turn.path == ["interpret", "fallback"]
    assert "I didn't understand" in turn.reply


def test_unclear_message_repeats_the_pending_confirmation(chat: Chat, client: TestClient):
    chat.send(Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)
    turn = chat.send(Intent.UNKNOWN)
    assert turn.reply == "Sorry, I didn't understand that. Shall I assign bed 4M-102B? (yes / no)"
    assert chat.state["stage"] is Stage.AWAITING_CONFIRMATION
    assert current_bed(client, "P-1001") is None


def test_cancel_clears_the_request(chat: Chat, client: TestClient):
    chat.send(Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)
    turn = chat.send(Intent.CANCEL)
    assert turn.reply == "Cancelled. No bed has been assigned."
    assert chat.state["stage"] is Stage.IDLE
    assert chat.state["slots"] == Slots()

    assert chat.send(Intent.CONFIRM).path == ["interpret", "fallback"]
    assert current_bed(client, "P-1001") is None


def test_patient_status_lookup_leaves_request_in_progress(chat: Chat):
    chat.send(Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)

    turn = chat.send(Intent.PATIENT_STATUS, patient_id="P-2001")
    assert turn.reply == "Helen Brooks (P-2001) is in bed 4M-102A."
    assert chat.state["stage"] is Stage.AWAITING_CONFIRMATION
    assert chat.state["slots"].patient_id == "P-1001"


def test_patient_status_for_unknown_patient(chat: Chat):
    turn = chat.send(Intent.PATIENT_STATUS, patient_id="P-9999")
    assert turn.reply == "I couldn't find a patient with ID P-9999."


def test_llm_failure_changes_nothing(chat: Chat, client: TestClient):
    chat.send(Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)

    turn = chat.send_with_llm_down()

    assert turn.path == ["interpret"]
    assert "language service is unavailable" in turn.reply
    assert chat.state["stage"] is Stage.AWAITING_CONFIRMATION
    assert chat.state["proposed_bed_id"] == "4M-102B"
    assert current_bed(client, "P-1001") is None


def test_backend_down_during_search_is_reported_and_retryable(client: TestClient):
    chat = Chat(unreachable_api())
    turn = chat.send(Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)
    assert turn.path == ["interpret", "collect", "identify_patient"]
    assert "can't reach the hospital system" in turn.reply
    assert "nothing has been assigned" in turn.reply


def test_backend_failure_during_assignment_never_claims_success(client: TestClient):
    def fail_assignments(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/assignments":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(500)

    api_calls = HospitalApi(client)
    broken = HospitalApi(httpx.Client(base_url="http://api.invalid", transport=httpx.MockTransport(fail_assignments)))
    api_calls.assign_bed = broken.assign_bed
    chat = Chat(api_calls)

    chat.send(Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)
    turn = chat.send(Intent.CONFIRM)

    assert turn.path == ["interpret", "assign"]
    assert "could not confirm the assignment" in turn.reply
    assert "Confirmed" not in turn.reply
    assert chat.state["stage"] is Stage.AWAITING_CONFIRMATION
    assert current_bed(client, "P-1001") is None


def test_patient_can_be_identified_by_a_unique_name(chat: Chat):
    turn = chat.send(Intent.REQUEST_BED, patient_name="Maria Alvarez", **MEDICAL)

    assert turn.path == ["interpret", "collect", "identify_patient", "search_beds", "propose"]
    assert "Bed 4M-102B is eligible for Maria Alvarez (P-1001)" in turn.reply
    assert chat.state["slots"].patient_id == "P-1001"


def test_ambiguous_name_lists_the_matches_and_asks_instead_of_choosing(chat: Chat, client: TestClient):
    turn = chat.send(Intent.REQUEST_BED, patient_name="Alvarez", **MEDICAL)

    assert turn.path == ["interpret", "collect", "identify_patient", "ask_missing"]
    assert "More than one patient matches 'Alvarez'. You can answer with a number:" in turn.reply
    assert "1. Maria Alvarez (P-1001)\n2. Elena Alvarez (P-1005)" in turn.reply
    assert "Which patient is this for?" in turn.reply
    assert chat.state["slots"].patient_id is None

    turn = chat.send(Intent.PROVIDE_INFO, patient_id="P-1005")
    assert "eligible for Elena Alvarez (P-1005)" in turn.reply
    assert current_bed(client, "P-1005") is None


def test_unknown_name_is_reported_and_asked_for_again(chat: Chat):
    turn = chat.send(Intent.REQUEST_BED, patient_name="Nobody Here", **MEDICAL)
    assert turn.path[-1] == "ask_missing"
    assert "I couldn't find a patient named 'Nobody Here'." in turn.reply


def test_single_letter_name_is_not_searched(chat: Chat):
    turn = chat.send(Intent.REQUEST_BED, patient_name="a", **MEDICAL)
    assert "I couldn't find a patient named 'a'." in turn.reply


def test_id_wins_when_a_message_gives_both_id_and_name(chat: Chat):
    turn = chat.send(Intent.REQUEST_BED, patient_id="P-1002", patient_name="Alvarez", **MEDICAL)
    assert "eligible for James Okafor (P-1002)" in turn.reply


def test_naming_a_different_patient_replaces_the_one_in_progress(chat: Chat):
    chat.send(Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)
    turn = chat.send(Intent.PROVIDE_INFO, patient_name="James Okafor")
    assert turn.path[-1] == "propose"
    assert "eligible for James Okafor (P-1002)" in turn.reply
    assert chat.state["slots"].patient_id == "P-1002"


def test_named_patient_who_already_has_a_bed_is_not_reassigned(chat: Chat):
    turn = chat.send(Intent.REQUEST_BED, patient_name="Helen Brooks", **MEDICAL)
    assert "Helen Brooks (P-2001) is already in bed 4M-102A" in turn.reply


def test_patient_status_by_name_reports_every_match(chat: Chat):
    turn = chat.send(Intent.PATIENT_STATUS, patient_name="Helen Brooks")
    assert turn.reply == "Helen Brooks (P-2001) is in bed 4M-102A."

    turn = chat.send(Intent.PATIENT_STATUS, patient_name="Alvarez")
    assert turn.reply == (
        "Maria Alvarez (P-1001) does not have a bed assigned.\nElena Alvarez (P-1005) does not have a bed assigned."
    )

    turn = chat.send(Intent.PATIENT_STATUS, patient_name="Nobody Here")
    assert turn.reply == "I couldn't find a patient named 'Nobody Here'."


def test_patient_status_without_a_patient_asks_who(chat: Chat):
    turn = chat.send(Intent.PATIENT_STATUS)
    assert "Which patient?" in turn.reply


def test_list_patients_with_and_without_beds(chat: Chat):
    turn = chat.send(Intent.LIST_PATIENTS)
    assert turn.path == ["interpret", "list_patients"]
    assert turn.reply.startswith("Patients (9):")

    turn = chat.send(Intent.LIST_PATIENTS, has_bed=True)
    assert turn.reply.startswith("Patients with a bed (4):")
    assert "- Helen Brooks (P-2001): bed 4M-102A" in turn.reply

    turn = chat.send(Intent.LIST_PATIENTS, has_bed=False)
    assert turn.reply.startswith("Patients waiting for a bed (5):")
    assert "- Maria Alvarez (P-1001): no bed" in turn.reply
    assert "Helen Brooks" not in turn.reply


def test_list_beds_by_status(chat: Chat):
    turn = chat.send(Intent.LIST_BEDS)
    assert turn.path == ["interpret", "list_beds"]
    assert turn.reply.startswith("Beds (16):")
    assert "Available means free" not in turn.reply

    turn = chat.send(Intent.LIST_BEDS, bed_status="OCCUPIED")
    assert turn.reply.startswith("Beds occupied (4):")
    assert "- 4M-102A (4 Medical, shared): occupied by Helen Brooks (P-2001)" in turn.reply

    turn = chat.send(Intent.LIST_BEDS, bed_status="RESERVED")
    assert turn.reply == "Beds reserved (1):\n- 5T-202 (5 Telemetry, private, monitoring): reserved"


def test_available_bed_list_warns_that_available_is_not_eligible(chat: Chat):
    turn = chat.send(Intent.LIST_BEDS, bed_status="AVAILABLE")
    assert turn.reply.startswith("Beds available (8):")
    assert "- 5T-201 (5 Telemetry, private, isolation, monitoring): available" in turn.reply
    assert turn.reply.endswith("so ask me to find a bed for them.")


def test_empty_list_says_none(chat: Chat, client: TestClient):
    client.patch("/beds/5T-202/status", json={"status": "AVAILABLE"})
    assert chat.send(Intent.LIST_BEDS, bed_status="RESERVED").reply == "Beds reserved: none."


def test_lists_do_not_disturb_a_pending_proposal(chat: Chat, client: TestClient):
    chat.send(Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)

    chat.send(Intent.LIST_BEDS, bed_status="AVAILABLE")
    chat.send(Intent.LIST_PATIENTS, has_bed=False)
    assert chat.state["stage"] is Stage.AWAITING_CONFIRMATION
    assert chat.state["proposed_bed_id"] == "4M-102B"

    assert "Confirmed by the hospital system" in chat.send(Intent.CONFIRM).reply
    assert current_bed(client, "P-1001") == "4M-102B"


def test_backend_down_during_a_list_is_reported(client: TestClient):
    chat = Chat(unreachable_api())
    assert "can't reach the hospital system" in chat.send(Intent.LIST_BEDS).reply
    assert "can't reach the hospital system" in chat.send(Intent.LIST_PATIENTS).reply


def test_patient_can_be_picked_by_position_from_the_list_shown(chat: Chat):
    chat.send(Intent.REQUEST_BED, patient_name="Alvarez", **MEDICAL)
    assert [patient["id"] for patient in chat.state["patient_options"]] == ["P-1001", "P-1005"]

    turn = chat.send(Intent.PROVIDE_INFO, choice_number=2)

    assert turn.path == ["interpret", "collect", "identify_patient", "search_beds", "propose"]
    assert "eligible for Elena Alvarez (P-1005)" in turn.reply
    assert chat.state["patient_options"] == []


def test_extractor_is_told_a_numbered_list_was_shown(chat: Chat):
    chat.send(Intent.REQUEST_BED, patient_name="Alvarez", **MEDICAL)
    chat.send(Intent.UNKNOWN)

    first, second = chat.extractor.contexts
    assert first.option_count == 0
    assert second.option_count == 2
    assert second.pending_slots == ["patient_id"]


@pytest.mark.parametrize("choice_number", [0, 3, -1])
def test_position_outside_the_list_is_rejected_and_the_list_is_shown_again(chat: Chat, choice_number: int):
    chat.send(Intent.REQUEST_BED, patient_name="Alvarez", **MEDICAL)

    turn = chat.send(Intent.PROVIDE_INFO, choice_number=choice_number)

    assert turn.path[-1] == "ask_missing"
    assert "Please choose a number from 1 to 2:\n1. Maria Alvarez (P-1001)\n2. Elena Alvarez (P-1005)" in turn.reply
    assert chat.state["slots"].patient_id is None
    assert len(chat.state["patient_options"]) == 2


def test_position_is_ignored_when_no_list_was_shown(chat: Chat):
    turn = chat.send(Intent.PROVIDE_INFO, choice_number=1)
    assert turn.path == ["interpret", "fallback"]


def test_list_of_options_survives_an_unrelated_answer(chat: Chat):
    chat.send(Intent.REQUEST_BED, patient_name="Alvarez", ward_type="MEDICAL")
    chat.send(Intent.PROVIDE_INFO, isolation_required=False, monitoring_required=False, requires_private_room=False)

    turn = chat.send(Intent.PROVIDE_INFO, choice_number=1)
    assert "eligible for Maria Alvarez (P-1001)" in turn.reply


def test_explicit_id_wins_over_a_list_position(chat: Chat):
    chat.send(Intent.REQUEST_BED, patient_name="Alvarez", **MEDICAL)
    turn = chat.send(Intent.PROVIDE_INFO, patient_id="P-1002", choice_number=1)
    assert "eligible for James Okafor (P-1002)" in turn.reply
    assert chat.state["patient_options"] == []
