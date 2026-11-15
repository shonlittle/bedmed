"""API tests against the seed scenarios described in api/seed.py."""

from fastapi.testclient import TestClient

MEDICAL = {
    "ward_type": "MEDICAL",
    "isolation_required": False,
    "monitoring_required": False,
    "requires_private_room": False,
}
TELEMETRY = {**MEDICAL, "ward_type": "TELEMETRY", "monitoring_required": True}
ICU = {**MEDICAL, "ward_type": "ICU", "monitoring_required": True}
SURGICAL = {**MEDICAL, "ward_type": "SURGICAL"}


def search(client: TestClient, patient_id: str, requirements: dict) -> dict:
    response = client.post("/beds/search", json={"patient_id": patient_id, "requirements": requirements})
    assert response.status_code == 200
    return response.json()


def assign(client: TestClient, patient_id: str, bed_id: str, requirements: dict):
    return client.post("/assignments", json={"patient_id": patient_id, "bed_id": bed_id, "requirements": requirements})


def test_health(client: TestClient):
    assert client.get("/health").json() == {"status": "ok"}


def test_patient_lookup_returns_patient_and_current_bed(client: TestClient):
    assert client.get("/patients/P-1001").json() == {
        "id": "P-1001",
        "full_name": "Maria Alvarez",
        "sex": "FEMALE",
        "current_bed_id": None,
    }
    assert client.get("/patients/P-2001").json()["current_bed_id"] == "4M-102A"


def test_unknown_patient_returns_404(client: TestClient):
    response = client.get("/patients/P-9999")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PATIENT_NOT_FOUND"


def test_search_ranks_eligible_beds_and_explains_rejections(client: TestClient):
    result = search(client, "P-1001", MEDICAL)

    assert [bed["id"] for bed in result["eligible"]] == ["4M-102B", "4M-104B", "4M-101"]
    rejected = {item["bed"]["id"]: item["reasons"] for item in result["rejected"]}
    assert rejected == {
        "4M-102A": ["BED_OCCUPIED"],
        "4M-103A": ["BED_OCCUPIED"],
        "4M-103B": ["ROOM_SHARING_INCOMPATIBLE"],
        "4M-104A": ["BED_CLEANING"],
    }


def test_available_bed_is_not_eligible_when_room_sharing_policy_rejects_it(client: TestClient):
    result = search(client, "P-1002", MEDICAL)
    eligible_ids = [bed["id"] for bed in result["eligible"]]
    assert "4M-103B" in eligible_ids
    assert "4M-102B" not in eligible_ids


def test_unknown_sex_requires_review_for_occupied_shared_rooms(client: TestClient):
    result = search(client, "P-1004", MEDICAL)
    assert [bed["id"] for bed in result["eligible"]] == ["4M-104B", "4M-101"]
    assert result["rejection_summary"]["ROOM_SHARING_UNDETERMINED"] == 2


def test_search_with_no_eligible_bed_summarises_why(client: TestClient):
    result = search(client, "P-1003", ICU)
    assert result["eligible"] == []
    assert result["rejection_summary"] == {"BED_OCCUPIED": 2, "BED_CLEANING": 1}


def test_closed_ward_has_no_eligible_beds(client: TestClient):
    result = search(client, "P-1001", SURGICAL)
    assert result["eligible"] == []
    assert result["rejection_summary"] == {"WARD_UNAVAILABLE": 2}


def test_capability_that_no_bed_offers_yields_no_eligible_beds(client: TestClient):
    result = search(client, "P-1001", {**MEDICAL, "monitoring_required": True})
    assert result["eligible"] == []
    assert result["rejection_summary"]["NOT_MONITORING_CAPABLE"] == 7


def test_search_rejects_incomplete_requirements(client: TestClient):
    response = client.post("/beds/search", json={"patient_id": "P-1001", "requirements": {"ward_type": "MEDICAL"}})
    assert response.status_code == 422


def test_search_for_unknown_patient_returns_404(client: TestClient):
    response = client.post("/beds/search", json={"patient_id": "P-9999", "requirements": MEDICAL})
    assert response.status_code == 404


def test_assignment_succeeds_and_updates_state(client: TestClient):
    response = assign(client, "P-1001", "4M-102B", MEDICAL)

    assert response.status_code == 201
    assert response.json()["bed_id"] == "4M-102B"
    assert client.get("/patients/P-1001").json()["current_bed_id"] == "4M-102B"
    assert "4M-102B" not in [bed["id"] for bed in search(client, "P-1004", MEDICAL)["eligible"]]


def test_assignment_revalidates_eligibility_and_returns_409(client: TestClient):
    preferred = search(client, "P-1002", TELEMETRY)["eligible"][0]["id"]
    assert preferred == "5T-203A"

    # Another process takes the bed between search and assignment.
    assert client.patch(f"/beds/{preferred}/status", json={"status": "RESERVED"}).status_code == 200

    response = assign(client, "P-1002", preferred, TELEMETRY)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "BED_NOT_ELIGIBLE"
    assert response.json()["detail"]["reasons"] == ["BED_RESERVED"]
    assert client.get("/patients/P-1002").json()["current_bed_id"] is None

    assert [bed["id"] for bed in search(client, "P-1002", TELEMETRY)["eligible"]] == ["5T-201"]


def test_assignment_to_ineligible_bed_returns_409(client: TestClient):
    response = assign(client, "P-1001", "4M-103B", MEDICAL)
    assert response.status_code == 409
    assert response.json()["detail"]["reasons"] == ["ROOM_SHARING_INCOMPATIBLE"]


def test_patient_cannot_be_assigned_twice(client: TestClient):
    assert assign(client, "P-1001", "4M-102B", MEDICAL).status_code == 201
    response = assign(client, "P-1001", "4M-104B", MEDICAL)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PATIENT_ALREADY_ASSIGNED"


def test_assignment_to_unknown_bed_returns_404(client: TestClient):
    response = assign(client, "P-1001", "9Z-999", MEDICAL)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "BED_NOT_FOUND"


def test_escalation_records_server_computed_rejection_summary(client: TestClient):
    response = client.post(
        "/escalations",
        json={"patient_id": "P-1003", "requirements": ICU, "reason": "No eligible ICU bed."},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "OPEN"
    assert body["rejection_summary"] == {"BED_OCCUPIED": 2, "BED_CLEANING": 1}
    assert client.get("/patients/P-1003").json()["current_bed_id"] is None


def test_escalation_for_unknown_patient_returns_404(client: TestClient):
    response = client.post(
        "/escalations", json={"patient_id": "P-9999", "requirements": ICU, "reason": "No eligible ICU bed."}
    )
    assert response.status_code == 404


def test_cleaned_bed_becomes_eligible(client: TestClient):
    assert client.patch("/beds/2I-03/status", json={"status": "AVAILABLE"}).status_code == 200
    assert [bed["id"] for bed in search(client, "P-1003", ICU)["eligible"]] == ["2I-03"]


def test_bed_with_patient_cannot_be_marked_available(client: TestClient):
    response = client.patch("/beds/4M-102A/status", json={"status": "AVAILABLE"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "BED_HAS_PATIENT"


def test_status_update_for_unknown_bed_returns_404(client: TestClient):
    assert client.patch("/beds/9Z-999/status", json={"status": "CLEANING"}).status_code == 404
