"""A YouTube video in, the same Document out, with timecodes attached.

Caption cues are a few words each, which is far too fine a grain to cite: a claim
would end up anchored to "and that is why". Cues are regrouped into sentence-sized
segments that keep the start time of their first cue and the end time of their last,
so a claim can be shown as "14:32" and the link can jump there.
"""

from __future__ import annotations

import re

import httpx
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import CouldNotRetrieveTranscript

from ..models import Document, Segment
from .markdown import _doc_id

# youtu.be/ID, /watch?v=ID, /shorts/ID, /embed/ID, /live/ID, or a bare id.
_ID_PATTERNS = (
    re.compile(r"(?:youtu\.be/)([0-9A-Za-z_-]{11})"),
    re.compile(r"[?&]v=([0-9A-Za-z_-]{11})"),
    re.compile(r"/(?:shorts|embed|live|v)/([0-9A-Za-z_-]{11})"),
    re.compile(r"^([0-9A-Za-z_-]{11})$"),
)

# Auto-generated captions arrive with no punctuation at all, so a sentence-ending
# rule alone would produce one enormous segment. These caps bound it either way.
MAX_SEGMENT_CHARS = 420
SENTENCE_END = re.compile(r"[.!?][\"')\]]?$")


class TranscriptError(RuntimeError):
    pass


def video_id(url: str) -> str:
    for pattern in _ID_PATTERNS:
        match = pattern.search(url.strip())
        if match:
            return match.group(1)
    raise TranscriptError(f"no YouTube video id in {url!r}")


def fetch_transcript(vid: str, languages: tuple[str, ...] = ("en", "en-US", "en-GB")) -> list[dict]:
    """Kept separate from the parsing so tests never need the network."""
    try:
        fetched = YouTubeTranscriptApi().fetch(vid, languages=list(languages))
    except CouldNotRetrieveTranscript as exc:
        raise TranscriptError(f"no transcript for {vid}: {type(exc).__name__}") from exc
    return [
        {"text": snippet.text, "start": snippet.start, "duration": snippet.duration}
        for snippet in fetched
    ]


def fetch_title(vid: str, timeout: float = 15.0) -> str | None:
    """oEmbed gives the title without a key or a quota. Failing is not fatal."""
    try:
        response = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
            timeout=timeout,
        )
        response.raise_for_status()
        return (response.json().get("title") or "").strip() or None
    except (httpx.HTTPError, ValueError):
        return None


def group_cues(cues: list[dict], max_chars: int = MAX_SEGMENT_CHARS) -> list[dict]:
    """Merge cues up to a sentence end, or to the cap when there is no punctuation."""
    grouped: list[dict] = []
    words: list[str] = []
    start: float | None = None
    end: float = 0.0

    for cue in cues:
        text = " ".join(cue["text"].split())
        if not text:
            continue
        if start is None:
            start = float(cue["start"])
        end = float(cue["start"]) + float(cue.get("duration") or 0.0)
        words.append(text)

        joined = " ".join(words)
        if SENTENCE_END.search(joined) or len(joined) >= max_chars:
            grouped.append({"text": joined, "t_start": start, "t_end": end})
            words, start = [], None

    if words and start is not None:
        grouped.append({"text": " ".join(words), "t_start": start, "t_end": end})
    return grouped


def build_document(vid: str, cues: list[dict], title: str | None = None) -> Document:
    """Assemble raw text and offsets together so the two can never disagree."""
    segments: list[Segment] = []
    raw_parts: list[str] = []
    cursor = 0

    for group in group_cues(cues):
        text = group["text"]
        segments.append(
            Segment(
                id=f"s{len(segments)}",
                text=text,
                char_start=cursor,
                char_end=cursor + len(text),
                t_start=group["t_start"],
                t_end=group["t_end"],
            )
        )
        raw_parts.append(text)
        cursor += len(text) + 2  # the "\n\n" that joins them below

    if not segments:
        raise TranscriptError(f"transcript for {vid} is empty")

    raw = "\n\n".join(raw_parts)
    return Document(
        id=_doc_id(vid, raw),
        title=title or f"YouTube video {vid}",
        source_type="youtube",
        source_ref=f"https://www.youtube.com/watch?v={vid}",
        segments=segments,
        raw=raw,
    )


def ingest_youtube(url: str, cues: list[dict] | None = None, title: str | None = None) -> Document:
    vid = video_id(url)
    if cues is None:
        cues = fetch_transcript(vid)
    if title is None:
        title = fetch_title(vid)
    return build_document(vid, cues, title)
