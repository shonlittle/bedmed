"""Tools: the only way the assistant can read or change hospital state.

Each tool is one documented API call. The assistant has no database access, and a tool
reports success only when the API returned a success response.
"""

import httpx


class ApiError(Exception):
    """The API did not complete the request."""

    def __init__(self, code: str, message: str, reasons: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.reasons = reasons or []


class NotFoundError(ApiError):
    pass


class ConflictError(ApiError):
    pass


class BackendError(ApiError):
    """The API was unreachable or returned something unexpected. Nothing can be assumed about the outcome."""


class HospitalApi:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def get_patient(self, patient_id: str) -> dict:
        return self._request("GET", f"/patients/{patient_id}")

    def search_beds(self, patient_id: str, requirements: dict) -> dict:
        return self._request("POST", "/beds/search", {"patient_id": patient_id, "requirements": requirements})

    def assign_bed(self, patient_id: str, bed_id: str, requirements: dict) -> dict:
        body = {"patient_id": patient_id, "bed_id": bed_id, "requirements": requirements}
        return self._request("POST", "/assignments", body)

    def create_escalation(self, patient_id: str, requirements: dict, reason: str) -> dict:
        body = {"patient_id": patient_id, "requirements": requirements, "reason": reason}
        return self._request("POST", "/escalations", body)

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            response = self._client.request(method, path, json=body)
        except httpx.HTTPError as error:
            raise BackendError(
                "BACKEND_UNREACHABLE", f"Could not reach the hospital API: {type(error).__name__}"
            ) from error

        if response.is_success:
            return response.json()

        code, message, reasons = _error_detail(response)
        if response.status_code == httpx.codes.NOT_FOUND:
            raise NotFoundError(code, message)
        if response.status_code == httpx.codes.CONFLICT:
            raise ConflictError(code, message, reasons)
        raise BackendError(code, message)


def _error_detail(response: httpx.Response) -> tuple[str, str, list[str]]:
    fallback = (f"HTTP_{response.status_code}", f"The hospital API returned HTTP {response.status_code}.", [])
    try:
        detail = response.json().get("detail")
    except ValueError:
        return fallback
    if not isinstance(detail, dict):
        return fallback
    return detail.get("code", fallback[0]), detail.get("message", fallback[1]), detail.get("reasons", [])
