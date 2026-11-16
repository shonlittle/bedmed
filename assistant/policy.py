"""Policy retrieval (RAG): answer questions about written hospital policy.

Retrieval is for unstructured policy text only. Live facts such as bed status always come
from the hospital API, and a policy answer can never trigger an action.

The steps are separate so each can be tested and evaluated on its own:
chunk the documents, embed them, retrieve the closest passages, then have the LLM answer
from those passages alone. Code then checks the answer's citations before it is shown.
"""

import logging
import math
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, Field, ValidationError

from assistant.extractor import describe_llm_error
from assistant.prompts import build_policy_answer_messages

logger = logging.getLogger("bedmed.policy")

DEFAULT_POLICY_DIRECTORY = Path(__file__).resolve().parent.parent / "policies"
PASSAGES_PER_QUESTION = 3
MAX_ANSWER_TOKENS = 600
SECTION_HEADING = re.compile(r"^## +(.+)$", re.MULTILINE)


class PolicyError(Exception):
    """Policy lookup could not be completed (embedding or LLM call failed, or no documents)."""


@dataclass(frozen=True)
class PolicyChunk:
    """One section of one policy document."""

    id: str
    document: str
    section: str
    text: str

    @property
    def citation(self) -> str:
        return f"{self.document}, section {self.section}"


def chunk_document(stem: str, markdown_text: str) -> list[PolicyChunk]:
    """Split a policy into its `##` sections. The document title stays with every chunk."""
    title_line = markdown_text.strip().splitlines()[0] if markdown_text.strip() else ""
    title = title_line.removeprefix("# ").strip() or stem
    parts = SECTION_HEADING.split(markdown_text)
    # parts is [preamble, heading 1, body 1, heading 2, body 2, ...]
    sections = zip(parts[1::2], parts[2::2], strict=True)
    return [
        PolicyChunk(id=f"{stem}#{number}", document=title, section=heading.strip(), text=body.strip())
        for number, (heading, body) in enumerate(sections, start=1)
        if body.strip()
    ]


def load_policy_chunks(directory: Path = DEFAULT_POLICY_DIRECTORY) -> list[PolicyChunk]:
    chunks: list[PolicyChunk] = []
    for path in sorted(directory.glob("*.md")):
        chunks.extend(chunk_document(path.stem, path.read_text(encoding="utf-8")))
    return chunks


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """Embeds text through an OpenAI-compatible embeddings API."""

    def __init__(self, client: OpenAI, model: str) -> None:
        self._client = client
        self._model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.embeddings.create(model=self._model, input=texts)
        except OpenAIError as error:
            raise PolicyError(f"Embedding call failed: {describe_llm_error(error)}") from error
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norms = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norms if norms else 0.0


@dataclass(frozen=True)
class ScoredChunk:
    chunk: PolicyChunk
    score: float


class PolicyIndex:
    """An in-memory vector index. The documents are embedded once, on first use.

    A few dozen sections do not need a vector database. At larger scale the same interface
    would sit in front of pgvector or a dedicated store.
    """

    def __init__(self, chunks: list[PolicyChunk], embedder: Embedder) -> None:
        self._chunks = chunks
        self._embedder = embedder
        self._vectors: list[list[float]] | None = None
        self._lock = threading.Lock()

    def search(self, question: str, limit: int = PASSAGES_PER_QUESTION) -> list[ScoredChunk]:
        if not self._chunks:
            raise PolicyError("No policy documents are loaded.")
        vectors = self._chunk_vectors()
        (question_vector,) = self._embedder.embed([question])
        scored = [
            ScoredChunk(chunk, cosine_similarity(question_vector, vector))
            for chunk, vector in zip(self._chunks, vectors, strict=True)
        ]
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]

    def _chunk_vectors(self) -> list[list[float]]:
        with self._lock:
            if self._vectors is None:
                # The title and heading are embedded with the body, so a section is found by its topic too.
                texts = [f"{chunk.document}: {chunk.section}\n{chunk.text}" for chunk in self._chunks]
                self._vectors = self._embedder.embed(texts)
                logger.info("policy_index_built chunks=%d", len(self._chunks))
            return self._vectors


class PolicyAnswer(BaseModel):
    """What the LLM must return when answering from passages."""

    found: bool = Field(description="True only if the passages answer the question.")
    answer: str = Field(description="The answer in plain language, or an empty string if not found.")
    cited_ids: list[str] = Field(description="Ids of the passages the answer relies on.")


class AnswerWriter(Protocol):
    def write(self, question: str, passages: list[PolicyChunk]) -> PolicyAnswer: ...


class LLMAnswerWriter:
    def __init__(self, client: OpenAI, model: str) -> None:
        self._client = client
        self._model = model

    def write(self, question: str, passages: list[PolicyChunk]) -> PolicyAnswer:
        try:
            completion = self._client.chat.completions.parse(
                model=self._model,
                messages=build_policy_answer_messages(question, [(p.id, p.citation, p.text) for p in passages]),
                response_format=PolicyAnswer,
                temperature=0,
                max_tokens=MAX_ANSWER_TOKENS,
            )
        except (OpenAIError, ValidationError) as error:
            raise PolicyError(f"LLM call failed: {describe_llm_error(error)}") from error
        answer = completion.choices[0].message.parsed
        if answer is None:
            raise PolicyError("LLM returned no structured output.")
        return answer


@dataclass(frozen=True)
class PolicyReply:
    found: bool
    answer: str
    sources: list[PolicyChunk]
    retrieved: list[ScoredChunk]


class PolicyGuide:
    """Retrieve, answer, then verify: an answer is shown only if it cites passages that were actually retrieved."""

    def __init__(self, index: PolicyIndex, writer: AnswerWriter) -> None:
        self._index = index
        self._writer = writer

    def answer(self, question: str) -> PolicyReply:
        retrieved = self._index.search(question)
        draft = self._writer.write(question, [item.chunk for item in retrieved])
        sources = [item.chunk for item in retrieved if item.chunk.id in draft.cited_ids]
        if not draft.found or not draft.answer.strip() or not sources:
            return PolicyReply(found=False, answer="", sources=[], retrieved=retrieved)
        return PolicyReply(found=True, answer=draft.answer.strip(), sources=sources, retrieved=retrieved)


def build_policy_guide(client: OpenAI, chat_model: str, embedding_model: str) -> PolicyGuide:
    index = PolicyIndex(load_policy_chunks(), OpenAIEmbedder(client, embedding_model))
    return PolicyGuide(index, LLMAnswerWriter(client, chat_model))
