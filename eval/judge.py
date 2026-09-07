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

from recut.extract import render_source, windows
from recut.llm import LLM
from recut.models import Document

# Deliberately not the generation model. Sharing one would let a model ratify its
# own habits, which is the failure this whole file exists to rule out.
#
# What is guaranteed here is that the judge and the generators never share a
# *model*, and both sides are pinned by name so that holds by construction.
#
# Full provider separation was tried and reverted the same day. Putting the
# judge on Groq and pinning generators to Gemini alone was a stronger claim, and
# it cost the generators their fallback: when Gemini's daily budget went, a
# fresh run failed 15 of 15 sources at extraction and the judge never ran. With
# two providers, both halves cannot be single-provider and independently
# fault-tolerant at the same time. The guarantee that matters for the number is
# that nothing grades its own output, and an explicit model pin gives that
# without making one quota fatal.
#
# So the generators walk Gemini first and fall back to a different Groq model,
# and run_eval refuses to start if the two are ever configured the same.
#
# The cost is Groq's, and it is real: the binding limit is tokens per day and it
# appears in no response header, so a judged run can stop without warning. It
# checkpoints per source, so that costs the source it was on.
JUDGE_PROVIDER = "groq"
JUDGE_GROQ_MODEL = "openai/gpt-oss-120b"
JUDGE_MODELS = (JUDGE_GROQ_MODEL,)


# Groq's documented ceiling for this tier. Held to deliberately rather than
# discovered by 429: windowing turned one large source into three ~3000 token
# requests, which spends a minute's budget in seconds and then pays for it in
# retries. Set below the real limit so an estimate that runs slightly low still
# lands inside it.
JUDGE_TOKENS_PER_MINUTE = 6500


def judge_client(**kwargs) -> LLM:
    """A judge pinned to one model the generators are never given, and paced."""
    kwargs.setdefault("tokens_per_minute", JUDGE_TOKENS_PER_MINUTE)
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

# Gemini took a 35k character source in one request. Groq returns 413 above
# roughly 20k, which is how a judge that had never been windowed came to fail
# the four largest sources in the golden set and nothing else. Sources are
# capped near 40k, so no source needs more than four passes at this size.
JUDGE_WINDOW_CHARS = 12000


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

    # Support is existential over the source: a sentence is supported if any
    # part of the source supports it. Each window is therefore asked only about
    # the sentences nothing has supported yet, and a `supported` verdict is
    # final. Asking every window about every sentence gives the same answer and
    # costs more.
    #
    # `not_a_claim` is a property of the sentence rather than of the source, so
    # the first window to see a sentence settles it and no later window revisits
    # it. Without that rule the same sentence could be a claim against one
    # window and not against the next, and the denominator would depend on where
    # the boundaries happened to fall.
    panes = windows(document, JUDGE_WINDOW_CHARS)
    by_index: dict[int, _Verdict] = {}
    open_indexes = list(range(1, len(sentences) + 1))

    for pane in panes:
        if not open_indexes:
            break
        numbered = "\n".join(f"{i}. {sentences[i - 1]}" for i in open_indexes)
        prompt = PROMPT.format(source=render_source(pane), sentences=numbered)
        response = llm.structured(prompt, _Response)

        for verdict in response.verdicts:
            if verdict.n not in open_indexes:
                continue
            previous = by_index.get(verdict.n)
            # Never downgrade. One window saying "supported" outranks another
            # saying it could not find it, which is the point of windowing.
            if previous is None or previous.verdict.strip().lower() != "supported":
                by_index[verdict.n] = verdict

        open_indexes = [
            index
            for index in open_indexes
            if (by_index[index].verdict.strip().lower() if index in by_index else "")
            not in ("supported", "not_a_claim")
        ]
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
        "windows": len(panes),
        "claims": claims,
        "unsupported": unsupported,
        "rate": (unsupported / claims) if claims else None,
        "unjudged": sum(1 for v in verdicts if v["verdict"] == "unjudged"),
        "verdicts": verdicts,
    }
