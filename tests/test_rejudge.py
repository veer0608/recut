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
    monkeypatch.setattr(rejudge, "judge", lambda body, document, llm, cache=None: judge.result)


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


class TestOnlyFlag:
    """--only limits what is paid for, never what is reported.

    The trap it avoids: a targeted run that reported over its own selection
    would turn "judge one source" into a headline over one source, which is the
    abandonment rule defeated by a command-line flag.
    """

    def _run(self, tmp_path, monkeypatch, only, existing=()):
        import rejudge as rj
        import run_eval

        runs = tmp_path / "results"
        (runs / "r" / "sources").mkdir(parents=True)
        source = tmp_path / "post.md"
        source.write_text("# S" + "\n\n" + "The source says a thing.", encoding="utf-8")
        for name in ("a", "b", "c"):
            (runs / "r" / "sources" / f"{name}.json").write_text(
                json.dumps({
                    "id": name, "kind": "markdown", "ref": str(source),
                    "chars": len(source.read_text(encoding="utf-8")),
                    "targets": ["linkedin"], "bodies": {"linkedin": "A thing is said."},
                    "judged_claims": 2, "judged_unsupported": 0,
                    "claim_utilisation": 0.5, "format_violations": {},
                    "injections": {"planted": 1, "caught": 1, "clean_bodies": 1,
                                   "false_positive_bodies": 0, "recall_by_rule": {"number": 1.0}},
                }), encoding="utf-8",
            )
        out = runs / "r-rejudged" / "sources"
        out.mkdir(parents=True)
        for name in existing:
            (out / f"{name}.json").write_text(
                json.dumps({"id": name, "targets": ["linkedin"], "judged_by_model": True,
                            "judged_claims": 2, "judged_unsupported": 0,
                            "claim_utilisation": 0.5, "format_violations": {},
                            "injections": {"planted": 1, "caught": 1, "clean_bodies": 1,
                                           "false_positive_bodies": 0,
                                           "recall_by_rule": {"number": 1.0}}}),
                encoding="utf-8",
            )
        monkeypatch.setattr(rj, "RESULTS", runs)
        monkeypatch.setattr(run_eval, "RESULTS", runs)
        class _Stub:
            budget = type("B", (), {"calls": 0})()

        monkeypatch.setattr(rj, "judge_client", lambda **kw: _Stub())
        monkeypatch.setattr(rj, "judge", lambda body, doc, llm, cache=None: {
            "claims": 2, "unsupported": 1, "rate": 0.5, "sentences": 2,
            "unjudged": 0, "verdicts": [],
        })
        rj.main(["r", "--only", only])
        return json.loads((runs / "r-rejudged" / "report.json").read_text(encoding="utf-8"))

    def test_only_the_named_source_is_judged(self, tmp_path, monkeypatch):
        report = self._run(tmp_path, monkeypatch, "b")
        assert {f["id"] for f in report["failures"]} == {"a", "c"}
        assert report["sources_completed"] == 1

    def test_a_targeted_run_does_not_publish_a_rate_over_its_selection(self, tmp_path, monkeypatch):
        assert self._run(tmp_path, monkeypatch, "b")["unsupported_claim_rate"] is None

    def test_earlier_work_still_counts_toward_the_report(self, tmp_path, monkeypatch):
        # a and c were judged on previous runs; judging b completes the set.
        report = self._run(tmp_path, monkeypatch, "b", existing=("a", "c"))
        assert report["sources_completed"] == 3
        assert report["complete"] is True
        assert report["unsupported_claim_rate"] is not None


class TestAbandonment:
    def test_a_source_that_fails_to_judge_still_withholds_the_headline(self, checkpoint, monkeypatch):
        _patch(monkeypatch, _Judge())
        good = rejudge.rejudge_source(checkpoint, object())
        report = aggregate([good], [{"id": "md-y", "error": "429"}], {})
        assert report["unsupported_claim_rate"] is None
        assert report["unsupported_claim_rate_provisional"] is not None
