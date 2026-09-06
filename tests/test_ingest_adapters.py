"""Three source types, one contract. No network in any of these."""

import pytest

from recut.ingest import source_kind
from recut.ingest.article import extract_article, ingest_article
from recut.ingest.markdown import ingest_text
from recut.ingest.youtube import (
    TranscriptError,
    build_document,
    group_cues,
    ingest_youtube,
    video_id,
)

MARKDOWN = """# Why Your Bank Statement Lies

The merchant name is typed by the payment processor, not the shop.

The date you see is usually the settlement date.
"""

HTML = """<!doctype html><html><head>
<title>Why Your Bank Statement Lies</title></head><body>
<nav><a href="/">Home</a><a href="/about">About</a></nav>
<div class="cookie-banner">We use cookies. Accept all?</div>
<article>
<h1>Why Your Bank Statement Lies</h1>
<p>The merchant name is typed by the payment processor, not the shop. That is why a
coffee costs money at a company you have never heard of.</p>
<p>The date you see is usually the settlement date, not the day you paid. On a
weekend, those can be three days apart.</p>
</article>
<footer>Copyright 2026. All rights reserved. Subscribe to our newsletter.</footer>
</body></html>"""

CUES = [
    {"text": "the merchant name on a transaction", "start": 0.0, "duration": 2.0},
    {"text": "is typed by the payment processor,", "start": 2.0, "duration": 2.5},
    {"text": "not the shop.", "start": 4.5, "duration": 1.5},
    {"text": "the date you see is usually", "start": 6.0, "duration": 2.0},
    {"text": "the settlement date.", "start": 8.0, "duration": 2.0},
]


@pytest.fixture
def markdown_doc():
    return ingest_text(MARKDOWN, source_ref="test.md")


@pytest.fixture
def article_doc():
    return ingest_article("https://example.com/statements", html=HTML)


@pytest.fixture
def youtube_doc():
    return ingest_youtube(
        "https://www.youtube.com/watch?v=0PkBP0dk4Lw", cues=CUES, title="Why Your Bank Statement Lies"
    )


@pytest.fixture(params=["markdown_doc", "article_doc", "youtube_doc"])
def any_doc(request):
    return request.getfixturevalue(request.param)


class TestOneContract:
    """The whole point of phase 1: nothing downstream can tell these apart."""

    def test_offsets_index_the_raw_text(self, any_doc):
        for segment in any_doc.segments:
            assert any_doc.excerpt(segment) == segment.text

    def test_segments_are_ordered_and_uniquely_named(self, any_doc):
        ids = [s.id for s in any_doc.segments]
        assert ids == [f"s{i}" for i in range(len(ids))]
        assert all(
            a.char_start < b.char_start for a, b in zip(any_doc.segments, any_doc.segments[1:])
        )

    def test_it_has_a_title_and_a_source_ref(self, any_doc):
        assert any_doc.title.strip()
        assert any_doc.source_ref.strip()

    def test_the_content_survived(self, any_doc):
        assert "merchant name" in any_doc.text.casefold()
        assert "settlement date" in any_doc.text.casefold()

    def test_lookup_by_id_round_trips(self, any_doc):
        assert any_doc.segment("s0").id == "s0"
        assert any_doc.segment("s99") is None


class TestSourceKind:
    @pytest.mark.parametrize(
        "ref, kind",
        [
            ("https://www.youtube.com/watch?v=0PkBP0dk4Lw", "youtube"),
            ("https://youtu.be/0PkBP0dk4Lw", "youtube"),
            ("https://m.youtube.com/watch?v=0PkBP0dk4Lw", "youtube"),
            ("https://example.com/post", "article"),
            ("http://example.com/post", "article"),
            ("notes/post.md", "markdown"),
            (r"C:\Users\veera\post.md", "markdown"),
        ],
    )
    def test_dispatch(self, ref, kind):
        assert source_kind(ref) == kind


class TestArticle:
    def test_boilerplate_is_stripped(self, article_doc):
        body = article_doc.text.casefold()
        assert "cookie" not in body
        assert "subscribe to our newsletter" not in body
        assert "all rights reserved" not in body

    def test_title_comes_from_the_page(self, article_doc):
        assert article_doc.title == "Why Your Bank Statement Lies"

    def test_source_ref_is_the_url(self, article_doc):
        assert article_doc.source_ref == "https://example.com/statements"

    def test_an_article_is_not_timed(self, article_doc):
        assert article_doc.is_timed is False

    def test_a_nav_only_page_is_refused_with_the_length(self):
        # Recall mode happily returns "Home" here. Four words is not an article,
        # and the error says how short it actually was.
        with pytest.raises(RuntimeError, match="need 200"):
            extract_article("<html><body><nav><a href='/'>Home</a></nav></body></html>")


class TestYouTube:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=0PkBP0dk4Lw",
            "https://youtu.be/0PkBP0dk4Lw",
            "https://www.youtube.com/shorts/0PkBP0dk4Lw",
            "https://www.youtube.com/embed/0PkBP0dk4Lw",
            "https://www.youtube.com/watch?list=PL1&v=0PkBP0dk4Lw&t=30",
            "0PkBP0dk4Lw",
        ],
    )
    def test_every_url_shape_yields_the_id(self, url):
        assert video_id(url) == "0PkBP0dk4Lw"

    def test_a_url_with_no_id_is_refused(self):
        with pytest.raises(TranscriptError):
            video_id("https://www.youtube.com/feed/subscriptions")

    def test_cues_are_regrouped_to_sentences(self, youtube_doc):
        # Five cues, two sentences. Citing "not the shop." on its own is useless.
        assert len(youtube_doc.segments) == 2
        assert youtube_doc.segments[0].text.endswith("not the shop.")

    def test_a_group_spans_its_first_and_last_cue(self, youtube_doc):
        first = youtube_doc.segments[0]
        assert first.t_start == 0.0
        assert first.t_end == 6.0

    def test_timecodes_are_human_readable(self):
        cues = [{"text": "a claim made late in the video.", "start": 872.0, "duration": 3.0}]
        assert build_document("x", cues).segments[0].timecode == "14:32"

    def test_unpunctuated_captions_still_get_split(self):
        # Auto-generated captions carry no punctuation at all.
        cues = [{"text": "word " * 20, "start": float(i), "duration": 1.0} for i in range(30)]
        grouped = group_cues(cues, max_chars=200)
        assert len(grouped) > 1
        assert all(len(g["text"]) < 320 for g in grouped)

    def test_a_video_is_timed(self, youtube_doc):
        assert youtube_doc.is_timed is True

    def test_an_empty_transcript_is_refused(self):
        with pytest.raises(TranscriptError):
            build_document("x", [])
