"""Web server for the assistant: the demo UI and the endpoints it calls.

This is a separate service from the hospital API. The browser talks only to this server,
and this server reaches hospital data only through the API, like the CLI does.
It is a local demonstration and has no authentication.
"""

import logging
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import markdown
from fastapi import FastAPI, HTTPException, status
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel, Field

from assistant.config import MISSING_KEY_MESSAGE, load_settings
from assistant.extractor import Extractor, LLMExtractor
from assistant.models import BedStatus
from assistant.tools import ApiError, HospitalApi
from assistant.workflow import Conversation, Step, build_graph

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).parent / "static"

# Deliverables shown in the UI, rendered from the same Markdown files that GitHub displays.
DOCUMENTS = {
    "design": ("Design", "docs/design.md"),
    "scalability": ("Scalability", "docs/scalability.md"),
    "readme": ("README", "README.md"),
}
BED_ID_PATTERN = r"^[A-Za-z0-9-]{1,20}$"


class EventLog(logging.Handler):
    """Keeps the most recent workflow events in memory for the Logs page."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self.entries: deque[dict] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        self.entries.append(
            {
                "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
                "level": record.levelname,
                "source": record.name,
                "message": record.getMessage(),
            }
        )


EVENT_LOG = EventLog()
logging.getLogger("bedmed").addHandler(EVENT_LOG)
logging.getLogger("bedmed").setLevel(logging.INFO)


class ChatIn(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    message: str = Field(min_length=1, max_length=1000)


class StepOut(BaseModel):
    node: str
    kind: str
    event: str

    @classmethod
    def from_step(cls, step: Step) -> "StepOut":
        return cls(node=step.node, kind=step.kind, event=step.event)


class ChatOut(BaseModel):
    reply: str
    steps: list[StepOut]
    stage: str
    proposed_bed_id: str | None


class BedStatusIn(BaseModel):
    status: BedStatus


class Sessions:
    """Conversations keyed by browser session. State lives in memory and is lost on restart."""

    def __init__(self, extractor: Extractor, api: HospitalApi) -> None:
        self._extractor = extractor
        self._api = api
        self.reset()

    def reset(self) -> None:
        self._graph = build_graph(self._extractor, self._api)

    def conversation(self, session_id: str) -> Conversation:
        return Conversation(self._graph, thread_id=session_id)

    def diagram(self) -> str:
        return self._graph.get_graph().draw_mermaid()

    def nodes(self) -> list[StepOut]:
        names = [name for name in self._graph.get_graph().nodes if not name.startswith("__")]
        return [StepOut.from_step(Step(name, "")) for name in names]


def unavailable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=message)


def render_markdown(path: Path) -> str:
    return markdown.markdown(path.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])


def build_app(
    extractor: Extractor | None,
    http_client: httpx.Client,
    documents_root: Path = REPO_ROOT,
    lifespan=None,
) -> FastAPI:
    """Assemble the app. `extractor` is None when no LLM key is configured; chat then explains how to fix it."""
    app = FastAPI(title="BedMed assistant", docs_url=None, redoc_url=None, lifespan=lifespan)
    api = HospitalApi(http_client)
    sessions = Sessions(extractor, api) if extractor else None

    @app.post("/api/chat")
    def chat(body: ChatIn) -> ChatOut:
        if sessions is None:
            raise unavailable(MISSING_KEY_MESSAGE)
        conversation = sessions.conversation(body.session_id)
        turn = conversation.send(body.message.strip())
        return ChatOut(
            reply=turn.reply,
            steps=[StepOut.from_step(step) for step in turn.steps],
            stage=conversation.state["stage"],
            proposed_bed_id=conversation.state["proposed_bed_id"],
        )

    @app.get("/api/board")
    def board() -> dict:
        """Everything the hospital view shows, read fresh from the API."""
        try:
            return {
                "beds": api.list_beds(),
                "patients": api.list_patients(),
                "escalations": api.list_escalations(),
            }
        except ApiError as error:
            raise unavailable("The hospital API is not responding.") from error

    @app.get("/api/workflow")
    def workflow() -> dict:
        if sessions is None:
            raise unavailable(MISSING_KEY_MESSAGE)
        return {"mermaid": sessions.diagram(), "nodes": sessions.nodes()}

    @app.get("/api/logs")
    def logs() -> list[dict]:
        return list(reversed(EVENT_LOG.entries))

    @app.get("/api/documents")
    def list_documents() -> list[dict]:
        return [
            {"slug": slug, "title": title}
            for slug, (title, relative_path) in DOCUMENTS.items()
            if (documents_root / relative_path).is_file()
        ]

    @app.get("/api/documents/{slug}")
    def get_document(slug: str) -> dict:
        if slug not in DOCUMENTS:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such document.")
        title, relative_path = DOCUMENTS[slug]
        path = documents_root / relative_path
        if not path.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This document has not been written yet.")
        return {"title": title, "source": relative_path, "html": render_markdown(path)}

    # --- Demo controls: stand in for other hospital systems. They are not assistant tools. ---

    @app.post("/api/demo/beds/{bed_id}/status", status_code=status.HTTP_204_NO_CONTENT)
    def simulate_bed_status_change(body: BedStatusIn, bed_id: str = PathParam(pattern=BED_ID_PATTERN)) -> None:
        """Change a bed's status as if another system had done it, to demonstrate conflicts."""
        response = call_api("PATCH", f"/beds/{bed_id}/status", {"status": body.status})
        if not response.is_success:
            raise HTTPException(response.status_code, detail=error_message(response))

    @app.post("/api/demo/reset", status_code=status.HTTP_204_NO_CONTENT)
    def reset_demo() -> None:
        """Restore the seed data and forget every conversation."""
        response = call_api("POST", "/demo/reset")
        if not response.is_success:
            raise HTTPException(response.status_code, detail=error_message(response))
        if sessions is not None:
            sessions.reset()
        EVENT_LOG.entries.clear()

    def call_api(method: str, path: str, body: dict | None = None) -> httpx.Response:
        try:
            return http_client.request(method, path, json=body)
        except httpx.HTTPError as error:
            raise unavailable("The hospital API is not responding.") from error

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def error_message(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, dict) and "message" in detail:
        return detail["message"]
    return f"The hospital API returned HTTP {response.status_code}."


def create_app() -> FastAPI:
    """Entry point for `uvicorn assistant.web:create_app --factory`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = load_settings()
    http_client = httpx.Client(base_url=settings.api_url, timeout=10)
    extractor = None
    if settings.llm_api_key:
        llm_client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=30, max_retries=1)
        extractor = LLMExtractor(llm_client, settings.model)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        http_client.close()

    return build_app(extractor, http_client, lifespan=lifespan)
