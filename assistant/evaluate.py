"""Evaluation of the LLM step: score extraction against labelled cases.

Each case is a message, the context the workflow would supply, and the exact extraction
expected. Because the model's output is a small schema, scoring is exact match per field.
Run with `make eval`. It calls the real model, so it needs an LLM key and costs a little.
"""

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from assistant.config import ConfigError, load_settings
from assistant.extractor import ExtractionContext, ExtractionError, Extractor, LLMExtractor
from assistant.models import Extraction

DEFAULT_CASES = Path(__file__).resolve().parent.parent / "evals" / "extraction_cases.jsonl"
FIELDS = ("intent", *(name for name in Extraction.model_fields if name != "intent"))
PARALLEL_CALLS = 4


class CaseContext(BaseModel):
    stage: str = "IDLE"
    pending_slots: list[str] = Field(default_factory=list)
    proposed_bed_id: str | None = None
    option_count: int = 0

    def to_extraction_context(self) -> ExtractionContext:
        return ExtractionContext(**self.model_dump())


class EvalCase(BaseModel):
    id: str
    message: str
    context: CaseContext = Field(default_factory=CaseContext)
    expected: Extraction


@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    mismatches: dict[str, tuple[object, object]] = field(default_factory=dict)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return not self.mismatches and self.error is None


class CasesError(Exception):
    pass


def load_cases(path: Path) -> list[EvalCase]:
    """Read one JSON case per line. The file is validated in full before anything is run."""
    cases: list[EvalCase] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(EvalCase.model_validate_json(line))
        except ValidationError as error:
            raise CasesError(f"{path.name} line {number} is not a valid case: {error.errors()[0]['msg']}") from error
    duplicates = [case_id for case_id, count in Counter(case.id for case in cases).items() if count > 1]
    if duplicates:
        raise CasesError(f"{path.name} has duplicate case ids: {', '.join(duplicates)}")
    return cases


def comparable(name: str, value: object) -> object:
    """Names are compared without regard to case or surrounding spaces; everything else exactly."""
    if name == "patient_name" and isinstance(value, str):
        return value.strip().casefold()
    return value


def compare(expected: Extraction, actual: Extraction) -> dict[str, tuple[object, object]]:
    return {
        name: (getattr(expected, name), getattr(actual, name))
        for name in FIELDS
        if comparable(name, getattr(expected, name)) != comparable(name, getattr(actual, name))
    }


def score_case(case: EvalCase, extractor: Extractor) -> CaseResult:
    try:
        actual = extractor.extract(case.message, case.context.to_extraction_context())
    except ExtractionError as error:
        return CaseResult(case, error=str(error))
    return CaseResult(case, mismatches=compare(case.expected, actual))


def score_cases(cases: list[EvalCase], extractor: Extractor) -> list[CaseResult]:
    with ThreadPoolExecutor(max_workers=PARALLEL_CALLS) as executor:
        return list(executor.map(lambda case: score_case(case, extractor), cases))


def field_accuracy(results: list[CaseResult]) -> dict[str, float]:
    """Share of cases where each field was right. A case that errored counts as wrong for every field."""
    total = len(results)
    return {
        name: sum(1 for result in results if result.error is None and name not in result.mismatches) / total
        for name in FIELDS
    }


def exact_match_rate(results: list[CaseResult]) -> float:
    return sum(result.passed for result in results) / len(results)


def format_report(results: list[CaseResult], model: str) -> str:
    passed = sum(result.passed for result in results)
    lines = [
        f"Model: {model}",
        f"Cases fully correct: {passed}/{len(results)} ({exact_match_rate(results):.0%})",
        "",
        "Accuracy by field:",
        *(f"  {name:<24}{accuracy:.0%}" for name, accuracy in field_accuracy(results).items()),
    ]
    failures = [result for result in results if not result.passed]
    if failures:
        lines += ["", "Failures:"]
    for result in failures:
        lines.append(f"  [{result.case.id}] {result.case.message!r}")
        if result.error:
            lines.append(f"      error: {result.error}")
        for name, (expected, actual) in result.mismatches.items():
            lines.append(f"      {name}: expected {json.dumps(expected)}, got {json.dumps(actual)}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score LLM extraction against labelled cases")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="path to a JSONL file of cases")
    parser.add_argument(
        "--min-exact",
        type=float,
        default=0.0,
        help="fail (exit code 1) if the share of fully correct cases is below this, e.g. 0.95",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings()
    try:
        settings.require_llm_key()
        cases = load_cases(args.cases)
    except (ConfigError, CasesError, OSError) as error:
        print(f"Cannot run the evaluation: {error}", file=sys.stderr)
        return 2
    if not cases:
        print(f"Cannot run the evaluation: {args.cases} contains no cases.", file=sys.stderr)
        return 2

    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=30, max_retries=4)
    results = score_cases(cases, LLMExtractor(client, settings.model))
    print(format_report(results, settings.model))
    return 0 if exact_match_rate(results) >= args.min_exact else 1


if __name__ == "__main__":
    sys.exit(main())
