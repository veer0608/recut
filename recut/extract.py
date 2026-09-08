"""The one expensive pass: source text to an anchored claim inventory.

Everything the product sells hangs off this. Outputs are never generated from the
raw source, only from the claims produced here, so a claim without a segment id
is dropped rather than carried forward. That is the whole design in one rule.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from .llm import LLM, load_prompt
from .models import Claim, ClaimSet, Document, Segment


class _RawClaim(BaseModel):
    id: str = ""
    text: str
    kind: str = "fact"
    segment_ids: list[str] = Field(default_factory=list)
    verbatim: str | None = None


class _RawExtract(BaseModel):
    thesis: str = ""
    claims: list[_RawClaim] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    hook_candidates: list[str] = Field(default_factory=list)
    voice_samples: list[str] = Field(default_factory=list)


_KINDS = {"fact", "stat", "quote", "opinion", "narrative"}


# Groq's free tier caps a request at 8000 tokens per minute, and long inputs make
# every model quietly drop claims from the middle. One window is roughly 1500
# tokens of source, which fits both constraints with room for the instructions.
WINDOW_CHARS = 6_000


def render_source(segments: Sequence[Segment]) -> str:
    """The source as the model sees it: every paragraph tagged with its segment id."""
    blocks = []
    for segment in segments:
        stamp = f" @{segment.timecode}" if segment.timecode else ""
        blocks.append(f"[{segment.id}{stamp}] {segment.text}")
    return "\n\n".join(blocks)


def windows(document: Document, max_chars: int = WINDOW_CHARS) -> list[list[Segment]]:
    """Split on segment boundaries so no claim is ever cut in half."""
    out: list[list[Segment]] = []
    current: list[Segment] = []
    used = 0
    for segment in document.segments:
        if current and used + len(segment.text) > max_chars:
            out.append(current)
            current, used = [], 0
        current.append(segment)
        used += len(segment.text)
    if current:
        out.append(current)
    return out


def _clean(
    raw: _RawExtract, document: Document, offset: int
) -> tuple[list[Claim], int, int]:
    known = {segment.id for segment in document.segments}
    claims: list[Claim] = []
    dropped = 0
    demoted = 0
    for item in raw.claims:
        anchors = [seg_id for seg_id in item.segment_ids if seg_id in known]
        if not anchors or not item.text.strip():
            # An unanchored claim is the exact failure this product exists to
            # prevent. It does not get a second chance downstream.
            dropped += 1
            continue
        verbatim = item.verbatim
        if verbatim and verbatim not in document.text:
            verbatim = None
        kind = item.kind if item.kind in _KINDS else "fact"
        # A quote claim with no verbatim has no words to quote. `text` is a
        # restatement in the extractor's words by construction, so a generator
        # told to quote a quote claim will quote the restatement and attribute it
        # to a named person. art-willison published exactly that. The label is the
        # invitation, so the label goes: the claim is anchored and its content is
        # real, it simply is not a quotation. Demoted rather than dropped because
        # dropping loses a true claim to fix a false badge, and asking the model
        # again was tried and made it worse.
        if kind == "quote" and not verbatim:
            kind = "opinion"
            demoted += 1
        claims.append(
            Claim(
                # Ids are assigned from a running counter, never taken from the
                # model, so two windows can never hand back the same id.
                id=f"c{offset + len(claims)}",
                text=item.text.strip(),
                kind=kind,
                segment_ids=anchors,
                verbatim=verbatim,
            )
        )
    return claims, dropped, demoted


def extract(document: Document, llm: LLM, max_chars: int = WINDOW_CHARS) -> ClaimSet:
    """One pass per window, merged. Ids are assigned here, never by the model."""
    claims: list[Claim] = []
    entities: list[str] = []
    hooks: list[str] = []
    voices: list[str] = []
    theses: list[str] = []
    dropped = 0
    demoted = 0

    for segments in windows(document, max_chars):
        prompt = load_prompt(
            "extract",
            title=document.title,
            source_type=document.source_type,
            source=render_source(segments),
        )
        raw = llm.structured(prompt, _RawExtract)
        window_claims, window_dropped, window_demoted = _clean(raw, document, len(claims))
        claims += window_claims
        dropped += window_dropped
        demoted += window_demoted
        entities += raw.entities
        hooks += raw.hook_candidates
        # A "sample" the model paraphrased is not a sample. Only exact spans survive.
        voices += [v for v in raw.voice_samples if v.strip() and v.strip() in document.text]
        if raw.thesis.strip():
            theses.append(raw.thesis.strip())

    source_text = document.text.casefold()
    seen_entities: set[str] = set()
    kept_entities = []
    for name in entities:
        key = name.casefold()
        if key in source_text and key not in seen_entities:
            seen_entities.add(key)
            kept_entities.append(name)

    claim_set = ClaimSet(
        document_id=document.id,
        # The opening window states the argument; later windows restate fragments
        # of it, so the first one is the one to keep.
        thesis=theses[0] if theses else "",
        claims=claims,
        entities=kept_entities,
        hook_candidates=list(dict.fromkeys(hooks))[:8],
        voice_samples=list(dict.fromkeys(v.strip() for v in voices))[:6],
    )
    claim_set.__dict__["_dropped"] = dropped
    claim_set.__dict__["_demoted_quotes"] = demoted
    return claim_set


def render_claims(claims: ClaimSet, document: Document) -> str:
    """The claim inventory as a generator sees it. This is the ONLY context a
    generator gets: it cannot quote what it was never shown."""
    # Not "THESIS:", which a generator once copied into claim_ids as if it were
    # an id, and the citation rule dutifully flagged it.
    lines = [f"The argument the source makes: {claims.thesis}"] if claims.thesis else []
    lines.append("")
    for claim in claims.claims:
        anchors = ", ".join(claim.segment_ids)
        line = f"[{claim.id}] ({claim.kind}) {claim.text}   <- {anchors}"
        if claim.verbatim:
            line += f'\n      exact words: "{claim.verbatim}"'
        lines.append(line)
    if claims.hook_candidates:
        lines.append("")
        lines.append("HOOKS THE SOURCE ALREADY MAKES:")
        lines += [f"- {hook}" for hook in claims.hook_candidates]
    if claims.voice_samples:
        lines.append("")
        lines.append(
            "HOW THE SOURCE SOUNDS. These are exact sentences from it, included so "
            "you can match its register. They are tone reference, not extra facts."
        )
        lines += [f"- {sample}" for sample in claims.voice_samples]
    return "\n".join(lines)
