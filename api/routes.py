"""HTTP endpoints. Routes translate between HTTP and the placement service; rules live in the service."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api import directory, placement
from api.database import get_session
from api.models import BedStatus
from api.schemas import (
    AssignmentIn,
    AssignmentOut,
    BedListItemOut,
    BedOut,
    BedSearchIn,
    BedSearchOut,
    BedStatusIn,
    EscalationIn,
    EscalationOut,
    PatientOut,
    RejectedBedOut,
)

router = APIRouter()


def not_found(error: placement.NotFoundError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": error.code, "message": str(error)})


def conflict(error: placement.ConflictError) -> HTTPException:
    detail = {"code": error.code, "message": str(error), "reasons": error.reasons}
    return HTTPException(status.HTTP_409_CONFLICT, detail=detail)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/patients")
def list_patients(
    name: str | None = Query(default=None, min_length=2, max_length=100, description="Part of the patient's name."),
    has_bed: bool | None = Query(default=None, description="Only patients with (true) or without (false) a bed."),
    session: Session = Depends(get_session),
) -> list[PatientOut]:
    return [PatientOut.from_patient(patient) for patient in directory.list_patients(session, name, has_bed)]


@router.get("/beds")
def list_beds(
    bed_status: BedStatus | None = Query(default=None, alias="status", description="Only beds in this status."),
    session: Session = Depends(get_session),
) -> list[BedListItemOut]:
    """Operational status only. Whether a bed suits a particular patient is answered by POST /beds/search."""
    return [BedListItemOut.from_bed(bed) for bed in directory.list_beds(session, bed_status)]


@router.get("/patients/{patient_id}")
def get_patient(patient_id: str, session: Session = Depends(get_session)) -> PatientOut:
    try:
        return PatientOut.from_patient(placement.get_patient(session, patient_id))
    except placement.NotFoundError as error:
        raise not_found(error) from error


@router.post("/beds/search")
def search_beds(body: BedSearchIn, session: Session = Depends(get_session)) -> BedSearchOut:
    """Return the beds this patient may use, best match first, and why near misses were rejected."""
    try:
        result = placement.search_beds(session, body.patient_id, body.requirements)
    except placement.NotFoundError as error:
        raise not_found(error) from error
    return BedSearchOut(
        patient_id=body.patient_id,
        eligible=[BedOut.from_bed(bed) for bed in result.eligible],
        rejected=[RejectedBedOut(bed=BedOut.from_bed(bed), reasons=reasons) for bed, reasons in result.rejected],
        rejection_summary=result.rejection_summary,
    )


@router.post("/assignments", status_code=status.HTTP_201_CREATED)
def create_assignment(body: AssignmentIn, session: Session = Depends(get_session)) -> AssignmentOut:
    """Assign a bed. Eligibility is re-checked here; a 409 means the bed can no longer be used."""
    try:
        assignment = placement.assign_bed(session, body.patient_id, body.bed_id, body.requirements)
    except placement.NotFoundError as error:
        raise not_found(error) from error
    except placement.ConflictError as error:
        raise conflict(error) from error
    return AssignmentOut(
        id=assignment.id,
        patient_id=assignment.patient_id,
        bed_id=assignment.bed_id,
        created_at=assignment.created_at,
    )


@router.post("/escalations", status_code=status.HTTP_201_CREATED)
def create_escalation(body: EscalationIn, session: Session = Depends(get_session)) -> EscalationOut:
    try:
        escalation = placement.create_escalation(session, body.patient_id, body.requirements, body.reason)
    except placement.NotFoundError as error:
        raise not_found(error) from error
    return EscalationOut(
        id=escalation.id,
        patient_id=escalation.patient_id,
        reason=escalation.reason,
        rejection_summary=escalation.rejection_summary,
        status=escalation.status,
        created_at=escalation.created_at,
    )


@router.patch("/beds/{bed_id}/status")
def update_bed_status(bed_id: str, body: BedStatusIn, session: Session = Depends(get_session)) -> BedOut:
    try:
        return BedOut.from_bed(placement.update_bed_status(session, bed_id, body.status))
    except placement.NotFoundError as error:
        raise not_found(error) from error
    except placement.ConflictError as error:
        raise conflict(error) from error
