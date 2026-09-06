"""Core types. Segment is the load-bearing one: everything downstream cites it."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal["markdown", "article", "youtube", "media"]
ClaimKind = Literal["fact", "stat", "quote", "opinion", "narrative"]
Target = Literal["linkedin", "thread", "newsletter", "article", "vidsmith"]
Severity = Literal["error", "notice"]


class Segment(BaseModel):
    """A citable span of the source.

    Text sources anchor on character offsets. Timed sources anchor on offsets and
    on wall-clock seconds, so a claim lifted from a video can be shown as "14:32"
    without any extra bookkeeping.
    """

    id: str
    text: str
    char_start: int
    char_end: int
    t_start: float | None = None
    t_end: float | None = None
    speaker: str | None = None

    @property
    def timecode(self) -> str | None:
        if self.t_start is None:
            return None
        total = int(self.t_start)
        return f"{total // 60}:{total % 60:02d}"


class Document(BaseModel):
    id: str
    title: str
    source_type: SourceType
    source_ref: str
    lang: str = "en"
    segments: list[Segment] = Field(default_factory=list)
    # The exact text every char_start and char_end indexes into. For a file it is
    # the file; for a web page it is what the extractor pulled out of the HTML; for
    # a video it is the transcript we assembled. Keeping it means the same
    # highlight-the-source guarantee holds whatever the source was.
    raw: str = ""

    def excerpt(self, segment: Segment) -> str:
        return self.raw[segment.char_start : segment.char_end]

    @property
    def text(self) -> str:
        return "\n\n".join(s.text for s in self.segments)

    @property
    def is_timed(self) -> bool:
        return any(s.t_start is not None for s in self.segments)

    def segment(self, seg_id: str) -> Segment | None:
        for s in self.segments:
            if s.id == seg_id:
                return s
        return None


class Claim(BaseModel):
    id: str
    text: str
    kind: ClaimKind = "fact"
    segment_ids: list[str] = Field(default_factory=list)
    verbatim: str | None = None
    confidence: float = 1.0


class ClaimSet(BaseModel):
    document_id: str
    thesis: str = ""
    claims: list[Claim] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    hook_candidates: list[str] = Field(default_factory=list)
    # Verbatim sentences kept only so a generator can match the source's register.
    # They are safe to show: a span copied from the source is anchored by
    # definition, which is not true of anything a model writes about the source.
    voice_samples: list[str] = Field(default_factory=list)

    def claim(self, claim_id: str) -> Claim | None:
        for c in self.claims:
            if c.id == claim_id:
                return c
        return None

    @property
    def ids(self) -> set[str]:
        return {c.id for c in self.claims}


class Warning(BaseModel):
    """One thing the verifier could not anchor back to the source."""

    rule: Literal["number", "quote", "entity", "intensity", "citation"]
    severity: Severity
    span: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.severity}/{self.rule}] {self.span!r}: {self.detail}"


class Artifact(BaseModel):
    target: Target
    body: str
    claim_ids: list[str] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)
    # Extra files this target emits, keyed by path relative to the run directory.
    # A vidsmith project is a directory, not a paragraph, and the alternative was
    # a special case in write_out for exactly one target.
    files: dict[str, str] = Field(default_factory=dict)

    @property
    def errors(self) -> list[Warning]:
        return [w for w in self.warnings if w.severity == "error"]

    @property
    def clean(self) -> bool:
        return not self.errors
