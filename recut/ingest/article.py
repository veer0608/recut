"""A web article in, the same Document out.

trafilatura does the boilerplate stripping. Everything after that is the markdown
path, unchanged: articles and files differ in where the text came from, not in how
a claim gets anchored to it.
"""

from __future__ import annotations

import httpx
import trafilatura

from ..models import Document
from .markdown import _doc_id, split_segments, title_of

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


# Below this, whatever came back is navigation furniture rather than an article.
MIN_ARTICLE_CHARS = 200


class FetchError(RuntimeError):
    pass


def fetch_html(url: str, timeout: float = 30.0) -> bytes:
    """Kept separate from the parsing so tests never need the network.

    Returns bytes on purpose. httpx picks an encoding from the response headers,
    which is wrong for any page that declares its charset only in a meta tag, and a
    mis-decoded name is one the verifier can never match back to the source.
    trafilatura sniffs the document itself, so hand it the bytes and let it decide.
    """
    try:
        response = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": UA, "Accept-Language": "en"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FetchError(f"could not fetch {url}: {exc}") from exc
    return response.content


def extract_article(html: str | bytes, url: str | None = None) -> tuple[str, str | None]:
    """Readable markdown plus the page's own title, if it declared one.

    Three passes, strictest first. A repurposing tool would rather lose a marginal
    paragraph than inherit a cookie banner, so precision leads. But precision also
    discards pages below a length threshold, and a short post is a real source, so
    a page that comes back empty gets progressively more generous treatment rather
    than a refusal.
    """
    metadata = trafilatura.extract_metadata(html)
    title = getattr(metadata, "title", None) if metadata else None

    # bare_extraction ignores output_format and hands back plain text, which loses
    # the headings that carry an article's structure. extract() is the one that
    # actually serialises markdown.
    best = ""
    for mode in ({"favor_precision": True}, {}, {"favor_recall": True}):
        text = (
            trafilatura.extract(
                html,
                url=url,
                output_format="markdown",
                include_comments=False,
                include_tables=True,
                **mode,
            )
            or ""
        ).strip()
        if len(text) >= MIN_ARTICLE_CHARS:
            return text, (title or None)
        best = max(best, text, key=len)

    # Recall mode will happily return a nav link off a page with no article on it.
    # Anything this short is not a source worth repurposing, so say so with the
    # number rather than handing the pipeline four words to work from.
    raise FetchError(
        f"no readable article content on the page (best extraction was "
        f"{len(best)} chars, need {MIN_ARTICLE_CHARS})"
    )


def ingest_article(url: str, html: str | bytes | None = None) -> Document:
    if html is None:
        html = fetch_html(url)
    text, page_title = extract_article(html, url)

    segments = split_segments(text)
    if not segments:
        raise FetchError("no readable article content on the page")

    # The page's own <title> beats a heading trafilatura kept from the body, but a
    # site that declares nothing still gets a title from the first line.
    title = (page_title or "").strip() or title_of(text, url)

    return Document(
        id=_doc_id(url, text),
        title=title,
        source_type="article",
        source_ref=url,
        segments=segments,
        # Offsets index the extracted article, not the HTML. That is the text a
        # reader would recognise as the piece, so it is the one worth highlighting.
        raw=text,
    )
