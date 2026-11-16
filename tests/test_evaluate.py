"""Tests for the extraction evaluation: the case file and the scoring. No LLM is called."""

from pathlib import Path

import pytest

from assistant.evaluate import (
    DEFAULT_CASES,
    CasesError,
    EvalCase,
    exact_match_rate,
    field_accuracy,
    format_report,
    load_cases,
    score_case,
    score_cases,
)
from assistant.extractor import ExtractionError
from assistant.models import Extraction, Intent


class FixedExtractor:
    """Returns the same extraction (or raises the same error) for every message."""

    def __init__(self, result: Extraction | Exception) -> None:
        self._result = result
        self.contexts = []

    def extract(self, message, context):
        self.contexts.append(context)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def make_case(case_id: str = "case", **expected) -> EvalCase:
    return EvalCase(id=case_id, message="(message)", expected={"intent": "REQUEST_BED", **expected})


def test_shipped_cases_are_valid_and_cover_every_intent():
    cases = load_cases(DEFAULT_CASES)
    assert len(cases) >= 40
    assert {case.expected.intent for case in cases} == set(Intent)


def test_shipped_cases_cover_every_conversation_stage():
    stages = {case.context.stage for case in load_cases(DEFAULT_CASES)}
    assert stages == {"IDLE", "COLLECTING", "AWAITING_CONFIRMATION"}


def test_invalid_case_file_is_rejected_with_the_line_number(tmp_path: Path):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        '{"id": "ok", "message": "hi", "expected": {"intent": "UNKNOWN"}}\n{"id": "bad", "message": "hi"}\n'
    )
    with pytest.raises(CasesError, match="line 2"):
        load_cases(path)


def test_duplicate_case_ids_are_rejected(tmp_path: Path):
    line = '{"id": "same", "message": "hi", "expected": {"intent": "UNKNOWN"}}\n'
    path = tmp_path / "cases.jsonl"
    path.write_text(line * 2)
    with pytest.raises(CasesError, match="duplicate case ids: same"):
        load_cases(path)


def test_matching_extraction_passes():
    case = make_case(patient_id="P-1001", ward_type="MEDICAL")
    actual = Extraction(intent=Intent.REQUEST_BED, patient_id="1001", ward_type="MEDICAL")
    assert score_case(case, FixedExtractor(actual)).passed


def test_every_differing_field_is_reported():
    case = make_case(patient_id="P-1001", ward_type="MEDICAL")
    actual = Extraction(intent=Intent.PROVIDE_INFO, patient_id="P-1001", monitoring_required=True)

    result = score_case(case, FixedExtractor(actual))

    assert not result.passed
    assert result.mismatches == {
        "intent": (Intent.REQUEST_BED, Intent.PROVIDE_INFO),
        "ward_type": ("MEDICAL", None),
        "monitoring_required": (None, True),
    }


def test_inventing_an_unstated_detail_is_a_failure():
    result = score_case(make_case(), FixedExtractor(Extraction(intent=Intent.REQUEST_BED, isolation_required=False)))
    assert result.mismatches == {"isolation_required": (None, False)}


def test_patient_names_are_compared_without_regard_to_case():
    case = make_case(patient_name="James Okafor")
    actual = Extraction(intent=Intent.REQUEST_BED, patient_name=" james okafor ")
    assert score_case(case, FixedExtractor(actual)).passed


def test_extractor_failure_is_recorded_not_raised():
    result = score_case(make_case(), FixedExtractor(ExtractionError("LLM unavailable")))
    assert not result.passed
    assert result.error == "LLM unavailable"


def test_case_context_is_passed_to_the_extractor():
    case = EvalCase(
        id="context",
        message="the last one",
        context={"stage": "COLLECTING", "pending_slots": ["patient_id"], "option_count": 3},
        expected={"intent": "PROVIDE_INFO", "choice_number": 3},
    )
    extractor = FixedExtractor(Extraction(intent=Intent.PROVIDE_INFO, choice_number=3))

    assert score_case(case, extractor).passed
    context = extractor.contexts[0]
    assert (context.stage, context.pending_slots, context.option_count) == ("COLLECTING", ["patient_id"], 3)


def test_summary_counts_exact_matches_and_per_field_accuracy():
    cases = [make_case("right", ward_type="MEDICAL"), make_case("wrong", ward_type="ICU")]
    results = score_cases(cases, FixedExtractor(Extraction(intent=Intent.REQUEST_BED, ward_type="MEDICAL")))

    assert exact_match_rate(results) == 0.5
    accuracy = field_accuracy(results)
    assert accuracy["intent"] == 1.0
    assert accuracy["ward_type"] == 0.5


def test_errored_case_counts_against_every_field():
    results = score_cases([make_case()], FixedExtractor(ExtractionError("boom")))
    assert exact_match_rate(results) == 0.0
    assert set(field_accuracy(results).values()) == {0.0}


def test_report_lists_failures_with_expected_and_actual():
    cases = [make_case("right", ward_type="MEDICAL"), make_case("wrong", ward_type="ICU")]
    results = score_cases(cases, FixedExtractor(Extraction(intent=Intent.REQUEST_BED, ward_type="MEDICAL")))

    report = format_report(results, "test-model")

    assert "Model: test-model" in report
    assert "Cases fully correct: 1/2 (50%)" in report
    assert "[wrong]" in report
    assert 'ward_type: expected "ICU", got "MEDICAL"' in report
    assert "[right]" not in report
