"""Operational data model. IDs double as the human-readable labels staff use (e.g. bed "4M-102B")."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.database import Base


class WardType(StrEnum):
    MEDICAL = "MEDICAL"
    TELEMETRY = "TELEMETRY"
    ICU = "ICU"
    SURGICAL = "SURGICAL"


class WardStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class RoomType(StrEnum):
    PRIVATE = "PRIVATE"
    SHARED = "SHARED"


class CompatibilityRule(StrEnum):
    """Room-sharing policy. Configured per room because it is hospital policy, not a universal rule."""

    NONE = "NONE"
    SAME_SEX = "SAME_SEX"


class BedStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    CLEANING = "CLEANING"
    RESERVED = "RESERVED"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


class Sex(StrEnum):
    FEMALE = "FEMALE"
    MALE = "MALE"
    UNKNOWN = "UNKNOWN"


class EscalationStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


def enum_column(enum_class: type[StrEnum]) -> Enum:
    return Enum(enum_class, native_enum=False, length=20)


class Ward(Base):
    __tablename__ = "wards"

    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    ward_type: Mapped[WardType] = mapped_column(enum_column(WardType))
    status: Mapped[WardStatus] = mapped_column(enum_column(WardStatus))

    rooms: Mapped[list["Room"]] = relationship(back_populates="ward")


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    ward_id: Mapped[str] = mapped_column(ForeignKey("wards.id"))
    room_type: Mapped[RoomType] = mapped_column(enum_column(RoomType))
    compatibility_rule: Mapped[CompatibilityRule] = mapped_column(enum_column(CompatibilityRule))

    ward: Mapped[Ward] = relationship(back_populates="rooms")
    beds: Mapped[list["Bed"]] = relationship(back_populates="room", order_by="Bed.id")


class Bed(Base):
    __tablename__ = "beds"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"))
    status: Mapped[BedStatus] = mapped_column(enum_column(BedStatus))
    isolation_capable: Mapped[bool] = mapped_column(Boolean)
    monitoring_capable: Mapped[bool] = mapped_column(Boolean)

    room: Mapped[Room] = relationship(back_populates="beds")
    assignment: Mapped["Assignment | None"] = relationship(back_populates="bed")


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(100))
    sex: Mapped[Sex] = mapped_column(enum_column(Sex))

    assignment: Mapped["Assignment | None"] = relationship(back_populates="patient")


class Assignment(Base):
    """A patient currently placed in a bed. The unique constraints are the last defence against double booking."""

    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), unique=True)
    bed_id: Mapped[str] = mapped_column(ForeignKey("beds.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    patient: Mapped[Patient] = relationship(back_populates="assignment")
    bed: Mapped[Bed] = relationship(back_populates="assignment")


class Escalation(Base):
    """A placement handed to a human because no eligible bed could be assigned."""

    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"))
    reason: Mapped[str] = mapped_column(String(500))
    requirements: Mapped[dict] = mapped_column(JSON)
    rejection_summary: Mapped[dict] = mapped_column(JSON)
    status: Mapped[EscalationStatus] = mapped_column(enum_column(EscalationStatus), default=EscalationStatus.OPEN)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
