"""Everything the assistant says. Replies are templates filled from API data, never LLM text,
so the assistant cannot claim an action the backend did not confirm."""

from assistant.models import Slots

SLOT_QUESTIONS = {
    "patient_id": "Which patient is this for? Please give a patient ID (for example P-1001) or a name.",
    "ward_type": "What type of ward do they need: medical, telemetry, ICU or surgical?",
    "isolation_required": "Do they need isolation?",
    "monitoring_required": "Do they need cardiac monitoring?",
    "requires_private_room": "Do they need a private room?",
}

REASON_TEXT = {
    "WARD_TYPE_MISMATCH": "wrong ward type",
    "WARD_UNAVAILABLE": "ward closed",
    "BED_OCCUPIED": "occupied",
    "BED_CLEANING": "being cleaned",
    "BED_RESERVED": "reserved",
    "BED_OUT_OF_SERVICE": "out of service",
    "NOT_ISOLATION_CAPABLE": "not isolation-capable",
    "NOT_MONITORING_CAPABLE": "no cardiac monitoring",
    "PRIVATE_ROOM_REQUIRED": "not a private room",
    "ROOM_SHARING_INCOMPATIBLE": "not allowed by the room-sharing policy",
    "ROOM_SHARING_UNDETERMINED": "room sharing needs human review",
}

LLM_FAILURE = (
    "I couldn't interpret that because the language service is unavailable. Nothing has changed. Please try again."
)
BACKEND_UNAVAILABLE = (
    "I can't reach the hospital system right now, so nothing has been assigned. Say 'try again' to retry."
)
CANCELLED = "Cancelled. No bed has been assigned."
STATUS_NEEDS_PATIENT = "Which patient? Please give a patient ID (for example P-1001) or a name."
HELP = (
    "I can find and assign a bed for a patient, tell you where a patient is, or list patients and beds. "
    "For example: 'I need a telemetry bed for P-1002'."
)


def join(*parts: str) -> str:
    return "\n".join(part for part in parts if part)


def describe_reasons(reasons: list[str]) -> str:
    return ", ".join(REASON_TEXT.get(reason, reason) for reason in reasons)


def describe_patient(patient: dict) -> str:
    return f"{patient['full_name']} ({patient['id']})"


def patient_not_found(patient_id: str) -> str:
    return f"I couldn't find a patient with ID {patient_id}."


def already_placed(patient: dict) -> str:
    return f"{describe_patient(patient)} is already in bed {patient['current_bed_id']}. No new bed has been assigned."


def ask_for_missing(missing: list[str]) -> str:
    if len(missing) == 1:
        return SLOT_QUESTIONS[missing[0]]
    questions = "\n".join(f"- {SLOT_QUESTIONS[name]}" for name in missing)
    return f"I need a few more details:\n{questions}"


def proposal(patient: dict, bed: dict, other_eligible: int, near_misses: list[dict]) -> str:
    room = "private room" if bed["room_type"] == "PRIVATE" else f"shared room {bed['room_id']}"
    lines = [f"Bed {bed['id']} is eligible for {describe_patient(patient)}: {bed['ward_name']}, {room}."]
    if other_eligible:
        lines.append(f"Other eligible beds: {other_eligible}.")
    free_but_ineligible = [
        f"{item['bed']['id']} ({describe_reasons(item['reasons'])})"
        for item in near_misses
        if item["bed"]["status"] == "AVAILABLE"
    ]
    if free_but_ineligible:
        lines.append(f"Free but not eligible: {'; '.join(free_but_ineligible)}.")
    lines.append(f"Shall I assign {bed['id']}? (yes / no)")
    return "\n".join(lines)


def conflict(bed_id: str, reasons: list[str]) -> str:
    why = f" ({describe_reasons(reasons)})" if reasons else ""
    return f"Bed {bed_id} could not be assigned: it is no longer eligible{why}. No assignment was made."


def declined(bed_id: str) -> str:
    return f"Okay, not {bed_id}."


def assigned(patient: dict, assignment: dict) -> str:
    return (
        f"Confirmed by the hospital system: {describe_patient(patient)} is assigned to bed "
        f"{assignment['bed_id']} (assignment #{assignment['id']})."
    )


def assignment_refused(message: str) -> str:
    return f"The hospital system refused the assignment: {message} No new bed has been assigned."


def assignment_unconfirmed(bed_id: str) -> str:
    return (
        f"I could not confirm the assignment of bed {bed_id} because the hospital system did not respond. "
        "Treat it as not assigned. Say 'yes' to try again."
    )


def describe_needs(slots: Slots) -> str:
    needs = [
        label
        for label, required in [
            ("isolation", slots.isolation_required),
            ("cardiac monitoring", slots.monitoring_required),
            ("private room", slots.requires_private_room),
        ]
        if required
    ]
    return ", ".join(needs) if needs else "no special needs"


def escalation_reason(slots: Slots, rejection_summary: dict[str, int], declined_bed_ids: list[str]) -> str:
    request = f"{slots.ward_type} bed ({describe_needs(slots)})"
    if declined_bed_ids:
        return f"Requester declined every eligible {request}: {', '.join(declined_bed_ids)}."
    if not rejection_summary:
        return f"No eligible {request}: the hospital has no beds in that type of ward."
    counts = ", ".join(f"{REASON_TEXT.get(reason, reason)} x{count}" for reason, count in rejection_summary.items())
    return f"No eligible {request}. Why candidate beds were rejected: {counts}."


def escalated(patient: dict, escalation: dict, reason: str) -> str:
    return (
        f"No bed has been assigned for {describe_patient(patient)}. "
        f"I have escalated this to bed management (escalation #{escalation['id']}).\n"
        f"Reason: {reason}"
    )


def escalation_failed(reason: str) -> str:
    return (
        "No bed has been assigned, and I could not record the escalation because the hospital system "
        f"did not respond. Please contact bed management directly, or say 'try again'.\nReason: {reason}"
    )


def patient_status(patient: dict) -> str:
    if patient["current_bed_id"]:
        return f"{describe_patient(patient)} is in bed {patient['current_bed_id']}."
    return f"{describe_patient(patient)} does not have a bed assigned."


def fallback(proposed_bed_id: str | None, missing: list[str], collecting: bool) -> str:
    if proposed_bed_id:
        return f"Sorry, I didn't understand that. Shall I assign bed {proposed_bed_id}? (yes / no)"
    if collecting and missing:
        return join("Sorry, I didn't understand that.", ask_for_missing(missing))
    return join("Sorry, I didn't understand that.", HELP)


MAX_NAME_MATCHES_SHOWN = 5
AVAILABLE_IS_NOT_ELIGIBLE = (
    "Available means free. Whether a bed suits a particular patient depends on their needs, "
    "so ask me to find a bed for them."
)
BED_STATUS_TEXT = {
    "AVAILABLE": "available",
    "OCCUPIED": "occupied",
    "CLEANING": "being cleaned",
    "RESERVED": "reserved",
    "OUT_OF_SERVICE": "out of service",
}


def name_matches(name: str, matches: list[dict]) -> str:
    """Zero or several patients matched a name. The assistant never picks one itself."""
    if not matches:
        return f"I couldn't find a patient named '{name}'."
    shown = ", ".join(describe_patient(patient) for patient in matches[:MAX_NAME_MATCHES_SHOWN])
    more = len(matches) - MAX_NAME_MATCHES_SHOWN
    suffix = f", and {more} more" if more > 0 else ""
    return f"More than one patient matches '{name}': {shown}{suffix}."


def patient_statuses(patients: list[dict]) -> str:
    return "\n".join(patient_status(patient) for patient in patients[:MAX_NAME_MATCHES_SHOWN])


def patient_list(patients: list[dict], has_bed: bool | None) -> str:
    title = {None: "Patients", True: "Patients with a bed", False: "Patients waiting for a bed"}[has_bed]
    if not patients:
        return f"{title}: none."
    lines = [f"- {describe_patient(patient)}: {describe_placement(patient)}" for patient in patients]
    return "\n".join([f"{title} ({len(patients)}):", *lines])


def describe_placement(patient: dict) -> str:
    return f"bed {patient['current_bed_id']}" if patient["current_bed_id"] else "no bed"


def describe_bed(bed: dict) -> str:
    room = "private" if bed["room_type"] == "PRIVATE" else "shared"
    features = [
        label
        for label, present in [("isolation", bed["isolation_capable"]), ("monitoring", bed["monitoring_capable"])]
        if present
    ]
    status = BED_STATUS_TEXT.get(bed["status"], bed["status"])
    if bed["patient_id"]:
        status = f"{status} by {bed['patient_name']} ({bed['patient_id']})"
    return f"- {bed['id']} ({', '.join([bed['ward_name'], room, *features])}): {status}"


def bed_list(beds: list[dict], status: str | None) -> str:
    title = f"Beds {BED_STATUS_TEXT.get(status, status)}" if status else "Beds"
    if not beds:
        return f"{title}: none."
    lines = [f"{title} ({len(beds)}):", *(describe_bed(bed) for bed in beds)]
    if status == "AVAILABLE":
        lines.append(AVAILABLE_IS_NOT_ELIGIBLE)
    return "\n".join(lines)
