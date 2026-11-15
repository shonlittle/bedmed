"""Tests for the assistant's web server: the endpoints behind the demo UI."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant.models import Extraction, Intent
from assistant.web import EVENT_LOG, build_app
from tests.test_workflow import MEDICAL, ScriptedExtractor

SESSION = "test-session-0001"


@pytest.fixture
def extractor() -> ScriptedExtractor:
    return ScriptedExtractor()


@pytest.fixture
def documents(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text(
        "# Title\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n```mermaid\ngraph TD; A-->B\n```\n"
    )
    return tmp_path


@pytest.fixture
def web(client: TestClient, extractor: ScriptedExtractor, documents: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("BEDMED_DEMO_MODE", "1")
    EVENT_LOG.entries.clear()
    return TestClient(build_app(extractor, client, documents_root=documents))


def say(web: TestClient, extractor: ScriptedExtractor, intent: Intent, session: str = SESSION, **slots):
    extractor.result = Extraction(intent=intent, **slots)
    return web.post("/api/chat", json={"session_id": session, "message": "(scripted)"})


def test_index_page_and_static_files_are_served(web: TestClient):
    assert "<title>BedMed</title>" in web.get("/").text
    assert web.get("/static/app.js").status_code == 200
    assert web.get("/static/styles.css").status_code == 200


def test_chat_returns_reply_trace_and_proposed_bed(web: TestClient, extractor: ScriptedExtractor):
    response = say(web, extractor, Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)

    assert response.status_code == 200
    body = response.json()
    assert "Shall I assign 4M-102B?" in body["reply"]
    assert body["stage"] == "AWAITING_CONFIRMATION"
    assert body["proposed_bed_id"] == "4M-102B"
    assert [(step["node"], step["kind"]) for step in body["steps"]] == [
        ("interpret", "llm"),
        ("collect", "logic"),
        ("identify_patient", "tool"),
        ("search_beds", "tool"),
        ("propose", "logic"),
    ]
    assert body["steps"][0]["event"].startswith("intent_detected intent=REQUEST_BED")
    assert body["steps"][3]["event"] == "bed_search patient=P-1001 eligible=3 rejected=4"


def test_trace_never_contains_the_patient_name(web: TestClient, extractor: ScriptedExtractor):
    body = say(web, extractor, Intent.REQUEST_BED, patient_name="Maria Alvarez", **MEDICAL).json()
    events = " ".join(step["event"] for step in body["steps"])
    assert "patient_name=(given)" in events
    assert "Maria" not in events


def test_conversations_are_separate_per_session(web: TestClient, extractor: ScriptedExtractor):
    say(web, extractor, Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)
    other = say(web, extractor, Intent.CONFIRM, session="other-session-0002").json()
    assert other["proposed_bed_id"] is None
    assert "I didn't understand" in other["reply"]

    assert "Confirmed by the hospital system" in say(web, extractor, Intent.CONFIRM).json()["reply"]


@pytest.mark.parametrize(
    "body",
    [
        {"session_id": SESSION, "message": ""},
        {"session_id": SESSION, "message": "x" * 1001},
        {"session_id": "short", "message": "hello"},
        {"session_id": "has spaces in it", "message": "hello"},
        {"message": "hello"},
    ],
)
def test_chat_rejects_invalid_input(web: TestClient, body: dict):
    assert web.post("/api/chat", json=body).status_code == 422


def test_chat_explains_a_missing_llm_key(client: TestClient, documents: Path):
    web = TestClient(build_app(None, client, documents_root=documents))
    response = web.post("/api/chat", json={"session_id": SESSION, "message": "hello"})
    assert response.status_code == 503
    assert "OPENROUTER_API_KEY is not set" in response.json()["detail"]
    assert web.get("/api/board").status_code == 200


def test_board_shows_beds_patients_and_escalations(web: TestClient, extractor: ScriptedExtractor):
    board = web.get("/api/board").json()
    assert len(board["beds"]) == 16
    assert len(board["patients"]) == 9
    assert board["escalations"] == []
    surgical = next(bed for bed in board["beds"] if bed["id"] == "3S-301A")
    assert surgical["ward_status"] == "CLOSED"

    icu = {**MEDICAL, "ward_type": "ICU", "monitoring_required": True}
    say(web, extractor, Intent.REQUEST_BED, patient_id="P-1003", **icu)
    escalations = web.get("/api/board").json()["escalations"]
    assert [item["patient_id"] for item in escalations] == ["P-1003"]


def test_board_reflects_an_assignment(web: TestClient, extractor: ScriptedExtractor):
    say(web, extractor, Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)
    say(web, extractor, Intent.CONFIRM)
    beds = {bed["id"]: bed for bed in web.get("/api/board").json()["beds"]}
    assert beds["4M-102B"]["status"] == "OCCUPIED"
    assert beds["4M-102B"]["patient_id"] == "P-1001"


def test_board_reports_an_unreachable_api(extractor: ScriptedExtractor, documents: Path):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    http_client = httpx.Client(base_url="http://api.invalid", transport=httpx.MockTransport(refuse))
    web = TestClient(build_app(extractor, http_client, documents_root=documents))
    assert web.get("/api/board").status_code == 503
    assert web.post("/api/demo/reset").status_code == 503


def test_demo_control_changes_a_bed_and_causes_a_conflict(web: TestClient, extractor: ScriptedExtractor):
    telemetry = {**MEDICAL, "ward_type": "TELEMETRY", "monitoring_required": True}
    say(web, extractor, Intent.REQUEST_BED, patient_id="P-1002", **telemetry)

    assert web.post("/api/demo/beds/5T-203A/status", json={"status": "RESERVED"}).status_code == 204

    body = say(web, extractor, Intent.CONFIRM).json()
    assert "Bed 5T-203A could not be assigned" in body["reply"]
    assert body["proposed_bed_id"] == "5T-201"
    assert [step["node"] for step in body["steps"]] == ["interpret", "assign", "search_beds", "propose"]


def test_demo_control_passes_on_the_api_refusal(web: TestClient):
    response = web.post("/api/demo/beds/4M-102A/status", json={"status": "AVAILABLE"})
    assert response.status_code == 409
    assert "must stay occupied" in response.json()["detail"]

    assert web.post("/api/demo/beds/9Z-999/status", json={"status": "CLEANING"}).status_code == 404
    assert web.post("/api/demo/beds/4M-101/status", json={"status": "BROKEN"}).status_code == 422
    assert web.post("/api/demo/beds/bad%20id!/status", json={"status": "CLEANING"}).status_code == 422


def test_reset_restores_seed_data_and_forgets_conversations(web: TestClient, extractor: ScriptedExtractor):
    say(web, extractor, Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)
    say(web, extractor, Intent.CONFIRM)
    say(web, extractor, Intent.REQUEST_BED, patient_id="P-1002", **MEDICAL)

    assert web.post("/api/demo/reset").status_code == 204

    beds = {bed["id"]: bed for bed in web.get("/api/board").json()["beds"]}
    assert beds["4M-102B"]["status"] == "AVAILABLE"
    assert web.get("/api/logs").json() == []
    assert say(web, extractor, Intent.CONFIRM).json()["proposed_bed_id"] is None


def test_api_reset_is_disabled_outside_demo_mode(client: TestClient, monkeypatch):
    monkeypatch.delenv("BEDMED_DEMO_MODE", raising=False)
    assert client.post("/demo/reset").status_code == 404


def test_logs_list_workflow_events_newest_first(web: TestClient, extractor: ScriptedExtractor):
    say(web, extractor, Intent.REQUEST_BED, patient_id="P-1001", **MEDICAL)
    messages = [entry["message"] for entry in web.get("/api/logs").json()]
    assert messages[0].startswith("bed_proposed bed=4M-102B")
    assert messages[-1].startswith("intent_detected")
    assert all(entry["source"].startswith("bedmed") for entry in web.get("/api/logs").json())


def test_workflow_endpoint_describes_the_graph(web: TestClient):
    workflow = web.get("/api/workflow").json()
    assert "interpret" in workflow["mermaid"]
    kinds = {node["node"]: node["kind"] for node in workflow["nodes"]}
    assert kinds["interpret"] == "llm"
    assert kinds["assign"] == "tool"
    assert kinds["propose"] == "logic"
    assert [name for name, kind in kinds.items() if kind == "llm"] == ["interpret"]


def test_documents_list_only_files_that_exist(web: TestClient):
    assert web.get("/api/documents").json() == [{"slug": "readme", "title": "README"}]


def test_document_is_rendered_from_markdown(web: TestClient):
    page = web.get("/api/documents/readme").json()
    assert page["source"] == "README.md"
    assert "<h1>Title</h1>" in page["html"]
    assert "<table>" in page["html"]
    assert 'class="language-mermaid"' in page["html"]


def test_unknown_or_unwritten_documents_return_404(web: TestClient):
    assert web.get("/api/documents/design").status_code == 404
    assert web.get("/api/documents/..%2F.env").status_code == 404
    assert web.get("/api/documents/nope").status_code == 404
