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
            retry.meta["repaired"] = True
            retry.meta["first_pass_errors"] = [str(w) for w in artifact.errors]
            artifact = retry if len(retry.errors) < len(artifact.errors) else artifact
        artifacts.append(artifact)

    return claims, artifacts


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
        sidecar = {
            "target": artifact.target,
            "meta": artifact.meta,
            "warnings": [w.model_dump() for w in artifact.warnings],
            "provenance": [
                {
                    "claim_id": claim.id,
                    "claim": claim.text,
                    "segments": [
                        {
                            "id": seg_id,
                            "timecode": (s.timecode if (s := document.segment(seg_id)) else None),
                            "char_start": s.char_start if s else None,
                            "char_end": s.char_end if s else None,
                        }
                        for seg_id in claim.segment_ids
                    ],
                }
                for claim_id in artifact.claim_ids
                if (claim := claims.claim(claim_id))
            ],
        }
        (out_dir / f"{artifact.target}.provenance.json").write_text(
            json.dumps(sidecar, indent=2), encoding="utf-8"
        )

    return out_dir
