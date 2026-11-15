"""Intent and entity extraction. This is the only place the assistant calls an LLM."""

import logging
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from assistant.models import Extraction
from assistant.prompts import build_extraction_messages

logger = logging.getLogger("bedmed.extractor")


class ExtractionError(Exception):
    """The message could not be interpreted (LLM unavailable, refused, or returned invalid output)."""


@dataclass(frozen=True)
class ExtractionContext:
    """What the workflow was waiting for when the message arrived."""

    stage: str
    pending_slots: list[str]
    proposed_bed_id: str | None
    option_count: int = 0


class Extractor(Protocol):
    def extract(self, message: str, context: ExtractionContext) -> Extraction: ...


class LLMExtractor:
    """Extracts through an OpenAI-compatible API using schema-constrained output."""

    def __init__(self, client: OpenAI, model: str) -> None:
        self._client = client
        self._model = model

    def extract(self, message: str, context: ExtractionContext) -> Extraction:
        messages = build_extraction_messages(
            message, context.stage, context.pending_slots, context.proposed_bed_id, context.option_count
        )
        try:
            completion = self._client.chat.completions.parse(
                model=self._model,
                messages=messages,
                response_format=Extraction,
                temperature=0,
            )
        except (OpenAIError, ValidationError) as error:
            raise ExtractionError(f"LLM call failed: {type(error).__name__}") from error

        extraction = completion.choices[0].message.parsed
        if extraction is None:
            raise ExtractionError("LLM returned no structured output.")
        return extraction
