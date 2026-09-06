"""Transcript to written article.

Only offered for timed sources. Rewriting an article as an article is not
repurposing, it is a plagiarism surface with a different font.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..extract import render_claims
from ..llm import LLM, load_prompt
from ..models import Artifact, ClaimSet, Document

requires_timed = True

# Length follows the material. A two minute video holds maybe nine claims and no
# amount of prompting turns that into 800 honest words: asking for them just invites
# padding, which is the failure mode next door to fabrication. The target and the
# check are computed from the same number so they cannot disagree.
WORDS_PER_CLAIM = 50
MIN_TARGET = 350
MAX_TARGET = 1100
MIN_SECTIONS = 2


def target_words(claims: ClaimSet) -> int:
    return max(MIN_TARGET, min(MAX_TARGET, WORDS_PER_CLAIM * len(claims.claims)))

_HEADING = re.compile(r"^##\s+\S", re.MULTILINE)
# Phrases that give away that this was transcribed rather than written.
_TRANSCRIPT_TELLS = re.compile(
    r"\b(in this video|in the video|the speaker|the narrator|this talk|this episode|"
    r"as (?:we saw|mentioned) earlier|welcome back)\b",
    re.IGNORECASE,
)


class _Response(BaseModel):
    title: str = ""
    body: str
    claim_ids: list[str] = Field(default_factory=list)


def build(document: Document, claims: ClaimSet, llm: LLM, note: str = "") -> Artifact:
    target = target_words(claims)
    low, high = int(target * 0.6), int(target * 1.5)
    prompt = load_prompt(
        "article",
        title=document.title,
        claims=render_claims(claims, document),
        low=low,
        high=high,
    )
    if note:
        prompt = f"{prompt}\n\n---\n{note}"
    response = llm.structured(prompt, _Response)

    title = response.title.strip()
    body = response.body.strip()
    words = len(body.split())
    sections = len(_HEADING.findall(body))
    tells = sorted({m.group(0).lower() for m in _TRANSCRIPT_TELLS.finditer(body)})

    violations = []
    if not low <= words <= high:
        violations.append(f"{words} words, wanted {low} to {high}")
    if sections < MIN_SECTIONS:
        violations.append(f"{sections} sections, wanted at least {MIN_SECTIONS}")
    if tells:
        violations.append("reads as a transcript: " + ", ".join(tells))

    meta = {"words": words, "sections": sections, "title": title, "target_words": target}
    if violations:
        meta["format_violation"] = "; ".join(violations)

    composed = f"# {title}\n\n{body}" if title else body
    return Artifact(target="article", body=composed, claim_ids=response.claim_ids, meta=meta)
