"""The expensive half of the eval: is each sentence of output supported by the source?

This never runs on the request path. The deterministic verifier catches fabricated
numbers, quotes and names, which is most of what goes wrong, but it cannot catch a
sentence built entirely from real words that the source does not actually claim.
That is what this measures, and it costs a model call per output.

Two guards against the obvious objection that a model is grading a model:

- The judge is pinned to a different model than the one that wrote the output.
- Its own accuracy is measured on the planted injections from inject.py, where the
  answer is known. A judge that cannot spot a fabrication it was handed has no
  standing to report a rate on anything else.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from recut.extract import render_source
from recut.llm import LLM
from recut.models import Document

# Deliberately not the generation model. Sharing one would let a model ratify its
# own habits, which is the failure this whole file exists to rule out.
#
# The judge is on a different *provider* from the generators, not a different
# ladder on the same one. The earlier arrangement pinned Gemini models the
# generators did not start with, but the generators could still fall through to
# them, so the guarantee held by luck rather than by construction. The README
# said as much under known limits: no-overlap was designed for and not proven.
# Separating by provider is the version that cannot fail that way, and it also
# stops one exhausted quota taking out both halves of the eval at once, which it
# did on two consecutive days.
#
# The cost is Groq's, and it is real: the binding limit is tokens per day and it
# appears in no response header, so a judged run can stop without warning. It
# checkpoints per source, so that costs the source it was on.
JUDGE_PROVIDER = "groq"
JUDGE_GROQ_MODEL = "openai/gpt-oss-120b"
JUDGE_MODELS = (JUDGE_GROQ_MODEL,)


def judge_client(**kwargs) -> LLM:
    """A judge that cannot reach the generators' provider, by construction."""
    return LLM(use_gemini=False, groq_model=JUDGE_GROQ_MODEL, **kwargs)

PROMPT = """\
You are checking whether a piece of writing is supported by its source.

Below is the SOURCE, then a numbered list of SENTENCES taken from something written
about it. For each sentence, decide one thing only: does the source support it?

- `supported`: the source states this, or states something that plainly entails it.
  Rewording is fine. Compression is fine. Reasonable paraphrase is fine.
- `unsupported`: the source does not state this and does not entail it. This covers
  invented figures, invented names, invented quotes, and claims that go further than
  the source went, including a hedge dropped or a "some" turned into "most".

Judge only against the source. Do not use anything you know about the subject: a
sentence can be true in the world and still unsupported here, and unsupported is the
answer we need. Do not reward fluency. Do not give the benefit of the doubt.

Sentences that assert nothing checkable (a question, a call to action, a transition
like "here is what that means") are `not_a_claim` and are excluded from the score.

SOURCE
---
{source}
---

SENTENCES
{sentences}

Reply with a single JSON object and nothing else:

{{"verdicts": [{{"n": 1, "verdict": "supported", "why": "short reason"}}]}}
"""

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'“])")


class _Verdict(BaseModel):
    n: int
    verdict: str = "unsupported"
    why: str = ""


class _Response(BaseModel):
    verdicts: list[_Verdict] = Field(default_factory=list)


def sentences_of(body: str) -> list[str]:
    """Split an output into judgeable sentences.

    Markdown headings, subject lines and hashtag rows are dropped: they are labels,
    not assertions, and asking a judge to rule on "## Why this matters" only adds
    noise to the denominator.
    """
    out: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("Preview:"):
            continue
        line = re.sub(r"^Subject:\s*", "", line)
        if line.startswith("#") or re.fullmatch(r"(#\w+\s*)+", line):
            continue
        for piece in _SENTENCE_SPLIT.split(line):
            piece = piece.strip()
            if len(piece.split()) >= 4:
                out.append(piece)
    return out


def judge(body: str, document: Document, llm: LLM) -> dict:
    """Per-sentence verdicts plus the unsupported rate for this one output."""
    sentences = sentences_of(body)
    if not sentences:
        return {"sentences": 0, "claims": 0, "unsupported": 0, "rate": None, "verdicts": []}

    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
    prompt = PROMPT.format(source=render_source(document.segments), sentences=numbered)
    response = llm.structured(prompt, _Response)

    by_index = {v.n: v for v in response.verdicts}
    verdicts = []
    claims = 0
    unsupported = 0
    for index, sentence in enumerate(sentences, 1):
        verdict = by_index.get(index)
        # A sentence the judge silently skipped is not quietly counted as fine.
        label = (verdict.verdict if verdict else "unjudged").strip().lower()
        if label in ("supported", "unsupported"):
            claims += 1
            if label == "unsupported":
                unsupported += 1
        verdicts.append(
            {
                "n": index,
                "sentence": sentence,
                "verdict": label,
                "why": verdict.why if verdict else "judge returned no verdict",
            }
        )

    return {
        "sentences": len(sentences),
        "claims": claims,
        "unsupported": unsupported,
        "rate": (unsupported / claims) if claims else None,
        "unjudged": sum(1 for v in verdicts if v["verdict"] == "unjudged"),
        "verdicts": verdicts,
    }
