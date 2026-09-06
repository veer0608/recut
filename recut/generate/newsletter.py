from __future__ import annotations

from pydantic import BaseModel, Field

from ..extract import render_claims
from ..llm import LLM, load_prompt
from ..models import Artifact, ClaimSet, Document

MIN_WORDS = 260
MAX_WORDS = 700
SUBJECT_LIMIT = 60
PREVIEW_LIMIT = 90


class _Response(BaseModel):
    subject: str = ""
    preview: str = ""
    body: str
    claim_ids: list[str] = Field(default_factory=list)


def build(document: Document, claims: ClaimSet, llm: LLM, note: str = "") -> Artifact:
    prompt = load_prompt("newsletter", title=document.title, claims=render_claims(claims, document))
    if note:
        prompt = f"{prompt}\n\n---\n{note}"
    response = llm.structured(prompt, _Response)

    subject = response.subject.strip()
    preview = response.preview.strip()
    body = response.body.strip()
    words = len(body.split())

    violations = []
    if not MIN_WORDS <= words <= MAX_WORDS:
        violations.append(f"{words} words, wanted {MIN_WORDS} to {MAX_WORDS}")
    if len(subject) > SUBJECT_LIMIT:
        violations.append(f"subject is {len(subject)} chars, limit {SUBJECT_LIMIT}")
    if len(preview) > PREVIEW_LIMIT:
        violations.append(f"preview is {len(preview)} chars, limit {PREVIEW_LIMIT}")

    meta = {"words": words, "subject": subject, "preview": preview}
    if violations:
        meta["format_violation"] = "; ".join(violations)

    # The subject and preview go into the verified body too. They are the two lines
    # most likely to carry an invented number, because they are the two lines under
    # the most pressure to be interesting.
    composed = f"Subject: {subject}\nPreview: {preview}\n\n{body}"
    return Artifact(target="newsletter", body=composed, claim_ids=response.claim_ids, meta=meta)
