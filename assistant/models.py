"""Intents and entities: the structured view of what the user said.

These models are the contract between the LLM and the workflow. The LLM fills them in;
it never decides what happens next.
"""

import re
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Intent(StrEnum):
    REQUEST_BED = "REQUEST_BED"
    PROVIDE_INFO = "PROVIDE_INFO"
    CONFIRM = "CONFIRM"
    DECLINE = "DECLINE"
    PATIENT_STATUS = "PATIENT_STATUS"
    CANCEL = "CANCEL"
    UNKNOWN = "UNKNOWN"


class WardType(StrEnum):
    MEDICAL = "MEDICAL"
    TELEMETRY = "TELEMETRY"
    ICU = "ICU"
    SURGICAL = "SURGICAL"


REQUIREMENT_SLOTS = ("ward_type", "isolation_required", "monitoring_required", "requires_private_room")
SLOT_NAMES = ("patient_id", *REQUIREMENT_SLOTS)

PATIENT_ID_PATTERN = re.compile(r"^P?-?\s*(\d{1,8})$", re.IGNORECASE)


class Slots(BaseModel):
    """What must be known before a bed search. None means "not stated yet", which is different from "no"."""

    patient_id: str | None = Field(default=None, description="Patient identifier such as P-1001.")
    ward_type: WardType | None = Field(default=None, description="Level of care the patient needs.")
    isolation_required: bool | None = Field(default=None, description="Whether the patient needs isolation.")
    monitoring_required: bool | None = Field(default=None, description="Whether cardiac monitoring is needed.")
    requires_private_room: bool | None = Field(default=None, description="Whether a private room is needed.")

    @field_validator("patient_id")
    @classmethod
    def normalise_patient_id(cls, value: str | None) -> str | None:
        """Accept "1001", "p1001" or "P-1001" and store "P-1001"."""
        if value is None:
            return None
        cleaned = value.strip()
        match = PATIENT_ID_PATTERN.match(cleaned)
        return f"P-{match.group(1)}" if match else cleaned.upper() or None

    def missing(self) -> list[str]:
        return [name for name in SLOT_NAMES if getattr(self, name) is None]

    def merged_with(self, newer: "Slots") -> "Slots":
        """Newer stated values replace older ones; unstated values change nothing."""
        return self.model_copy(update=newer.model_dump(exclude_none=True))

    def requirements(self) -> dict:
        return self.model_dump(mode="json", include=set(REQUIREMENT_SLOTS))


class Extraction(Slots):
    """One user message, interpreted."""

    intent: Intent = Field(description="What the user wants to do with this message.")

    def slots(self) -> Slots:
        return Slots(**self.model_dump(include=set(SLOT_NAMES)))
