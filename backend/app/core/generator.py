"""LLM providers behind one interface.

MockProvider exists so the entire test suite runs offline with no API key and
no cost. That is a deliberate deliverable, not a testing convenience: a
reviewer can clone the repo and run every test immediately.
"""
from __future__ import annotations

import json
import re
from typing import Protocol

from app.config import settings
from app.core.prompts import SYSTEM_PROMPT, build_user_prompt
from app.core.schemas import Confidence, LLMAnswer, LLMCitation, RetrievedChunk

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class GenerationError(RuntimeError):
    pass


def parse_llm_json(raw: str) -> LLMAnswer:
    """Parse the model's response into the untrusted LLMAnswer contract.

    Models wrap JSON in markdown fences despite instructions not to, and
    sometimes add a sentence before it. Strip both rather than failing — but
    never repair the JSON itself: malformed structure means the model did not
    follow the contract, and inventing a fix would hide that.
    """
    text = raw.strip()

    fenced = _JSON_FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise GenerationError(f"no JSON object in response: {raw[:200]!r}")

    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise GenerationError(f"malformed JSON: {exc}") from exc

    try:
        return LLMAnswer(**data)
    except Exception as exc:                          # noqa: BLE001
        raise GenerationError(f"response did not match contract: {exc}") from exc


class LLMProvider(Protocol):
    name: str
    model: str

    def generate(self, question: str, chunks: list[RetrievedChunk]) -> LLMAnswer: ...


class MockProvider:
    """Deterministic provider for tests and offline demos.

    Builds an answer from the top chunk by quoting its first sentence verbatim,
    so the Step 9 verification layer has something real to verify. Abstains
    when no chunks were retrieved — mirroring correct behaviour rather than
    fabricating one.
    """

    name = "mock"
    model = "mock-deterministic"

    def generate(self, question: str, chunks: list[RetrievedChunk]) -> LLMAnswer:
        if not chunks:
            return LLMAnswer(
                answer="The provided documents do not contain enough information "
                       "to answer this question.",
                citations=[],
                insufficient_context=True,
                missing_information=f"No document in the corpus addresses: {question}",
                confidence=Confidence.LOW,
            )

        top = chunks[0]
        sentence = re.split(r"(?<=[.!?])\s", top.text.strip())[0]
        quote = " ".join(sentence.split()[:20])

        return LLMAnswer(
            answer=f"Based on {top.meta.doc_title}: {sentence}",
            citations=[LLMCitation(chunk_id=top.chunk_id, quote=quote)],
            insufficient_context=False,
            missing_information=None,
            confidence=Confidence.MEDIUM,
        )


class AnthropicProvider:
    """Claude via the Messages API."""

    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-5"):
        import anthropic
        key = api_key or settings.anthropic_api_key
        if not key:
            raise GenerationError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic(api_key=key)
        self.model = model

    def generate(self, question: str, chunks: list[RetrievedChunk]) -> LLMAnswer:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=1500,
            temperature=0,                    # grounded QA wants determinism
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(question, chunks)}],
        )
        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        answer = parse_llm_json(text)
        answer.input_tokens = response.usage.input_tokens
        answer.output_tokens = response.usage.output_tokens
        return answer


def get_provider(name: str | None = None) -> LLMProvider:
    """Resolve the configured provider. Defaults to mock so the app runs
    with no API key at all."""
    selected = (name or settings.llm_provider).lower()

    if selected == "mock":
        return MockProvider()
    if selected == "anthropic":
        return AnthropicProvider()

    raise GenerationError(f"unknown provider: {selected!r}")