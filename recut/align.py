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

import math
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
# A claim has to explain a real share of what the sentence says, not just overlap
# with it. At 0.18 a sentence about merchant names still anchored to a claim about
# dates on the strength of "bank", "statement" and "payment" alone. This is
# calibrated on few examples and errs high on purpose: for a tool whose claim is
# provenance, showing no anchor is a smaller failure than showing a wrong one.
MIN_SCORE = 0.35
# The winner must beat the runner-up by this much. Two claims that score alike
# means we cannot tell which one a sentence came from, and saying "this line came
# from that paragraph" when it is a coin flip is worse than saying nothing.
MIN_MARGIN = 1.35

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'“])")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’.%-]*")


def _singular(word: str) -> str:
    """Crude plural folding, which turns out to be the whole ball game.

    A generator paraphrases, and paraphrasing pluralises: the claim said
    "merchant names are typed by payment processors" and the output said
    "the merchant name is typed by a payment processor". With exact tokens those
    share almost nothing, and the sentence anchored to a claim about dates purely
    on "bank", "statement" and "payment". No stemmer, because a real one drags in
    a dependency and its own surprises for two rules that fix the actual problem.
    """
    if len(word) <= 3 or word.endswith("ss"):
        return word
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    # "-es" is a distinct plural only after s, x, z, ch or sh. Everywhere else it
    # is plain "-s" on a word that already ended in "e", and stripping two
    # characters turns "names" into "nam", which matches nothing.
    if len(word) > 4 and word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def tokens(text: str) -> set[str]:
    words = {w.lower().strip(".'’") for w in _WORD.findall(text)}
    return {_singular(w) for w in words if w and w not in _STOP and len(w) > 1}


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


def weights(claims: list[Claim]) -> dict[str, float]:
    """How much each word distinguishes one claim from another.

    A word in most of the claims tells you nothing about which one a sentence
    came from. "bank", "statement" and "payment" are in nearly every claim about
    bank statements, and counting them equally with "merchant" is what made a
    sentence about merchant names match a claim about dates.
    """
    total = len(claims) or 1
    frequency: dict[str, int] = {}
    for claim in claims:
        for token in tokens(claim.text):
            frequency[token] = frequency.get(token, 0) + 1
    return {token: math.log(1 + total / count) for token, count in frequency.items()}


def score(sentence: str, claim: Claim, weight: dict[str, float] | None = None) -> float:
    """Weighted overlap, with a nudge for a shared figure or an exact quote."""
    a, b = tokens(sentence), tokens(claim.text)
    if not a or not b:
        return 0.0
    shared = a & b
    if len(shared) < MIN_SHARED:
        return 0.0

    if weight:
        # A word in the sentence that appears in no claim at all is the rarest
        # case there is, so it gets the weight of a word seen once. It has to be
        # counted in the denominator: those words are the evidence that this
        # sentence is about something the claim does not cover. Leaving them out
        # let a sentence about merchant names score a perfect 1.0 against a claim
        # about dates, purely because every word they did share was generic.
        default = max(weight.values())
        got = sum(weight.get(w, default) for w in shared)
        want = sum(weight.get(w, default) for w in a)
        base = got / want if want else 0.0
    else:
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
    weight = weights(cited)
    out: list[dict] = []

    for start, end, sentence in sentences_of(artifact.body):
        ranked = sorted(
            ((score(sentence, c, weight), c) for c in cited), key=lambda p: -p[0]
        )
        best_score, best = ranked[0] if ranked else (0.0, None)
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        # Ambiguity is not a match. If the second-best claim is nearly as good,
        # we do not know which paragraph this line came from.
        if runner_up and best_score < runner_up * MIN_MARGIN:
            best, best_score = None, best_score
            ambiguous = True
        else:
            ambiguous = False

        entry: dict = {
            "start": start,
            "end": end,
            "text": sentence,
            "claim_id": None,
            "claim": None,
            "confidence": round(best_score, 3),
            "segments": [],
        }
        entry["ambiguous"] = ambiguous
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
