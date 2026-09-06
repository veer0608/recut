"""One thin client over two free-tier providers.

Gemini is primary because its limits are visible: a 503 means retry, a 429 names
the quota. Groq is the fallback and it lies by omission, so it is used only when
Gemini is unavailable. Its binding limit is tokens per day and appears in no
response header, which means a small probe succeeding proves nothing.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Never use the -latest aliases. They silently repoint to whatever is newest, and
# newest carries the smallest free-tier allowance: gemini-flash-latest resolved to
# gemini-3.8-flash at 20 requests per day, which one run of this pipeline exhausts.
# Pinned ids in descending order of daily allowance. Free-tier quota is per model,
# so a 429 on one says nothing about the next.
GEMINI_MODELS = (
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3-flash-preview",
)
GROQ_MODEL = "openai/gpt-oss-120b"

PROMPTS = Path(__file__).parent / "prompts"


class LLMError(RuntimeError):
    pass


class Fatal(LLMError):
    """This model will not answer this prompt. Retrying it wastes wall-clock time."""


# Statuses where waiting changes nothing: the request itself is the problem, or
# the quota is spent for the day. Move to the next model instead of sleeping.
NO_RETRY = {400, 401, 403, 404, 413, 422, 429}


class Budget(BaseModel):
    """What a run has spent. Checkpointed so a token cliff loses one item, not all."""

    calls: int = 0
    provider_calls: dict[str, int] = {}

    def record(self, provider: str) -> None:
        self.calls += 1
        self.provider_calls[provider] = self.provider_calls.get(provider, 0) + 1


def load_prompt(name: str, **fields: object) -> str:
    """Prompts live on disk as markdown so they stay diffable.

    `{{faithfulness}}` pulls in `_faithfulness.md`. It is shared rather than copied
    into each generator because it is the one instruction that must never drift: a
    stale copy in one prompt is a target quietly held to a weaker standard.
    """
    template = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    if "{{faithfulness}}" in template:
        shared = (PROMPTS / "_faithfulness.md").read_text(encoding="utf-8")
        template = template.replace("{{faithfulness}}", shared.strip())
    for key, value in fields.items():
        template = template.replace("{{" + key + "}}", str(value))
    return template


def _extract_json(text: str) -> str:
    """Models fence their JSON about a third of the time. Take the object either way."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError(f"no JSON object in response: {text[:200]!r}")
    return text[start : end + 1]


def _raise_for(response: httpx.Response, provider: str) -> None:
    """Turn a bad status into either Fatal (move on) or a retryable error.

    429 is the one that needs care, because the same status covers two completely
    different things: a per-minute burst cap that clears in seconds, and a per-day
    ceiling that does not clear until tomorrow. Treating every 429 as fatal
    abandons a run over a blip; treating every one as retryable sleeps through a
    day-long wall. So branch on what the body actually says, never on the status
    alone. Gemini names the quota in a `quotaId` like
    `GenerateRequestsPerDayPerProjectPerModel-FreeTier`. Groq's tier limit is per
    minute and it says so in prose.
    """
    if response.status_code < 400:
        return
    body = response.text
    detail = body.strip().replace("\n", " ")[:220]
    message = f"{provider} {response.status_code}: {detail}"

    if response.status_code == 429:
        per_day = re.search(r"PerDay|per day|requests per day", body, re.IGNORECASE)
        per_minute = re.search(r"PerMinute|PerSecond|per minute|TPM|RPM", body, re.IGNORECASE)
        if per_minute and not per_day:
            raise httpx.HTTPStatusError(message, request=response.request, response=response)
        raise Fatal(message)

    if response.status_code in NO_RETRY:
        raise Fatal(message)
    raise httpx.HTTPStatusError(message, request=response.request, response=response)


class LLM:
    def __init__(
        self,
        gemini_key: str | None = None,
        groq_key: str | None = None,
        timeout: float = 120.0,
        # gemini-flash-latest returns 503 in bursts that last a minute or more.
        # Eight attempts with capped backoff rides out a burst; five did not.
        max_retries: int = 8,
        # The eval's judge pins a different ladder than the generators use, so a
        # model is never grading its own output.
        gemini_models: tuple[str, ...] = GEMINI_MODELS,
        use_groq: bool = True,
    ) -> None:
        self.gemini_key = gemini_key or os.getenv("GEMINI_API_KEY") or None
        self.groq_key = groq_key or os.getenv("GROQ_API_KEY") or None
        if not (self.gemini_key or self.groq_key):
            raise LLMError("no GEMINI_API_KEY or GROQ_API_KEY found")
        self.timeout = timeout
        self.max_retries = max_retries
        self.gemini_models = gemini_models
        self.use_groq = use_groq
        self.budget = Budget()

    # ------------------------------------------------------------- transports

    def _gemini(self, client: httpx.Client, model: str, prompt: str, as_json: bool) -> str:
        config: dict = {"temperature": 0.4}
        if as_json:
            config["responseMimeType"] = "application/json"
        response = client.post(
            GEMINI_URL.format(model=model),
            headers={"x-goog-api-key": self.gemini_key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config},
        )
        _raise_for(response, "gemini")
        payload = response.json()
        candidates = payload.get("candidates") or []
        if not candidates:
            reason = payload.get("promptFeedback", {}).get("blockReason", "no candidates")
            raise Fatal(f"gemini returned nothing ({reason})")
        parts = candidates[0].get("content", {}).get("parts") or []
        # A thinking model can spend the whole budget before emitting a part, in
        # which case finishReason is the only thing that explains the silence.
        text = "".join(part.get("text", "") for part in parts)
        if not text.strip():
            raise Fatal(f"gemini produced no text (finishReason={candidates[0].get('finishReason')})")
        return text

    def _groq(self, client: httpx.Client, prompt: str, as_json: bool) -> str:
        # Groq's json_object mode rejects the whole request when a reasoning model
        # spends its budget before emitting the object, and returns an empty
        # failed_generation that says nothing. Asking in the prompt and parsing the
        # object out ourselves fails softer and costs nothing.
        body: dict = {
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
        }
        if as_json:
            body["messages"][0]["content"] += (
                "\n\nReply with the JSON object only. No prose before it, "
                "no code fence around it."
            )
        response = client.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {self.groq_key}"},
            json=body,
        )
        _raise_for(response, "groq")
        return response.json()["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------- api

    def text(self, prompt: str, as_json: bool = False) -> str:
        """One completion, retried across models and then across providers."""
        attempts: list[tuple[str, str]] = []
        if self.gemini_key:
            attempts += [("gemini", model) for model in self.gemini_models]
        if self.groq_key and self.use_groq:
            attempts.append(("groq", GROQ_MODEL))

        failures: list[str] = []
        with httpx.Client(timeout=self.timeout) as client:
            for provider, model in attempts:
                for attempt in range(self.max_retries):
                    try:
                        if provider == "gemini":
                            out = self._gemini(client, model, prompt, as_json)
                        else:
                            out = self._groq(client, prompt, as_json)
                        self.budget.record(provider)
                        return out
                    except Fatal as exc:
                        # Quota walls and oversized requests do not heal by waiting.
                        failures.append(str(exc))
                        break
                    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                        # 503 is transient overload on gemini-flash-latest and it
                        # clears in seconds. This retry is not optional.
                        if attempt == self.max_retries - 1:
                            failures.append(f"{model}: {exc}")
                        time.sleep(min(2**attempt, 8) + random.random())
        raise LLMError("every provider failed:\n  " + "\n  ".join(failures))

    def structured(self, prompt: str, model_type: type[T]) -> T:
        """A completion validated into a pydantic model, with one repair attempt."""
        raw = self.text(prompt, as_json=True)
        try:
            return model_type.model_validate_json(_extract_json(raw))
        except (ValidationError, LLMError) as exc:
            repair = (
                f"{prompt}\n\n---\nYour previous reply could not be parsed:\n"
                f"{str(exc)[:800]}\n\nReply again with valid JSON only, no prose, "
                "no code fence."
            )
            raw = self.text(repair, as_json=True)
            return model_type.model_validate_json(_extract_json(raw))
