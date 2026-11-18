"""Evaluation of policy retrieval, kept separate from the extraction evaluation.

Each case is a question and the policy section that answers it, or no section when the
policies do not cover it. Two things are scored separately:
retrieval (was the right section among those retrieved?) and
the answer (was it grounded in that section, or correctly declined?).
Run with `make eval`. It calls the real models.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from assistant.config import ConfigError, load_settings
from assistant.policy import PolicyError, PolicyGuide, build_policy_guide

DEFAULT_CASES = Path(__file__).resolve().parent.parent / "evals" / "policy_cases.jsonl"


class PolicyCase(BaseModel):
    id: str
    question: str
    expected_source: str | None = None  # None: the policies do not answer this, so the guide must decline.


@dataclass(frozen=True)
class PolicyCaseResult:
    case: PolicyCase
    retrieved_ids: list[str]
    cited_ids: list[str]
    found: bool
    error: str | None = None

    @property
    def answerable(self) -> bool:
        return self.case.expected_source is not None

    @property
    def retrieval_hit(self) -> bool:
        return self.case.expected_source in self.retrieved_ids

    @property
    def answer_correct(self) -> bool:
        """Answerable: answered and cited the expected section. Unanswerable: declined."""
        if self.error:
            return False
        if not self.answerable:
            return not self.found
        return self.found and self.case.expected_source in self.cited_ids


class CasesError(Exception):
    pass


def load_cases(path: Path) -> list[PolicyCase]:
    cases: list[PolicyCase] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(PolicyCase.model_validate_json(line))
        except ValidationError as error:
            raise CasesError(f"{path.name} line {number} is not a valid case: {error.errors()[0]['msg']}") from error
    return cases


def score_case(case: PolicyCase, guide: PolicyGuide) -> PolicyCaseResult:
    try:
        reply = guide.answer(case.question)
    except PolicyError as error:
        return PolicyCaseResult(case, [], [], found=False, error=str(error))
    return PolicyCaseResult(
        case,
        retrieved_ids=[item.chunk.id for item in reply.retrieved],
        cited_ids=[chunk.id for chunk in reply.sources],
        found=reply.found,
    )


def format_report(results: list[PolicyCaseResult]) -> str:
    answerable = [result for result in results if result.answerable]
    hits = sum(result.retrieval_hit for result in answerable)
    correct = sum(result.answer_correct for result in results)
    lines = [
        f"Retrieval: expected section retrieved for {hits}/{len(answerable)} answerable questions",
        f"Answers: {correct}/{len(results)} correct (grounded in the expected section, or correctly declined)",
    ]
    failures = [
        result for result in results if not result.answer_correct or (result.answerable and not result.retrieval_hit)
    ]
    if failures:
        lines += ["", "Failures:"]
    for result in failures:
        lines.append(f"  [{result.case.id}] {result.case.question!r}")
        lines.append(f"      expected source: {result.case.expected_source or '(none: should decline)'}")
        lines.append(f"      retrieved: {', '.join(result.retrieved_ids) or '(nothing)'}")
        lines.append(f"      answered: {result.found}, cited: {', '.join(result.cited_ids) or '(nothing)'}")
        if result.error:
            lines.append(f"      error: {result.error}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Score policy retrieval and answers against labelled questions")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="path to a JSONL file of cases")
    args = parser.parse_args()

    settings = load_settings()
    try:
        settings.require_llm_key()
        cases = load_cases(args.cases)
    except (ConfigError, CasesError, OSError) as error:
        print(f"Cannot run the evaluation: {error}", file=sys.stderr)
        return 2

    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=30, max_retries=4)
    guide = build_policy_guide(client, settings.model, settings.embedding_model)
    results = [score_case(case, guide) for case in cases]
    print(f"Models: {settings.embedding_model} (retrieval), {settings.model} (answers)")
    print(format_report(results))
    return 0 if all(result.answer_correct for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
