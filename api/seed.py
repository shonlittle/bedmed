"""Fictional seed data. Every row exists to support a demo scenario or a failure path.

Scenarios (patient -> request -> expected outcome):
- P-1001, medical: several eligible beds; 4M-103B is free but rejected by the room-sharing policy.
- P-1002, telemetry with monitoring: 5T-203A is preferred, 5T-201 is the fallback after a conflict.
- P-1003, ICU: every ICU bed is occupied or being cleaned, so the request escalates.
- P-1004, sex unknown: shared rooms with a roommate need human review; empty or private rooms are fine.
- P-1005 shares a surname with P-1001, so a search for "Alvarez" is ambiguous and must be clarified.
- Any patient, surgical: beds are free but the ward is closed.
- Any patient, medical with monitoring: no monitoring-capable medical bed exists.

Run `python -m api.seed` to reset the database to this state.
"""

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from api.database import Base
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

# (ward id, name, ward type, status)
WARDS = [
    ("4M", "4 Medical", WardType.MEDICAL, WardStatus.OPEN),
    ("5T", "5 Telemetry", WardType.TELEMETRY, WardStatus.OPEN),
    ("2I", "2 Intensive Care", WardType.ICU, WardStatus.OPEN),
    ("3S", "3 Surgical", WardType.SURGICAL, WardStatus.CLOSED),
]

# (room id, room type) -> the ward is the prefix before the dash.
ROOMS = [
    ("4M-101", RoomType.PRIVATE),
    ("4M-102", RoomType.SHARED),
    ("4M-103", RoomType.SHARED),
    ("4M-104", RoomType.SHARED),
    ("5T-201", RoomType.PRIVATE),
    ("5T-202", RoomType.PRIVATE),
    ("5T-203", RoomType.SHARED),
    ("2I-01", RoomType.PRIVATE),
    ("2I-02", RoomType.PRIVATE),
    ("2I-03", RoomType.PRIVATE),
    ("3S-301", RoomType.SHARED),
]

# (bed id, room id, status, isolation capable, monitoring capable)
BEDS = [
    ("4M-101", "4M-101", BedStatus.AVAILABLE, True, False),
    ("4M-102A", "4M-102", BedStatus.OCCUPIED, False, False),
    ("4M-102B", "4M-102", BedStatus.AVAILABLE, False, False),
    ("4M-103A", "4M-103", BedStatus.OCCUPIED, False, False),
    ("4M-103B", "4M-103", BedStatus.AVAILABLE, False, False),
    ("4M-104A", "4M-104", BedStatus.CLEANING, False, False),
    ("4M-104B", "4M-104", BedStatus.AVAILABLE, False, False),
    ("5T-201", "5T-201", BedStatus.AVAILABLE, True, True),
    ("5T-202", "5T-202", BedStatus.RESERVED, False, True),
    ("5T-203A", "5T-203", BedStatus.AVAILABLE, False, True),
    ("5T-203B", "5T-203", BedStatus.OUT_OF_SERVICE, False, True),
    ("2I-01", "2I-01", BedStatus.OCCUPIED, True, True),
    ("2I-02", "2I-02", BedStatus.OCCUPIED, True, True),
    ("2I-03", "2I-03", BedStatus.CLEANING, True, True),
    ("3S-301A", "3S-301", BedStatus.AVAILABLE, False, False),
    ("3S-301B", "3S-301", BedStatus.AVAILABLE, False, False),
]

PATIENTS = [
    # Waiting for a bed.
    ("P-1001", "Maria Alvarez", Sex.FEMALE),
    ("P-1002", "James Okafor", Sex.MALE),
    ("P-1003", "Priya Nair", Sex.FEMALE),
    ("P-1004", "Sam Rivera", Sex.UNKNOWN),
    ("P-1005", "Elena Alvarez", Sex.FEMALE),
    # Already placed.
    ("P-2001", "Helen Brooks", Sex.FEMALE),
    ("P-2002", "Daniel Kim", Sex.MALE),
    ("P-2003", "Rosa Delgado", Sex.FEMALE),
    ("P-2004", "Arthur Bell", Sex.MALE),
]

# (patient id, bed id) -> must match the OCCUPIED beds above.
ASSIGNMENTS = [
    ("P-2001", "4M-102A"),
    ("P-2002", "4M-103A"),
    ("P-2003", "2I-01"),
    ("P-2004", "2I-02"),
]


def build_room(room_id: str, room_type: RoomType) -> Room:
    rule = CompatibilityRule.SAME_SEX if room_type is RoomType.SHARED else CompatibilityRule.NONE
    return Room(id=room_id, ward_id=room_id.split("-")[0], room_type=room_type, compatibility_rule=rule)


def seed(session: Session) -> None:
    session.add_all(
        Ward(id=ward_id, name=name, ward_type=ward_type, status=status) for ward_id, name, ward_type, status in WARDS
    )
    session.flush()
    session.add_all(build_room(room_id, room_type) for room_id, room_type in ROOMS)
    session.add_all(
        Bed(id=bed_id, room_id=room_id, status=status, isolation_capable=isolation, monitoring_capable=monitoring)
        for bed_id, room_id, status, isolation, monitoring in BEDS
    )
    session.add_all(Patient(id=patient_id, full_name=name, sex=sex) for patient_id, name, sex in PATIENTS)
    session.flush()
    session.add_all(Assignment(patient_id=patient_id, bed_id=bed_id) for patient_id, bed_id in ASSIGNMENTS)
    session.commit()


def seed_if_empty(session: Session) -> None:
    if session.scalar(select(Ward.id).limit(1)) is None:
        seed(session)


def reset(engine: Engine) -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)


if __name__ == "__main__":
    from api.database import engine

    reset(engine)
    print("Database reset to seed data.")
