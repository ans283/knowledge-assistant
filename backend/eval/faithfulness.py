"""LLM-as-judge faithfulness scoring.

The judge MUST be a different model family than the generator. A model grading
its own output exhibits self-preference bias — it systematically rates its own
phrasing as more faithful than an equivalent answer it did not write. Using
Claude to generate and Gemini to judge (or vice versa) removes that.

Skipped entirely when no judge key is configured, so the rest of the harness
still runs offline.
"""
from __future__ import annotations

import json
import re

from app.core.schemas import QueryResponse

JUDGE_PROMPT = """You are grading whether an ANSWER is fully supported by SOURCES.

Do not judge whether the answer is correct in the real world. Judge only whether
every factual claim in it appears in the sources.

SOURCES:
{sources}

ANSWER:
{answer}

Reply with JSON only:
{{"supported_claims": <int>, "total_claims": <int>, "unsupported": ["..."],
  "verdict": "faithful" | "partially_faithful" | "unfaithful"}}"""


def judge_available() -> bool:
    from app.config import settings
    return bool(settings.gemini_api_key)


def judge_response(response: QueryResponse) -> dict | None:
    """Score one answer. Returns None when no judge is configured."""
    if not judge_available() or response.insufficient_context:
        return None

    from app.config import settings
    from google import genai

    sources = "\n\n".join(
        f"[{c.chunk_id}] {c.text}" for c in response.retrieved
    )
    prompt = JUDGE_PROMPT.format(sources=sources, answer=response.answer)

    client = genai.Client(api_key=settings.gemini_api_key)
    result = client.models.generate_content(
        model="gemini-2.5-flash", contents=prompt
    )

    text = result.text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None