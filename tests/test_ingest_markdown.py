import pytest

from recut.ingest.markdown import ingest_markdown, ingest_text, split_segments

ARTICLE = """---
title: Ignore me
tags: [a, b]
---

# Why Your Bank Statement Lies

Your bank statement is not a record of what you spent.

## Merchant names

The merchant name is typed by the payment processor, not the shop.
"""


@pytest.fixture
def path(tmp_path):
    file = tmp_path / "statement.md"
    file.write_text(ARTICLE, encoding="utf-8")
    return file


def test_title_comes_from_the_first_heading(path):
    assert ingest_markdown(path).title == "Why Your Bank Statement Lies"


def test_frontmatter_is_not_content(path):
    document = ingest_markdown(path)
    assert "Ignore me" not in document.text


def test_offsets_point_at_the_original_file(path):
    raw = path.read_text(encoding="utf-8")
    for segment in ingest_markdown(path).segments:
        assert raw[segment.char_start : segment.char_end] == segment.text


def test_segments_are_ordered_and_uniquely_named(path):
    segments = ingest_markdown(path).segments
    assert [s.id for s in segments] == [f"s{i}" for i in range(len(segments))]
    assert all(a.char_start < b.char_start for a, b in zip(segments, segments[1:]))


def test_text_source_is_not_timed(path):
    assert ingest_markdown(path).is_timed is False


def test_lookup_by_segment_id(path):
    document = ingest_markdown(path)
    assert document.segment("s0") is not None
    assert document.segment("s99") is None


def test_empty_source_is_rejected(tmp_path):
    empty = tmp_path / "empty.md"
    empty.write_text("   \n\n  \n", encoding="utf-8")
    with pytest.raises(ValueError):
        ingest_markdown(empty)


def test_inline_text_takes_the_same_path():
    document = ingest_text("# Hello\n\nOne paragraph.\n\nAnother.")
    assert document.title == "Hello"
    assert len(document.segments) == 3


def test_blank_blocks_do_not_become_segments():
    assert len(split_segments("a\n\n\n\n\nb")) == 2
