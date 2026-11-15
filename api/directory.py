"""Read-only listings of patients and beds. Nothing here changes state."""

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from api.models import Assignment, Bed, BedStatus, Patient, Room


def escape_like(text: str) -> str:
    """Make user text safe to use inside a LIKE pattern, so % and _ match themselves."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_patients(session: Session, name: str | None = None, has_bed: bool | None = None) -> list[Patient]:
    """Patients ordered by ID. `name` matches any part of the full name, ignoring case."""
    statement = select(Patient).options(selectinload(Patient.assignment)).order_by(Patient.id)
    if name is not None:
        statement = statement.where(Patient.full_name.ilike(f"%{escape_like(name.strip())}%", escape="\\"))
    if has_bed is True:
        statement = statement.where(Patient.assignment.has())
    elif has_bed is False:
        statement = statement.where(~Patient.assignment.has())
    return list(session.scalars(statement))


def list_beds(session: Session, status: BedStatus | None = None) -> list[Bed]:
    """Beds ordered by ID, with their room, ward and current occupant loaded."""
    statement = (
        select(Bed)
        .options(
            joinedload(Bed.room).joinedload(Room.ward),
            selectinload(Bed.assignment).joinedload(Assignment.patient),
        )
        .order_by(Bed.id)
    )
    if status is not None:
        statement = statement.where(Bed.status == status)
    return list(session.scalars(statement))
