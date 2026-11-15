"""Unit tests for the eligibility and ranking rules. No database is involved."""

import pytest

from api.eligibility import RejectionReason, evaluate_bed, rank_beds
from api.models import (
    Assignment,
    Bed,
    BedStatus,
    CompatibilityRule,
    Patient,
    Room,
    RoomType,
    Sex,
    Ward,
    WardStatus,
    WardType,
)
from api.schemas import PlacementRequirements


def make_bed(
    *,
    bed_id: str = "W-1A",
    ward_type: WardType = WardType.MEDICAL,
    ward_status: WardStatus = WardStatus.OPEN,
    room_type: RoomType = RoomType.PRIVATE,
    rule: CompatibilityRule = CompatibilityRule.SAME_SEX,
    status: BedStatus = BedStatus.AVAILABLE,
    isolation: bool = False,
    monitoring: bool = False,
    roommate_sexes: tuple[Sex, ...] = (),
) -> Bed:
    ward = Ward(id="W", name="Ward", ward_type=ward_type, status=ward_status)
    room = Room(id="W-1", ward=ward, room_type=room_type, compatibility_rule=rule)
    bed = Bed(id=bed_id, room=room, status=status, isolation_capable=isolation, monitoring_capable=monitoring)
    for index, sex in enumerate(roommate_sexes):
        other = Bed(
            id=f"W-1R{index}", room=room, status=BedStatus.OCCUPIED, isolation_capable=False, monitoring_capable=False
        )
        Assignment(bed=other, patient=Patient(id=f"R-{index}", full_name="Roommate", sex=sex))
    return bed


def make_patient(sex: Sex = Sex.FEMALE) -> Patient:
    return Patient(id="P-1", full_name="Test Patient", sex=sex)


def make_requirements(
    *,
    ward_type: WardType = WardType.MEDICAL,
    isolation: bool = False,
    monitoring: bool = False,
    private: bool = False,
) -> PlacementRequirements:
    return PlacementRequirements(
        ward_type=ward_type,
        isolation_required=isolation,
        monitoring_required=monitoring,
        requires_private_room=private,
    )


def test_available_bed_in_matching_ward_is_eligible():
    assert evaluate_bed(make_bed(), make_patient(), make_requirements()) == []


def test_ward_type_mismatch_is_rejected():
    bed = make_bed(ward_type=WardType.SURGICAL)
    assert evaluate_bed(bed, make_patient(), make_requirements()) == [RejectionReason.WARD_TYPE_MISMATCH]


def test_closed_ward_is_rejected_even_when_bed_is_available():
    bed = make_bed(ward_status=WardStatus.CLOSED)
    assert evaluate_bed(bed, make_patient(), make_requirements()) == [RejectionReason.WARD_UNAVAILABLE]


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (BedStatus.OCCUPIED, RejectionReason.BED_OCCUPIED),
        (BedStatus.CLEANING, RejectionReason.BED_CLEANING),
        (BedStatus.RESERVED, RejectionReason.BED_RESERVED),
        (BedStatus.OUT_OF_SERVICE, RejectionReason.BED_OUT_OF_SERVICE),
    ],
)
def test_non_available_status_is_rejected(status: BedStatus, reason: RejectionReason):
    assert evaluate_bed(make_bed(status=status), make_patient(), make_requirements()) == [reason]


def test_isolation_requirement_needs_isolation_capable_bed():
    requirements = make_requirements(isolation=True)
    assert evaluate_bed(make_bed(), make_patient(), requirements) == [RejectionReason.NOT_ISOLATION_CAPABLE]
    assert evaluate_bed(make_bed(isolation=True), make_patient(), requirements) == []


def test_monitoring_requirement_needs_monitoring_capable_bed():
    requirements = make_requirements(monitoring=True)
    assert evaluate_bed(make_bed(), make_patient(), requirements) == [RejectionReason.NOT_MONITORING_CAPABLE]
    assert evaluate_bed(make_bed(monitoring=True), make_patient(), requirements) == []


def test_private_room_requirement_rejects_shared_room():
    bed = make_bed(room_type=RoomType.SHARED)
    assert evaluate_bed(bed, make_patient(), make_requirements(private=True)) == [
        RejectionReason.PRIVATE_ROOM_REQUIRED
    ]


def test_shared_room_with_same_sex_roommate_is_eligible():
    bed = make_bed(room_type=RoomType.SHARED, roommate_sexes=(Sex.FEMALE,))
    assert evaluate_bed(bed, make_patient(Sex.FEMALE), make_requirements()) == []


def test_shared_room_with_other_sex_roommate_is_rejected():
    bed = make_bed(room_type=RoomType.SHARED, roommate_sexes=(Sex.MALE,))
    assert evaluate_bed(bed, make_patient(Sex.FEMALE), make_requirements()) == [
        RejectionReason.ROOM_SHARING_INCOMPATIBLE
    ]


def test_shared_room_is_undetermined_when_sex_is_unknown():
    bed = make_bed(room_type=RoomType.SHARED, roommate_sexes=(Sex.FEMALE,))
    assert evaluate_bed(bed, make_patient(Sex.UNKNOWN), make_requirements()) == [
        RejectionReason.ROOM_SHARING_UNDETERMINED
    ]


def test_empty_shared_room_needs_no_compatibility_check():
    bed = make_bed(room_type=RoomType.SHARED)
    assert evaluate_bed(bed, make_patient(Sex.UNKNOWN), make_requirements()) == []


def test_sharing_policy_is_configurable_per_room():
    bed = make_bed(room_type=RoomType.SHARED, rule=CompatibilityRule.NONE, roommate_sexes=(Sex.MALE,))
    assert evaluate_bed(bed, make_patient(Sex.FEMALE), make_requirements()) == []


def test_all_failing_rules_are_reported():
    bed = make_bed(status=BedStatus.CLEANING, room_type=RoomType.SHARED)
    requirements = make_requirements(isolation=True, private=True)
    assert evaluate_bed(bed, make_patient(), requirements) == [
        RejectionReason.BED_CLEANING,
        RejectionReason.NOT_ISOLATION_CAPABLE,
        RejectionReason.PRIVATE_ROOM_REQUIRED,
    ]


def test_ranking_prefers_bed_that_wastes_fewest_scarce_features():
    plain = make_bed(bed_id="B-PLAIN", room_type=RoomType.SHARED)
    isolation_private = make_bed(bed_id="A-ISOLATION", isolation=True)
    ranked = rank_beds([isolation_private, plain], make_requirements())
    assert [bed.id for bed in ranked] == ["B-PLAIN", "A-ISOLATION"]


def test_ranking_breaks_ties_by_bed_id():
    first = make_bed(bed_id="A", room_type=RoomType.SHARED)
    second = make_bed(bed_id="B", room_type=RoomType.SHARED)
    assert [bed.id for bed in rank_beds([second, first], make_requirements())] == ["A", "B"]
