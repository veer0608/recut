"""Map each sentence of an output back to the claim that supports it.

The generator declares which claims it used, but only for the artifact as a whole.
The interface needs it per sentence: hover a line, light up the paragraph of the
source it came from. Asking the model to emit that mapping would add a failure mode
to the one part of the pipeline that currently has none, so it is computed here
instead, deterministically and for free.

Two rules keep this honest:

- A sentence is only ever matched against claims the artifact already cited. This
  picks among anchors that were declared; it never invents one.
- Below a similarity floor it returns nothing at all. Showing a reader a source span
  that does not actually support the sentence they are reading would be worse than
  showing them no span, because they would believe it.
"""

from __future__ import annotations

import re

from .models import Artifact, Claim, ClaimSet, Document

# Words that match everything and therefore distinguish nothing.
_STOP = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at", "for",
    "with", "by", "from", "as", "is", "are", "was", "were", "be", "been", "being",
    "it", "its", "this", "that", "these", "those", "they", "them", "their", "there",
    "you", "your", "we", "our", "i", "he", "she", "his", "her", "not", "no", "so",
    "than", "then", "when", "while", "which", "who", "what", "how", "why", "can",
    "will", "would", "should", "could", "may", "might", "must", "do", "does", "did",
    "has", "have", "had", "into", "over", "under", "about", "more", "most", "some",
    "any", "all", "each", "one", "two", "up", "out", "off", "just", "only", "also",
}

# A sentence sharing fewer than this many distinctive words with a claim is not
# being supported by it, whatever the arithmetic says.
MIN_SHARED = 2
MIN_SCORE = 0.18

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'“])")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’.%-]*")


def tokens(text: str) -> set[str]:
    words = {w.lower().strip(".'’") for w in _WORD.findall(text)}
    return {w for w in words if w and w not in _STOP and len(w) > 1}


def sentences_of(body: str) -> list[tuple[int, int, str]]:
    """Every sentence as (start, end, text), offset into the body it came from.

    Offsets rather than a plain list, because the interface highlights a span in
    text the reader can see, and recomputing where a sentence sits after the fact
    is how off-by-one highlighting bugs happen.
    """
    out: list[tuple[int, int, str]] = []
    for line_match in re.finditer(r"[^\n]+", body):
        line, base = line_match.group(0), line_match.start()
        cursor = 0
        for piece in _SENTENCE_SPLIT.split(line):
            if not piece:
                continue
            start = line.index(piece, cursor)
            cursor = start + len(piece)
            stripped = piece.strip()
            if stripped:
                lead = start + (len(piece) - len(piece.lstrip()))
                out.append((base + lead, base + lead + len(stripped), stripped))
    return out


def score(sentence: str, claim: Claim) -> float:
    """Overlap of distinctive words, with a nudge for a shared exact figure."""
    a, b = tokens(sentence), tokens(claim.text)
    if not a or not b:
        return 0.0
    shared = a & b
    if len(shared) < MIN_SHARED:
        return 0.0
    base = len(shared) / min(len(a), len(b))
    # A number both sides agree on is far stronger evidence than a shared noun.
    if any(re.search(r"\d", w) for w in shared):
        base += 0.15
    if claim.verbatim and claim.verbatim.strip().lower() in sentence.lower():
        base += 0.35
    return min(base, 1.0)


def align(artifact: Artifact, claims: ClaimSet, document: Document) -> list[dict]:
    """One entry per sentence, carrying its claim and source spans, or nothing."""
    cited = [c for cid in artifact.claim_ids if (c := claims.claim(cid))]
    out: list[dict] = []

    for start, end, sentence in sentences_of(artifact.body):
        best: Claim | None = None
        best_score = 0.0
        for claim in cited:
            value = score(sentence, claim)
            if value > best_score:
                best, best_score = claim, value

        entry: dict = {
            "start": start,
            "end": end,
            "text": sentence,
            "claim_id": None,
            "claim": None,
            "confidence": round(best_score, 3),
            "segments": [],
        }
        if best is not None and best_score >= MIN_SCORE:
            entry["claim_id"] = best.id
            entry["claim"] = best.text
            entry["segments"] = [
                {
                    "id": segment.id,
                    "text": segment.text,
                    "timecode": segment.timecode,
                    "char_start": segment.char_start,
                    "char_end": segment.char_end,
                }
                for seg_id in best.segment_ids
                if (segment := document.segment(seg_id))
            ]
        out.append(entry)
    return out


def coverage(aligned: list[dict]) -> float:
    """Share of sentences the interface can actually show a source for.

    Worth surfacing rather than hiding: a page that silently shows no anchor for
    half its lines looks broken, and a reader deserves to know the difference
    between "unsupported" and "we could not tell you which claim this came from".
    """
    if not aligned:
        return 0.0
    return sum(1 for entry in aligned if entry["claim_id"]) / len(aligned)
