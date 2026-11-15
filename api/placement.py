"""Placement operations: bed search, assignment and escalation.

The API never trusts an earlier search. Assignment re-evaluates eligibility inside the
transaction that writes it, because state can change between search and assignment.
"""

import logging
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from api.eligibility import RejectionReason, evaluate_bed, rank_beds
from api.models import Assignment, Bed, BedStatus, Escalation, Patient, Room
from api.schemas import PlacementRequirements

logger = logging.getLogger("bedmed.placement")


class NotFoundError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ConflictError(Exception):
    def __init__(self, code: str, message: str, reasons: list[RejectionReason] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.reasons = reasons or []


@dataclass(frozen=True)
class BedSearchResult:
    eligible: list[Bed]
    rejected: list[tuple[Bed, list[RejectionReason]]]

    @property
    def rejection_summary(self) -> dict[RejectionReason, int]:
        return dict(Counter(reason for _, reasons in self.rejected for reason in reasons))


def get_patient(session: Session, patient_id: str) -> Patient:
    patient = session.get(Patient, patient_id)
    if patient is None:
        raise NotFoundError("PATIENT_NOT_FOUND", f"No patient with id {patient_id}.")
    return patient


def load_all_beds(session: Session) -> list[Bed]:
    statement = (
        select(Bed)
        .options(
            joinedload(Bed.room).options(
                joinedload(Room.ward),
                selectinload(Room.beds).selectinload(Bed.assignment).joinedload(Assignment.patient),
            )
        )
        .order_by(Bed.id)
    )
    return list(session.scalars(statement))


def search_beds(session: Session, patient_id: str, requirements: PlacementRequirements) -> BedSearchResult:
    patient = get_patient(session, patient_id)
    eligible: list[Bed] = []
    rejected: list[tuple[Bed, list[RejectionReason]]] = []

    for bed in load_all_beds(session):
        reasons = evaluate_bed(bed, patient, requirements)
        if not reasons:
            eligible.append(bed)
        elif RejectionReason.WARD_TYPE_MISMATCH not in reasons:
            # Beds in the wrong kind of ward are noise; report only the near misses.
            rejected.append((bed, reasons))

    logger.info("bed_search patient=%s eligible=%d rejected=%d", patient_id, len(eligible), len(rejected))
    return BedSearchResult(eligible=rank_beds(eligible, requirements), rejected=rejected)


def lock_room_beds(session: Session, room_id: str) -> list[Bed]:
    """Lock every bed in the room so status and roommates cannot change until this transaction ends."""
    statement = select(Bed).where(Bed.room_id == room_id).order_by(Bed.id).with_for_update()
    return list(session.scalars(statement))


def assign_bed(session: Session, patient_id: str, bed_id: str, requirements: PlacementRequirements) -> Assignment:
    patient = get_patient(session, patient_id)
    room_id = session.scalar(select(Bed.room_id).where(Bed.id == bed_id))
    if room_id is None:
        raise NotFoundError("BED_NOT_FOUND", f"No bed with id {bed_id}.")

    bed = next(bed for bed in lock_room_beds(session, room_id) if bed.id == bed_id)

    if patient.assignment is not None:
        raise ConflictError(
            "PATIENT_ALREADY_ASSIGNED", f"Patient {patient_id} is already in bed {patient.assignment.bed_id}."
        )

    reasons = evaluate_bed(bed, patient, requirements)
    if reasons:
        logger.info("assignment_conflict patient=%s bed=%s reasons=%s", patient_id, bed_id, [str(r) for r in reasons])
        raise ConflictError("BED_NOT_ELIGIBLE", f"Bed {bed_id} is not eligible for this patient.", reasons)

    assignment = Assignment(patient_id=patient_id, bed_id=bed_id)
    bed.status = BedStatus.OCCUPIED
    session.add(assignment)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        logger.info("assignment_conflict patient=%s bed=%s reasons=unique_constraint", patient_id, bed_id)
        raise ConflictError("ASSIGNMENT_CONFLICT", "The bed or patient was assigned by another request.") from error

    session.refresh(assignment)
    logger.info("assignment_succeeded patient=%s bed=%s assignment=%d", patient_id, bed_id, assignment.id)
    return assignment


def create_escalation(
    session: Session, patient_id: str, requirements: PlacementRequirements, reason: str
) -> Escalation:
    """Record a hand-off to a human. The rejection summary is computed here, not taken from the caller."""
    search = search_beds(session, patient_id, requirements)
    escalation = Escalation(
        patient_id=patient_id,
        reason=reason,
        requirements=requirements.model_dump(mode="json"),
        rejection_summary={str(key): count for key, count in search.rejection_summary.items()},
    )
    session.add(escalation)
    session.commit()
    session.refresh(escalation)
    logger.info("escalation_created patient=%s escalation=%d", patient_id, escalation.id)
    return escalation


def update_bed_status(session: Session, bed_id: str, status: BedStatus) -> Bed:
    bed = session.scalar(select(Bed).where(Bed.id == bed_id).with_for_update())
    if bed is None:
        raise NotFoundError("BED_NOT_FOUND", f"No bed with id {bed_id}.")
    if bed.assignment is not None and status is not BedStatus.OCCUPIED:
        raise ConflictError("BED_HAS_PATIENT", f"Bed {bed_id} has a patient assigned and must stay occupied.")
    bed.status = status
    session.commit()
    logger.info("bed_status_updated bed=%s status=%s", bed_id, status)
    return bed
