from __future__ import annotations

from pydantic import BaseModel, Field

from ..extract import render_claims
from ..llm import LLM, load_prompt
from ..models import Artifact, ClaimSet, Document

MIN_WORDS = 90
MAX_WORDS = 260


class _Response(BaseModel):
    body: str
    claim_ids: list[str] = Field(default_factory=list)


def build(document: Document, claims: ClaimSet, llm: LLM, note: str = "") -> Artifact:
    prompt = load_prompt("linkedin", title=document.title, claims=render_claims(claims, document))
    if note:
        prompt = f"{prompt}\n\n---\n{note}"
    response = llm.structured(prompt, _Response)

    body = response.body.strip()
    words = len(body.split())
    meta = {"words": words}
    if not MIN_WORDS <= words <= MAX_WORDS:
        meta["format_violation"] = f"{words} words, wanted {MIN_WORDS} to {MAX_WORDS}"

    return Artifact(target="linkedin", body=body, claim_ids=response.claim_ids, meta=meta)
