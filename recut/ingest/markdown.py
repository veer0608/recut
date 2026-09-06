"""Markdown and plain text in, a Document with honest character offsets out.

Offsets are taken from the original file rather than from the cleaned text, so a
claim can always be highlighted in the thing the user actually uploaded.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..models import Document, Segment

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)


def _doc_id(source_ref: str, text: str) -> str:
    digest = hashlib.sha1(f"{source_ref}\n{text}".encode()).hexdigest()
    return digest[:12]


def _blank(match: re.Match[str]) -> str:
    """Same length, same line breaks, no content."""
    return "".join("\n" if char == "\n" else " " for char in match.group(0))


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    return slug[:60] or "untitled"


def split_segments(text: str, offset: int = 0) -> list[Segment]:
    """One segment per paragraph or heading, numbered s0, s1, ...

    Paragraphs are the right grain: small enough that a citation points somewhere
    specific, large enough that a claim rarely straddles two of them.
    """
    segments: list[Segment] = []
    cursor = 0
    for block in re.split(r"\n\s*\n", text):
        start = text.index(block, cursor)
        cursor = start + len(block)
        stripped = block.strip()
        if not stripped:
            continue
        # A heading and its paragraph are separate blocks in markdown but one unit
        # of meaning, so keep headings as their own short segment rather than
        # dropping them: they carry the article's structure.
        lead = start + (len(block) - len(block.lstrip()))
        segments.append(
            Segment(
                id=f"s{len(segments)}",
                text=stripped,
                char_start=offset + lead,
                char_end=offset + lead + len(stripped),
            )
        )
    return segments


def title_of(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = _HEADING.match(line.strip())
        if match:
            return match.group(2).strip()
        if line.strip():
            break
    return fallback


def ingest_markdown(path: str | Path) -> Document:
    path = Path(path)
    raw = path.read_text(encoding="utf-8")

    # Frontmatter is metadata, not content, and its keys pollute entity checks.
    # Blank it out character for character rather than deleting it, so every
    # offset below still indexes the file the user actually uploaded.
    body = _FRONTMATTER.sub(_blank, raw)

    title = title_of(body, path.stem.replace("-", " ").replace("_", " ").title())
    segments = split_segments(body)
    if not segments:
        raise ValueError(f"{path} has no readable content")

    return Document(
        id=_doc_id(str(path), raw),
        title=title,
        source_type="markdown",
        source_ref=str(path),
        segments=segments,
        raw=raw,
    )


def ingest_text(text: str, title: str = "Untitled", source_ref: str = "inline") -> Document:
    """Same normalisation, for content that never touched the filesystem."""
    segments = split_segments(text)
    if not segments:
        raise ValueError("no readable content")
    return Document(
        id=_doc_id(source_ref, text),
        title=title_of(text, title),
        source_type="markdown",
        source_ref=source_ref,
        segments=segments,
        raw=text,
    )


slug = _slug
