"""Deterministic bed eligibility and ranking.

Eligibility answers "may this patient use this bed?" and returns every reason when the answer is no.
Ranking answers "which eligible bed is preferred?". The two are kept separate on purpose.
Nothing here calls an LLM or writes to the database.
"""

from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from api.models import Bed, BedStatus, CompatibilityRule, Patient, RoomType, Sex, WardStatus, WardType


class RejectionReason(StrEnum):
    WARD_TYPE_MISMATCH = "WARD_TYPE_MISMATCH"
    WARD_UNAVAILABLE = "WARD_UNAVAILABLE"
    BED_OCCUPIED = "BED_OCCUPIED"
    BED_CLEANING = "BED_CLEANING"
    BED_RESERVED = "BED_RESERVED"
    BED_OUT_OF_SERVICE = "BED_OUT_OF_SERVICE"
    NOT_ISOLATION_CAPABLE = "NOT_ISOLATION_CAPABLE"
    NOT_MONITORING_CAPABLE = "NOT_MONITORING_CAPABLE"
    PRIVATE_ROOM_REQUIRED = "PRIVATE_ROOM_REQUIRED"
    ROOM_SHARING_INCOMPATIBLE = "ROOM_SHARING_INCOMPATIBLE"
    ROOM_SHARING_UNDETERMINED = "ROOM_SHARING_UNDETERMINED"


class Requirements(Protocol):
    ward_type: WardType
    isolation_required: bool
    monitoring_required: bool
    requires_private_room: bool


STATUS_REASONS = {
    BedStatus.OCCUPIED: RejectionReason.BED_OCCUPIED,
    BedStatus.CLEANING: RejectionReason.BED_CLEANING,
    BedStatus.RESERVED: RejectionReason.BED_RESERVED,
    BedStatus.OUT_OF_SERVICE: RejectionReason.BED_OUT_OF_SERVICE,
}

SharingRule = Callable[[Patient, list[Patient]], RejectionReason | None]


def allow_any_roommates(patient: Patient, roommates: list[Patient]) -> RejectionReason | None:
    return None


def require_same_sex(patient: Patient, roommates: list[Patient]) -> RejectionReason | None:
    sexes = [patient.sex, *(roommate.sex for roommate in roommates)]
    if Sex.UNKNOWN in sexes:
        return RejectionReason.ROOM_SHARING_UNDETERMINED
    if len(set(sexes)) > 1:
        return RejectionReason.ROOM_SHARING_INCOMPATIBLE
    return None


SHARING_RULES: dict[CompatibilityRule, SharingRule] = {
    CompatibilityRule.NONE: allow_any_roommates,
    CompatibilityRule.SAME_SEX: require_same_sex,
}


def current_roommates(bed: Bed) -> list[Patient]:
    return [other.assignment.patient for other in bed.room.beds if other.id != bed.id and other.assignment]


def room_sharing_reason(bed: Bed, patient: Patient) -> RejectionReason | None:
    if bed.room.room_type is RoomType.PRIVATE:
        return None
    roommates = current_roommates(bed)
    if not roommates:
        return None
    return SHARING_RULES[bed.room.compatibility_rule](patient, roommates)


def evaluate_bed(bed: Bed, patient: Patient, requirements: Requirements) -> list[RejectionReason]:
    """Return every reason the bed is ineligible for this patient. An empty list means eligible."""
    reasons: list[RejectionReason] = []
    ward = bed.room.ward

    if ward.ward_type != requirements.ward_type:
        reasons.append(RejectionReason.WARD_TYPE_MISMATCH)
    if ward.status is not WardStatus.OPEN:
        reasons.append(RejectionReason.WARD_UNAVAILABLE)
    if bed.status in STATUS_REASONS:
        reasons.append(STATUS_REASONS[bed.status])
    if requirements.isolation_required and not bed.isolation_capable:
        reasons.append(RejectionReason.NOT_ISOLATION_CAPABLE)
    if requirements.monitoring_required and not bed.monitoring_capable:
        reasons.append(RejectionReason.NOT_MONITORING_CAPABLE)
    if requirements.requires_private_room and bed.room.room_type is not RoomType.PRIVATE:
        reasons.append(RejectionReason.PRIVATE_ROOM_REQUIRED)

    sharing_reason = room_sharing_reason(bed, patient)
    if sharing_reason:
        reasons.append(sharing_reason)

    return reasons


def unneeded_scarce_features(bed: Bed, requirements: Requirements) -> int:
    return sum(
        [
            bed.isolation_capable and not requirements.isolation_required,
            bed.monitoring_capable and not requirements.monitoring_required,
            bed.room.room_type is RoomType.PRIVATE and not requirements.requires_private_room,
        ]
    )


def rank_beds(eligible_beds: list[Bed], requirements: Requirements) -> list[Bed]:
    """Prefer the bed that wastes the fewest scarce features, keeping specialised beds free for those who need them."""
    return sorted(eligible_beds, key=lambda bed: (unneeded_scarce_features(bed, requirements), bed.id))
