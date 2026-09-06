"""Emit a vidsmith project directory that builds without being touched.

The output of this target is not a paragraph, it is a folder. vidsmith's
`_project_dir` accepts a path, so the emitted directory can be handed straight to
`python -m vidsmith build <path>` with no copying into its projects tree.
"""

from __future__ import annotations

import re

import yaml
from pydantic import BaseModel, Field

from ..extract import render_claims
from ..llm import LLM, load_prompt
from ..models import Artifact, ClaimSet, Document

MIN_SCENES = 5
MAX_SCENES = 7
MIN_WORDS = 110
MAX_WORDS = 200
MAX_SCENE_WORDS = 45

# Things a speech engine reads badly, or reads as the wrong word entirely.
_UNSPEAKABLE = re.compile(r"[%$£€₹*_`|#>]|\[|\]|\(|\)|\b\d+\s*[kKmMbB]\b")
# A stock library has no footage of an abstraction. These are the queries that
# come back with a stock photo of a lightbulb.
_ABSTRACT_QUERY = re.compile(
    r"^\s*(the\s+)?(concept|idea|notion|sense|feeling|process|nature|importance)\b",
    re.IGNORECASE,
)


class _Scene(BaseModel):
    heading: str = ""
    visual: str = ""
    narration: str


class _Response(BaseModel):
    title: str = ""
    scenes: list[_Scene] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


CONFIG: dict = {
    "theme": {"preset": "midnight", "title_card": True, "end_card": True},
    "voice": {"name": "en-US-AndrewNeural", "rate": "+8%"},
    "visuals": {"provider": "cards", "orientation": "portrait"},
    "captions": {"enabled": True, "style": "karaoke"},
    "audio": {"music": "auto", "mood": "calm"},
    "render": {"aspect": "9:16", "fps": 30},
}


def render_config(title: str, source_ref: str) -> str:
    """Dumped by PyYAML, not formatted into a template.

    A title is arbitrary text from a model, and titles containing a colon are
    completely ordinary ("Rome: how it really fell"). String interpolation emits
    invalid YAML for those and vidsmith would refuse the project.
    """
    body = yaml.safe_dump(
        {"title": title, **CONFIG}, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return f"# Written by recut from {source_ref}\n{body}"


def render_script(title: str, scenes: list[_Scene]) -> str:
    """vidsmith's own format: a title, then a heading, a visual and a paragraph."""
    parts = [f"# {title}", ""]
    for index, scene in enumerate(scenes, 1):
        parts.append(f"## {scene.heading.strip() or f'Scene {index}'}")
        if scene.visual.strip():
            parts.append(f"[visual: {scene.visual.strip()}]")
        parts.append(" ".join(scene.narration.split()))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def build(document: Document, claims: ClaimSet, llm: LLM, note: str = "") -> Artifact:
    prompt = load_prompt("video", title=document.title, claims=render_claims(claims, document))
    if note:
        prompt = f"{prompt}\n\n---\n{note}"
    response = llm.structured(prompt, _Response)

    scenes = [s for s in response.scenes if s.narration.strip()]
    title = response.title.strip() or document.title
    narration = " ".join(" ".join(s.narration.split()) for s in scenes)
    words = len(narration.split())

    violations = []
    if not MIN_SCENES <= len(scenes) <= MAX_SCENES:
        violations.append(f"{len(scenes)} scenes, wanted {MIN_SCENES} to {MAX_SCENES}")
    if not MIN_WORDS <= words <= MAX_WORDS:
        violations.append(f"{words} narration words, wanted {MIN_WORDS} to {MAX_WORDS}")

    long_scenes = [i + 1 for i, s in enumerate(scenes) if len(s.narration.split()) > MAX_SCENE_WORDS]
    if long_scenes:
        violations.append(f"scenes over {MAX_SCENE_WORDS} words: {long_scenes}")

    unspeakable = [i + 1 for i, s in enumerate(scenes) if _UNSPEAKABLE.search(s.narration)]
    if unspeakable:
        violations.append(f"narration a voice cannot read cleanly in scenes: {unspeakable}")

    missing_visual = [i + 1 for i, s in enumerate(scenes) if not s.visual.strip()]
    if missing_visual:
        violations.append(f"scenes with no visual query: {missing_visual}")

    abstract = [i + 1 for i, s in enumerate(scenes) if _ABSTRACT_QUERY.match(s.visual)]
    if abstract:
        violations.append(f"unfilmable visual queries in scenes: {abstract}")

    meta = {
        "scenes": len(scenes),
        "words": words,
        "title": title,
        "estimated_seconds": round(words / 2.6, 1),
    }
    if violations:
        meta["format_violation"] = "; ".join(violations)

    script = render_script(title, scenes)
    return Artifact(
        target="vidsmith",
        # Only the narration is verified. A "[visual: ...]" line is a stock search
        # query, never spoken and never published, and checking it would flag every
        # perfectly good query that names something the source did not.
        body=narration,
        claim_ids=response.claim_ids,
        meta=meta,
        files={
            "vidsmith/script.md": script,
            "vidsmith/config.yaml": render_config(title, document.source_ref),
        },
    )
