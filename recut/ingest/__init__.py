"""Pick an adapter from what the user typed.

Every adapter returns the same Document with the same invariant:
`document.raw[s.char_start:s.char_end] == s.text` for every segment. Nothing
downstream knows or cares which one ran.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..models import Document

_YOUTUBE_HOST = re.compile(r"^https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", re.IGNORECASE)
_URL = re.compile(r"^https?://", re.IGNORECASE)


def source_kind(ref: str) -> str:
    ref = ref.strip()
    if _YOUTUBE_HOST.match(ref):
        return "youtube"
    if _URL.match(ref):
        return "article"
    return "markdown"


def ingest(ref: str) -> Document:
    kind = source_kind(ref)
    if kind == "youtube":
        from .youtube import ingest_youtube

        return ingest_youtube(ref)
    if kind == "article":
        from .article import ingest_article

        return ingest_article(ref)

    from .markdown import ingest_markdown

    path = Path(ref)
    if not path.exists():
        raise FileNotFoundError(f"{ref} is not a file and does not look like a URL")
    return ingest_markdown(path)
