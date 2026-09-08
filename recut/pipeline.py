"""Source in, verified artifacts out. One extraction pass, one call per target."""

from __future__ import annotations

import json
from pathlib import Path

from .extract import extract
from .generate import article, linkedin, newsletter, thread, vidsmith
from .llm import LLM
from .models import Artifact, ClaimSet, Document
from .verify import repair_note, verify

GENERATORS = {
    "linkedin": linkedin,
    "thread": thread,
    "newsletter": newsletter,
    "article": article,
    "vidsmith": vidsmith,
}


def unsupported(name: str, document: Document) -> str | None:
    """Why this target cannot run on this source, or None if it can."""
    module = GENERATORS.get(name)
    if module is None:
        return f"unknown target {name!r}; have {sorted(GENERATORS)}"
    if getattr(module, "requires_timed", False) and not document.is_timed:
        # Rewriting an article as an article is not repurposing, it is a
        # plagiarism surface with a different font.
        return "only offered for video and audio sources"
    return None


def applicable(document: Document) -> list[str]:
    return [name for name in GENERATORS if unsupported(name, document) is None]


def repurpose(
    document: Document,
    targets: list[str],
    llm: LLM,
    claims: ClaimSet | None = None,
) -> tuple[ClaimSet, list[Artifact]]:
    """The claim inventory is built once and shared by every target.

    That is not only a cost decision. Two outputs generated from the same
    inventory cannot contradict each other, which they routinely do when each one
    reads the source itself.
    """
    if claims is None:
        claims = extract(document, llm)

    artifacts: list[Artifact] = []
    for name in targets:
        reason = unsupported(name, document)
        if reason:
            raise ValueError(f"{name}: {reason}")
        build = GENERATORS[name].build

        artifact = verify(build(document, claims, llm), document, claims)
        if artifact.errors:
            # Exactly one repair attempt. A second failure is surfaced rather than
            # hidden: silently shipping an unanchored figure is the thing this
            # product exists to stop.
            retry = verify(
                build(document, claims, llm, note=repair_note(artifact)), document, claims
            )
            # Did the retry clear what it was actually asked to clear? Counting
            # errors treats every rule as interchangeable, and it kept a draft
            # that put an invented direct quotation in a named person's mouth:
            # art-willison/linkedin in v6 tied on count, so the first pass won and
            # the fabricated quote shipped. The repair note names specific spans.
            # Whether those spans are gone is the question it was sent to answer.
            asked = {(w.rule, w.span) for w in artifact.errors}
            cleared = asked - {(w.rule, w.span) for w in retry.errors}
            keep_retry = len(retry.errors) < len(artifact.errors) or (
                bool(cleared) and len(retry.errors) <= len(artifact.errors)
            )
            first_pass = [str(w) for w in artifact.errors]
            retry_errors = [str(w) for w in retry.errors]
            artifact = retry if keep_retry else artifact
            # Recorded on whichever draft survives. Both of these used to be set on
            # the retry alone, so a rejected repair took the evidence that it had
            # ever run out of scope with it, and the stored run could not tell a
            # repair that was refused from one that never happened.
            artifact.meta["repaired"] = keep_retry
            artifact.meta["repair_attempted"] = True
            artifact.meta["first_pass_errors"] = first_pass
            artifact.meta["repair_errors"] = retry_errors
            artifact.meta["repair_cleared"] = sorted(
                f"[{rule}] {span}" for rule, span in cleared
            )
        _carry_provenance(artifact, document, claims)
        artifacts.append(artifact)

    return claims, artifacts


def sidecar(artifact: Artifact, document: Document, claims: ClaimSet) -> dict:
    """Which span of the source stands behind each claim this artifact used."""
    return {
        "target": artifact.target,
        "source": {"title": document.title, "ref": document.source_ref},
        "meta": artifact.meta,
        "warnings": [w.model_dump() for w in artifact.warnings],
        "provenance": [
            {
                "claim_id": claim.id,
                "claim": claim.text,
                "segments": [
                    {
                        "id": seg_id,
                        "timecode": (seg.timecode if (seg := document.segment(seg_id)) else None),
                        "char_start": seg.char_start if seg else None,
                        "char_end": seg.char_end if seg else None,
                    }
                    for seg_id in claim.segment_ids
                ],
            }
            for claim_id in artifact.claim_ids
            if (claim := claims.claim(claim_id))
        ],
    }


def _carry_provenance(artifact: Artifact, document: Document, claims: ClaimSet) -> None:
    """Put the anchors inside the directory a target emits.

    A target that emits files hands that directory to another tool, and the
    sidecar written beside it does not travel. The vidsmith project becomes a
    published video, so it was the one output that could not be traced back to
    its source, in a product whose entire claim is that everything can.

    Written after verification so the warnings it carries are the ones that
    survived the repair pass, not the first draft's.
    """
    if not artifact.files:
        return
    roots = {Path(name).parts[0] for name in artifact.files if Path(name).parts}
    for root in roots:
        artifact.files[f"{root}/provenance.json"] = json.dumps(
            sidecar(artifact, document, claims), indent=2
        )


def write_out(
    out_dir: Path, document: Document, claims: ClaimSet, artifacts: list[Artifact]
) -> Path:
    """Each artifact plus a provenance sidecar naming the source span behind it."""
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "claims.json").write_text(
        claims.model_dump_json(indent=2), encoding="utf-8"
    )

    for artifact in artifacts:
        (out_dir / f"{artifact.target}.txt").write_text(artifact.body, encoding="utf-8")
        for relative, content in artifact.files.items():
            path = out_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (out_dir / f"{artifact.target}.provenance.json").write_text(
            json.dumps(sidecar(artifact, document, claims), indent=2), encoding="utf-8"
        )

    return out_dir
