"""Re-scoring a finished run, without a judge and without a network.

A rate this file produces will sit beside rates from live runs, so the parts
that decide comparability are what get tested: that only the judged numbers
change, that a stale rate cannot survive, and that source drift is recorded
rather than assumed away.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

import rejudge  # noqa: E402
from run_eval import aggregate  # noqa: E402


@pytest.fixture
def checkpoint(tmp_path):
    source = tmp_path / "post.md"
    source.write_text(
        "# Reconciliation\n\nReconciling often is good practice.\n\n"
        "Differences may arise between the records.\n",
        encoding="utf-8",
    )
    path = tmp_path / "md-x.json"
    path.write_text(
        json.dumps(
            {
                "id": "md-x",
                "kind": "markdown",
                "ref": str(source),
                "chars": len(source.read_text(encoding="utf-8")),
                "targets": ["linkedin"],
                "bodies": {"linkedin": "Reconciling often is good practice."},
                "judged": {"linkedin": {"claims": 9, "unsupported": 9, "rate": 1.0}},
                "judged_claims": 9,
                "judged_unsupported": 9,
                "claim_utilisation": 0.5,
                "format_violations": {},
                "injections": {
                    "planted": 5, "caught": 5, "clean_bodies": 1,
                    "false_positive_bodies": 0, "recall_by_rule": {"number": 1.0},
                },
            }
        ),
        encoding="utf-8",
    )
    return path


class _Judge:
    def __init__(self, claims=4, unsupported=1):
        self.result = {"claims": claims, "unsupported": unsupported, "rate": unsupported / claims,
                       "sentences": claims, "unjudged": 0, "verdicts": []}


def _patch(monkeypatch, judge):
    monkeypatch.setattr(rejudge, "judge", lambda body, document, llm: judge.result)


class TestRejudgeSource:
    def test_the_old_judged_numbers_are_replaced_not_merged(self, checkpoint, monkeypatch):
        _patch(monkeypatch, _Judge(claims=4, unsupported=1))
        out = rejudge.rejudge_source(checkpoint, llm := object())
        assert out["judged_claims"] == 4
        assert out["judged_unsupported"] == 1

    def test_everything_the_judge_did_not_produce_is_carried_through(self, checkpoint, monkeypatch):
        _patch(monkeypatch, _Judge())
        out = rejudge.rejudge_source(checkpoint, object())
        old = json.loads(checkpoint.read_text(encoding="utf-8"))
        for key in ("id", "kind", "ref", "bodies", "injections", "claim_utilisation"):
            assert out[key] == old[key]

    def test_the_previous_scale_is_recorded_so_the_swap_is_visible(self, checkpoint, monkeypatch):
        _patch(monkeypatch, _Judge())
        out = rejudge.rejudge_source(checkpoint, object())
        assert out["rejudged"]["from"] == 9
        assert out["rejudged"]["provider"]

    def test_source_drift_is_measured_rather_than_assumed(self, checkpoint, monkeypatch):
        _patch(monkeypatch, _Judge())
        assert rejudge.rejudge_source(checkpoint, object())["rejudged"]["source_char_drift"] == 0

        old = json.loads(checkpoint.read_text(encoding="utf-8"))
        old["chars"] = 1
        checkpoint.write_text(json.dumps(old), encoding="utf-8")
        out = rejudge.rejudge_source(checkpoint, object())
        # A body written against different text is not comparable, so the size
        # of the difference has to reach the report rather than be swallowed.
        assert out["rejudged"]["source_char_drift"] > 0

    def test_a_rejudged_result_counts_as_judged(self, checkpoint, monkeypatch):
        _patch(monkeypatch, _Judge())
        out = rejudge.rejudge_source(checkpoint, object())
        # Otherwise the --no-judge guard withholds the headline from a run that
        # was, in fact, judged.
        assert out["judged_by_model"] is True
        assert aggregate([out], [], {})["unsupported_claim_rate"] is not None


class TestAbandonment:
    def test_a_source_that_fails_to_judge_still_withholds_the_headline(self, checkpoint, monkeypatch):
        _patch(monkeypatch, _Judge())
        good = rejudge.rejudge_source(checkpoint, object())
        report = aggregate([good], [{"id": "md-y", "error": "429"}], {})
        assert report["unsupported_claim_rate"] is None
        assert report["unsupported_claim_rate_provisional"] is not None
