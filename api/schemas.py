"""Request and response contracts for the API."""

from datetime import datetime

from pydantic import BaseModel, Field

from api.eligibility import RejectionReason
from api.models import Bed, BedStatus, Escalation, EscalationStatus, Patient, RoomType, Sex, WardStatus, WardType


class PlacementRequirements(BaseModel):
    """What this patient needs from a bed. Every field is required: an unstated need is not the same as "no"."""

    ward_type: WardType
    isolation_required: bool
    monitoring_required: bool
    requires_private_room: bool


class PatientOut(BaseModel):
    id: str
    full_name: str
    sex: Sex
    current_bed_id: str | None

    @classmethod
    def from_patient(cls, patient: Patient) -> "PatientOut":
        return cls(
            id=patient.id,
            full_name=patient.full_name,
            sex=patient.sex,
            current_bed_id=patient.assignment.bed_id if patient.assignment else None,
        )


class BedOut(BaseModel):
    id: str
    room_id: str
    room_type: RoomType
    ward_id: str
    ward_name: str
    ward_type: WardType
    status: BedStatus
    isolation_capable: bool
    monitoring_capable: bool

    @classmethod
    def from_bed(cls, bed: Bed) -> "BedOut":
        return cls(
            id=bed.id,
            room_id=bed.room_id,
            room_type=bed.room.room_type,
            ward_id=bed.room.ward.id,
            ward_name=bed.room.ward.name,
            ward_type=bed.room.ward.ward_type,
            status=bed.status,
            isolation_capable=bed.isolation_capable,
            monitoring_capable=bed.monitoring_capable,
        )


class BedListItemOut(BedOut):
    """A bed in a listing, with who is in it, if anyone."""

    ward_status: WardStatus
    patient_id: str | None
    patient_name: str | None

    @classmethod
    def from_bed(cls, bed: Bed) -> "BedListItemOut":
        occupant = bed.assignment.patient if bed.assignment else None
        return cls(
            **BedOut.from_bed(bed).model_dump(),
            ward_status=bed.room.ward.status,
            patient_id=occupant.id if occupant else None,
            patient_name=occupant.full_name if occupant else None,
        )


class BedSearchIn(BaseModel):
    patient_id: str
    requirements: PlacementRequirements


class RejectedBedOut(BaseModel):
    bed: BedOut
    reasons: list[RejectionReason]


class BedSearchOut(BaseModel):
    patient_id: str
    eligible: list[BedOut] = Field(description="Eligible beds, best match first.")
    rejected: list[RejectedBedOut] = Field(description="Beds in a ward of the required type that are not eligible.")
    rejection_summary: dict[RejectionReason, int]


class AssignmentIn(BaseModel):
    patient_id: str
    bed_id: str
    requirements: PlacementRequirements


class AssignmentOut(BaseModel):
    id: int
    patient_id: str
    bed_id: str
    created_at: datetime


class EscalationIn(BaseModel):
    patient_id: str
    requirements: PlacementRequirements
    reason: str = Field(min_length=1, max_length=500)


class EscalationOut(BaseModel):
    id: int
    patient_id: str
    reason: str
    rejection_summary: dict[RejectionReason, int]
    status: EscalationStatus
    created_at: datetime

    @classmethod
    def from_escalation(cls, escalation: Escalation) -> "EscalationOut":
        return cls(
            id=escalation.id,
            patient_id=escalation.patient_id,
            reason=escalation.reason,
            rejection_summary=escalation.rejection_summary,
            status=escalation.status,
            created_at=escalation.created_at,
        )


class BedStatusIn(BaseModel):
    status: BedStatus
