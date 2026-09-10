"""The eval harness itself, offline. A metric nobody tested is a rumour."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from inject import plant, score  # noqa: E402
from judge import judge, sentences_of  # noqa: E402
from judge_variance import compare  # noqa: E402
from run_eval import aggregate, clear_stale, load_sources  # noqa: E402

from recut.ingest.markdown import ingest_text  # noqa: E402
from recut.models import Artifact, Claim, ClaimSet  # noqa: E402
from recut.verify import verify  # noqa: E402

from .test_pipeline import ScriptedLLM  # noqa: E402

SOURCE = """# Why Your Bank Statement Lies

The merchant name is typed by the payment processor, not the shop.

Settlement and payment dates can be three days apart on a weekend.
"""


@pytest.fixture
def document():
    return ingest_text(SOURCE, source_ref="test")


@pytest.fixture
def claims():
    return ClaimSet(
        document_id="d",
        claims=[
            Claim(id="c0", text="Merchant names come from the processor.", segment_ids=["s1"]),
            Claim(id="c1", text="Dates can be three days apart.", segment_ids=["s2"]),
        ],
    )


@pytest.fixture
def clean_artifact():
    return Artifact(
        target="linkedin",
        body=(
            "Merchant names come from the payment processor, not the shop. "
            "Settlement and payment dates can be three days apart on a weekend."
        ),
        claim_ids=["c0"],
    )


class TestInjection:
    def test_one_injection_per_rule(self, clean_artifact, document):
        planted = plant(clean_artifact, document)
        assert {i.kind for i in planted} == {"number", "quote", "entity", "intensity", "citation"}

    def test_planted_text_is_absent_from_the_source(self, clean_artifact, document):
        for injection in plant(clean_artifact, document):
            if injection.kind not in ("citation", "intensity"):
                assert injection.planted.casefold() not in document.text.casefold()

    def test_injections_are_deterministic_for_a_seed(self, clean_artifact, document):
        a = [i.planted for i in plant(clean_artifact, document, seed=7)]
        b = [i.planted for i in plant(clean_artifact, document, seed=7)]
        assert a == b

    def test_a_different_seed_plants_different_text(self, clean_artifact, document):
        a = [i.planted for i in plant(clean_artifact, document, seed=1)]
        b = [i.planted for i in plant(clean_artifact, document, seed=4)]
        assert a != b

    def test_the_verifier_catches_every_planted_fabrication(
        self, clean_artifact, document, claims
    ):
        result = score([clean_artifact], document, claims)
        assert result["planted"] == 5
        assert result["recall"] == 1.0, result["missed"]

    def test_a_clean_body_raises_no_error(self, clean_artifact, document, claims):
        assert score([clean_artifact], document, claims)["unplanted_error_bodies"] == 0

    def test_an_unplanted_error_says_which_rule_fired(self, document, claims):
        # Recall was per rule from the start. This direction was only a body count,
        # so when v4 and v6 differed by two bodies out of thirty there was nothing
        # on disk to say which rule moved.
        noisy = Artifact(target="linkedin", body="Adyen Holdings said so.", claim_ids=[])
        result = score([noisy], document, claims)
        assert result["unplanted_error_bodies"] == 1
        assert result["unplanted_errors_by_rule"] == {"entity": 1}
        assert result["unplanted_errors"][0]["rules"] == ["entity"]

    def test_a_rule_firing_twice_in_one_body_counts_once(self, document, claims):
        # Kept commensurable with unplanted_error_bodies, which is a body count.
        twice = Artifact(
            target="linkedin", body="Adyen Holdings and Stripe Payments said so.", claim_ids=[]
        )
        result = score([twice], document, claims)
        assert result["unplanted_errors_by_rule"] == {"entity": 1}

    def test_copying_is_not_scored_as_an_unplanted_fabrication_error(self, document, claims):
        # Copying became an error once v6 confirmed the rate, which put it in front
        # of this scorer. It is never planted, and a body reproducing the source
        # really did reproduce it. Counting that here would make the false-positive
        # rate rise every time the copying rule did its job.
        copied = Artifact(target="linkedin", body=SOURCE.strip(), claim_ids=[])
        # Vacuous unless the rule actually fires on this body.
        fired = verify(copied, document, claims).errors
        assert [w.rule for w in fired] == ["copying"]
        result = score([copied], document, claims)
        assert result["unplanted_error_bodies"] == 0

    def test_credit_needs_the_right_rule_to_fire(self, document, claims):
        # A body whose entity rule fires for an unrelated reason must not be scored
        # as having caught the number injection.
        noisy = Artifact(target="linkedin", body="Adyen Holdings said so about it.", claim_ids=[])
        result = score([noisy], document, claims)
        assert result["recall_by_rule"]["number"] == 1.0
        assert result["unplanted_error_bodies"] == 1


class TestJudgeSentences:
    def test_headings_and_hashtags_are_not_claims(self):
        body = "## Why this matters\n\nMerchant names come from the processor.\n\n#banking #fintech"
        assert sentences_of(body) == ["Merchant names come from the processor."]

    def test_subject_line_is_judged_but_its_label_is_stripped(self):
        assert sentences_of("Subject: Dates drift and nobody tells you") == [
            "Dates drift and nobody tells you"
        ]

    def test_preview_line_is_dropped(self):
        assert sentences_of("Preview: something short here about it") == []

    def test_fragments_are_not_judged(self):
        assert sentences_of("Yes. No. Maybe so.") == []

    def test_sentences_split_on_terminators(self):
        body = "The first claim goes here. The second claim goes there."
        assert len(sentences_of(body)) == 2


class TestJudge:
    def _reply(self, verdicts):
        return json.dumps({"verdicts": verdicts})

    def test_rate_counts_only_checkable_sentences(self, document):
        body = "Merchant names come from the processor. Does that surprise anyone here?"
        llm = ScriptedLLM(
            [
                self._reply(
                    [
                        {"n": 1, "verdict": "supported", "why": "s1"},
                        {"n": 2, "verdict": "not_a_claim", "why": "a question"},
                    ]
                )
            ]
        )
        result = judge(body, document, llm)
        assert result["sentences"] == 2
        assert result["claims"] == 1
        assert result["rate"] == 0.0

    def test_unsupported_sentences_drive_the_rate(self, document):
        body = "Merchant names come from the processor. Refunds rose sharply last quarter."
        llm = ScriptedLLM(
            [
                self._reply(
                    [
                        {"n": 1, "verdict": "supported", "why": "s1"},
                        {"n": 2, "verdict": "unsupported", "why": "not in source"},
                    ]
                )
            ]
        )
        assert judge(body, document, llm)["rate"] == 0.5

    def test_a_sentence_the_judge_skipped_is_not_counted_as_fine(self, document):
        body = "Merchant names come from the processor. Refunds rose sharply last quarter."
        llm = ScriptedLLM([self._reply([{"n": 1, "verdict": "supported", "why": "s1"}])])
        result = judge(body, document, llm)
        assert result["unjudged"] == 1
        assert result["claims"] == 1

    def test_empty_output_has_no_rate_rather_than_a_zero(self, document):
        assert judge("## Just a heading", document, ScriptedLLM([]))["rate"] is None


class TestAbandonmentRule:
    def _result(self, source_id, judged, unsupported):
        return {
            "id": source_id,
            "targets": ["linkedin"],
            "format_violations": {},
            "claim_utilisation": 0.5,
            "judged_claims": judged,
            "judged_unsupported": unsupported,
            "injections": {
                "planted": 5,
                "caught": 5,
                "clean_bodies": 1,
                "unplanted_error_bodies": 0,
                "recall_by_rule": {"number": 1.0},
            },
        }

    def test_a_complete_run_publishes_the_rate(self):
        report = aggregate([self._result("a", 10, 1), self._result("b", 10, 1)], [], {})
        assert report["complete"] is True
        assert report["unsupported_claim_rate"] == pytest.approx(0.1)

    def test_one_failure_withholds_the_headline_number(self):
        report = aggregate([self._result("a", 10, 1)], [{"id": "b", "error": "429"}], {})
        assert report["complete"] is False
        assert report["unsupported_claim_rate"] is None
        # The provisional figure is kept, clearly labelled, so a failed run is still
        # diagnosable without becoming quotable.
        assert report["unsupported_claim_rate_provisional"] == pytest.approx(0.1)

    def test_secondary_metrics_still_aggregate(self):
        report = aggregate([self._result("a", 10, 1)], [], {})
        assert report["verifier_recall_on_injections"] == 1.0
        assert report["unplanted_error_rate"] == 0.0
        assert report["format_compliance"] == 1.0


class TestOneInstrument:
    """A rate must come from one measurement, not two.

    Window size moves the rate, because support is existential per window: a
    sentence needing two distant paragraphs is supported when one window holds
    both and not when none does. Halving the window to fit a nearly spent token
    budget would have judged one source of a run on a different instrument from
    the other fourteen, and nothing in the output would have shown it.
    """

    def _result(self, source_id, window_chars, judged=10, unsupported=1):
        return {
            "id": source_id,
            "judged_by_model": True,
            "targets": ["linkedin"],
            "format_violations": {},
            "claim_utilisation": 0.5,
            "judged_claims": judged,
            "judged_unsupported": unsupported,
            "judged": {"linkedin": {"claims": judged, "unsupported": unsupported,
                                    "window_chars": window_chars}},
            "injections": {"planted": 5, "caught": 5, "clean_bodies": 1,
                           "unplanted_error_bodies": 0, "recall_by_rule": {"number": 1.0}},
        }

    def test_one_window_size_publishes_a_rate(self):
        report = aggregate([self._result("a", 12000), self._result("b", 12000)], [], {})
        assert report["one_instrument"] is True
        assert report["unsupported_claim_rate"] == pytest.approx(0.1)

    def test_two_window_sizes_withhold_the_rate(self):
        report = aggregate([self._result("a", 12000), self._result("b", 6000)], [], {})
        assert report["one_instrument"] is False
        assert report["unsupported_claim_rate"] is None

    def test_the_sizes_used_are_recorded_so_the_mix_is_visible(self):
        report = aggregate([self._result("a", 12000), self._result("b", 6000)], [], {})
        assert report["judge_window_chars"] == [6000, 12000]

    def test_older_results_without_the_field_do_not_trip_it(self):
        # v1 and v2 were judged before the field existed. An absent size is
        # unknown, not a second instrument.
        old = self._result("a", 12000)
        del old["judged"]["linkedin"]["window_chars"]
        report = aggregate([old, self._result("b", 12000)], [], {})
        assert report["one_instrument"] is True
        assert report["unsupported_claim_rate"] is not None


class TestUnjudgedRun:
    """--no-judge measures the deterministic layer and must claim nothing more.

    The danger is not that the number is wrong, it is that a run which never asked
    a judge anything still writes a report shaped exactly like one that did.
    """

    def _result(self, source_id, judged_by_model, judged=10, unsupported=1):
        return {
            "id": source_id,
            "judged_by_model": judged_by_model,
            "targets": ["linkedin"],
            "format_violations": {},
            "claim_utilisation": 0.5,
            "judged_claims": judged,
            "judged_unsupported": unsupported,
            "injections": {
                "planted": 5,
                "caught": 5,
                "clean_bodies": 1,
                "unplanted_error_bodies": 0,
                "recall_by_rule": {"number": 1.0},
            },
        }

    def test_a_run_without_the_judge_publishes_no_rate(self):
        report = aggregate([self._result("a", False, judged=0, unsupported=0)], [], {})
        assert report["judged_by_model"] is False
        assert report["unsupported_claim_rate"] is None
        assert report["unsupported_claim_rate_provisional"] is None

    def test_stale_judged_numbers_cannot_leak_into_an_unjudged_run(self):
        # A checkpoint carrying judged counts from an earlier run must not give an
        # unjudged run a headline it did not measure.
        report = aggregate([self._result("a", False, judged=10, unsupported=1)], [], {})
        assert report["unsupported_claim_rate"] is None
        assert report["unsupported_claim_rate_provisional"] is None

    def test_one_unjudged_source_withholds_the_whole_rate(self):
        results = [self._result("a", True), self._result("b", False)]
        assert aggregate(results, [], {})["unsupported_claim_rate"] is None

    def test_the_deterministic_scores_survive(self):
        report = aggregate([self._result("a", False, judged=0, unsupported=0)], [], {})
        assert report["verifier_recall_on_injections"] == 1.0
        assert report["format_compliance"] == 1.0
        assert report["claim_utilisation"] == 0.5

    def test_an_ordinary_run_is_still_marked_judged(self):
        report = aggregate([self._result("a", True)], [], {})
        assert report["judged_by_model"] is True
        assert report["unsupported_claim_rate"] == pytest.approx(0.1)


class TestJudgeWindowing:
    """A source too large for one request, judged without changing the metric.

    Support is existential over the source, so a sentence backed by material in
    the last window must come out supported. Getting that wrong would not throw:
    it would quietly report supported sentences as unsupported and inflate the
    headline rate, which is the number the product is sold on.
    """

    @pytest.fixture
    def wide(self, monkeypatch):
        import judge as judge_mod

        from recut.ingest.markdown import ingest_text

        monkeypatch.setattr(judge_mod, "JUDGE_WINDOW_CHARS", 60)
        return ingest_text(
            "# Wide" + "\n\n" + ("\n\n").join(
                f"Paragraph {i} says something about topic {i}." * 2 for i in range(6)
            ),
            source_ref="wide",
        )

    def _reply(self, verdicts):
        return json.dumps({"verdicts": verdicts})

    def test_the_source_is_actually_split(self, wide):
        import judge as judge_mod

        from recut.extract import windows

        assert len(windows(wide, judge_mod.JUDGE_WINDOW_CHARS)) > 1

    def test_a_sentence_supported_only_by_a_later_window_is_supported(self, wide):
        body = "Something here is asserted about the sixth topic plainly."
        llm = ScriptedLLM(
            [
                self._reply([{"n": 1, "verdict": "unsupported", "why": "not in this part"}]),
                self._reply([{"n": 1, "verdict": "supported", "why": "found it here"}]),
            ]
        )
        result = judge(body, wide, llm)
        assert result["unsupported"] == 0
        assert result["claims"] == 1

    def test_a_supported_verdict_is_never_downgraded(self, wide):
        body = "Something here is asserted about the second topic plainly."
        llm = ScriptedLLM(
            [
                self._reply([{"n": 1, "verdict": "supported", "why": "found"}]),
                self._reply([{"n": 1, "verdict": "unsupported", "why": "not in this part"}]),
            ]
        )
        assert judge(body, wide, llm)["unsupported"] == 0

    def test_a_settled_sentence_is_not_asked_about_again(self, wide):
        # The second window has nothing left to resolve, so no call is made and
        # the scripted reply goes unused.
        body = "Something here is asserted about the third topic plainly."
        llm = ScriptedLLM([self._reply([{"n": 1, "verdict": "supported", "why": "found"}])])
        result = judge(body, wide, llm)
        assert result["claims"] == 1
        assert result["unsupported"] == 0

    def test_not_a_claim_is_settled_once_and_not_revisited(self, wide):
        body = "Would anyone here disagree with that at all?"
        llm = ScriptedLLM([self._reply([{"n": 1, "verdict": "not_a_claim", "why": "a question"}])])
        result = judge(body, wide, llm)
        # Excluded from the denominator, and never re-asked against later windows
        # where a different answer would make the denominator depend on where the
        # boundaries fell.
        assert result["claims"] == 0
        assert result["sentences"] == 1

    def test_a_sentence_no_window_supports_stays_unsupported(self, wide):
        body = "Revenue tripled in the final quarter of the year."
        llm = ScriptedLLM(
            [self._reply([{"n": 1, "verdict": "unsupported", "why": "no"}]) for _ in range(8)]
        )
        assert judge(body, wide, llm)["unsupported"] == 1

    def test_the_window_count_is_reported(self, wide):
        llm = ScriptedLLM(
            [self._reply([{"n": 1, "verdict": "supported", "why": "y"}]) for _ in range(8)]
        )
        assert judge("Something about the first topic is asserted.", wide, llm)["windows"] > 1


class TestJudgeBatching:
    """A verdict list long enough to be truncated, asked for in pieces.

    Groq cut a reply off mid-object on the largest source and the repair retry
    hit the same ceiling, because no rewording makes a reply shorter. Splitting
    the sentence list cannot change the metric the way splitting the source
    could: verdicts are independent per sentence.
    """

    @pytest.fixture
    def small_batch(self, monkeypatch):
        import judge as judge_mod

        monkeypatch.setattr(judge_mod, "JUDGE_SENTENCES_PER_CALL", 3)

    @pytest.fixture
    def doc(self):
        from recut.ingest.markdown import ingest_text

        return ingest_text("# S" + chr(10) + chr(10) + "The source says a thing about it.", source_ref="s")

    def _body(self, n):
        return " ".join(f"Sentence number {i} asserts something checkable here." for i in range(1, n + 1))

    def test_every_sentence_is_judged_across_batches(self, doc, small_batch):
        # Seven sentences at three per call: three calls, none skipped.
        replies = [
            json.dumps({"verdicts": [{"n": n, "verdict": "supported", "why": "y"} for n in ns]})
            for ns in ([1, 2, 3], [4, 5, 6], [7])
        ]
        result = judge(self._body(7), doc, ScriptedLLM(replies))
        assert result["sentences"] == 7
        assert result["claims"] == 7
        assert result["unjudged"] == 0

    def test_a_verdict_for_a_sentence_outside_the_batch_is_ignored(self, doc, small_batch):
        # A judge that renumbers or hallucinates an index must not have it
        # applied to whatever sentence happens to hold that number.
        replies = [
            json.dumps({"verdicts": [
                {"n": 1, "verdict": "supported", "why": "y"},
                {"n": 2, "verdict": "supported", "why": "y"},
                {"n": 3, "verdict": "supported", "why": "y"},
                {"n": 99, "verdict": "unsupported", "why": "not asked about"},
            ]}),
            json.dumps({"verdicts": [{"n": 4, "verdict": "unsupported", "why": "n"}]}),
        ]
        result = judge(self._body(4), doc, ScriptedLLM(replies))
        assert result["claims"] == 4
        assert result["unsupported"] == 1

    def test_batching_gives_the_same_answer_as_one_call(self, doc, monkeypatch):
        import judge as judge_mod

        body = self._body(6)
        verdicts = [{"n": n, "verdict": "supported" if n % 2 else "unsupported", "why": "y"}
                    for n in range(1, 7)]

        monkeypatch.setattr(judge_mod, "JUDGE_SENTENCES_PER_CALL", 100)
        whole = judge(body, doc, ScriptedLLM([json.dumps({"verdicts": verdicts})]))

        monkeypatch.setattr(judge_mod, "JUDGE_SENTENCES_PER_CALL", 2)
        split = judge(body, doc, ScriptedLLM([
            json.dumps({"verdicts": verdicts[0:2]}),
            json.dumps({"verdicts": verdicts[2:4]}),
            json.dumps({"verdicts": verdicts[4:6]}),
        ]))
        assert (whole["claims"], whole["unsupported"]) == (split["claims"], split["unsupported"])

    def test_a_sentence_no_batch_answered_is_not_counted_as_fine(self, doc, small_batch):
        replies = [json.dumps({"verdicts": [{"n": 1, "verdict": "supported", "why": "y"}]})]
        result = judge(self._body(3), doc, ScriptedLLM(replies))
        assert result["unjudged"] == 2
        assert result["claims"] == 1


class TestJudgeResume:
    """Progress through a large source survives a quota wall.

    art-ocr needs about 10k tokens across its windows. Four attempts each spent
    one to two thousand of exactly that budget, recorded nothing, and left the
    next attempt further away. The risk in fixing it is worse than the bug: a
    resume that attaches an old verdict to a new sentence corrupts the number
    instead of failing.
    """

    @pytest.fixture
    def wide(self, monkeypatch):
        import judge as judge_mod

        from recut.ingest.markdown import ingest_text

        monkeypatch.setattr(judge_mod, "JUDGE_WINDOW_CHARS", 60)
        return ingest_text(
            "# Wide" + "\n\n" + ("\n\n").join(
                f"Paragraph {i} says something about topic {i}." * 2 for i in range(6)
            ),
            source_ref="wide",
        )

    def _reply(self, verdicts):
        return json.dumps({"verdicts": verdicts})

    def test_a_completed_window_is_written_to_the_cache(self, wide, tmp_path):
        cache = tmp_path / "p.json"
        body = "Something here is asserted about the topics plainly."
        llm = ScriptedLLM([self._reply([{"n": 1, "verdict": "supported", "why": "y"}])])
        judge(body, wide, llm, cache=cache)
        assert cache.exists()
        assert json.loads(cache.read_text(encoding="utf-8"))["next_window"] >= 1

    def test_a_second_attempt_does_not_re_ask_a_settled_sentence(self, wide, tmp_path):
        cache = tmp_path / "p.json"
        body = "Something here is asserted about the topics plainly."
        judge(body, wide, ScriptedLLM([self._reply([{"n": 1, "verdict": "supported", "why": "y"}])]), cache=cache)
        # No scripted replies at all: if it asked anything, this would raise.
        again = judge(body, wide, ScriptedLLM([]), cache=cache)
        assert again["claims"] == 1
        assert again["unsupported"] == 0

    def test_a_changed_body_discards_the_saved_progress(self, wide, tmp_path):
        cache = tmp_path / "p.json"
        judge("A first body asserting one thing here.", wide,
              ScriptedLLM([self._reply([{"n": 1, "verdict": "supported", "why": "y"}])]), cache=cache)
        # Different body, same cache file. Reusing verdict index 1 would attach
        # the old answer to a different sentence.
        # Unresumed, so it walks every window; an unsupported sentence is
        # re-asked against each one, which is the existential rule working.
        result = judge("A different body asserting another thing.", wide,
                       ScriptedLLM([self._reply([{"n": 1, "verdict": "unsupported", "why": "n"}])
                                    for _ in range(12)]),
                       cache=cache)
        assert result["unsupported"] == 1

    def test_unreadable_progress_is_treated_as_none(self, wide, tmp_path):
        cache = tmp_path / "p.json"
        cache.write_text("{ truncated", encoding="utf-8")
        body = "Something here is asserted about the topics plainly."
        result = judge(body, wide, ScriptedLLM([self._reply([{"n": 1, "verdict": "supported", "why": "y"}])]), cache=cache)
        assert result["claims"] == 1

    def test_no_cache_path_means_no_files_and_no_resume(self, wide, tmp_path):
        body = "Something here is asserted about the topics plainly."
        judge(body, wide, ScriptedLLM([self._reply([{"n": 1, "verdict": "supported", "why": "y"}])]))
        assert list(tmp_path.iterdir()) == []


class TestJudgeSeparation:
    """Nothing grades its own work, and the eval can still run when a quota goes.

    Full provider separation was tried and reverted: pinning generators to
    Gemini alone cost them their fallback and a fresh run failed 15 of 15
    sources at extraction when Gemini's daily budget went. What is guaranteed
    now is that the two never share a model, both sides pinned by name. These
    tests check which model is actually reached, not which flag was set.
    """

    def _recording(self, llm, monkeypatch):
        reached = []
        monkeypatch.setattr(
            type(llm), "_gemini",
            lambda self, c, model, prompt, as_json: reached.append(("gemini", model)) or "{}",
        )
        monkeypatch.setattr(
            type(llm), "_groq",
            lambda self, c, prompt, as_json: reached.append(("groq", self.groq_model)) or "{}",
        )
        return reached

    def _reachable(self, llm, monkeypatch):
        """Every model this client would try, by exhausting each in turn."""
        import httpx

        seen = []

        def dead_gemini(self, c, model, prompt, as_json):
            seen.append(("gemini", model))
            raise httpx.HTTPStatusError(
                "429", request=httpx.Request("POST", "https://x"),
                response=httpx.Response(429, text="quota", request=httpx.Request("POST", "https://x")),
            )

        def dead_groq(self, c, prompt, as_json):
            seen.append(("groq", self.groq_model))
            raise httpx.HTTPStatusError(
                "429", request=httpx.Request("POST", "https://x"),
                response=httpx.Response(429, text="quota", request=httpx.Request("POST", "https://x")),
            )

        monkeypatch.setattr(type(llm), "_gemini", dead_gemini)
        monkeypatch.setattr(type(llm), "_groq", dead_groq)
        monkeypatch.setattr("recut.llm.time.sleep", lambda *_: None)
        monkeypatch.setattr(llm, "max_retries", 1)
        from recut.llm import LLMError

        with pytest.raises(LLMError):
            llm.text("x")
        return set(seen)

    def test_the_judge_never_reaches_gemini_even_holding_a_gemini_key(self, monkeypatch):
        from judge import JUDGE_GROQ_MODEL, judge_client

        llm = judge_client(gemini_key="g", groq_key="q")
        reached = self._recording(llm, monkeypatch)
        llm.text("anything")
        assert reached == [("groq", JUDGE_GROQ_MODEL)]

    def test_the_generators_keep_a_fallback_when_gemini_is_gone(self, monkeypatch):
        # The regression this exists for: without it, one exhausted Gemini
        # budget takes out every source at extraction before the judge runs.
        from run_eval import GENERATOR_GROQ_MODEL

        from recut.llm import LLM

        llm = LLM(gemini_key="g", groq_key="q", groq_model=GENERATOR_GROQ_MODEL)
        assert ("groq", GENERATOR_GROQ_MODEL) in self._reachable(llm, monkeypatch)

    def test_the_generators_and_the_judge_share_no_model(self, monkeypatch):
        from judge import JUDGE_GROQ_MODEL, judge_client
        from run_eval import GENERATOR_GROQ_MODEL

        from recut.llm import LLM

        generator = LLM(gemini_key="g", groq_key="q", groq_model=GENERATOR_GROQ_MODEL)
        judge_llm = judge_client(gemini_key="g", groq_key="q")
        assert self._reachable(generator, monkeypatch) & self._reachable(
            judge_llm, monkeypatch
        ) == set()

    def test_this_project_does_not_share_a_model_with_reruns(self):
        """Free-tier quota is per model, so a shared model is a shared budget.

        The sibling `reruns` benchmark runs a daily measurement against these.
        Sharing one meant a judge probed alive and was spent minutes later
        because something else had been through it, which cost two days of
        misdiagnosis. Hard-coded rather than read from that repo: this must fail
        when someone widens recut's ladder, not when reruns happens to be
        checked out.
        """
        from judge import JUDGE_GROQ_MODEL
        from run_eval import GENERATOR_GROQ_MODEL

        from recut.llm import GEMINI_MODELS, GROQ_MODEL

        reruns = {
            "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
            "openai/gpt-oss-20b",
        }
        ours = {*GEMINI_MODELS, GROQ_MODEL, GENERATOR_GROQ_MODEL, JUDGE_GROQ_MODEL}
        assert ours & reruns == set()

    def test_the_two_pins_are_not_the_same_string(self):
        # run_eval refuses to start if these ever converge. Asserting it here
        # means a rename cannot make that check vacuously true.
        from judge import JUDGE_GROQ_MODEL
        from run_eval import GENERATOR_GROQ_MODEL

        assert GENERATOR_GROQ_MODEL != JUDGE_GROQ_MODEL

    def test_a_client_with_no_enabled_provider_is_refused_at_construction(self):
        from recut.llm import LLM, LLMError

        # Otherwise this surfaces later as "every provider failed", which reads
        # like a quota wall and is a configuration mistake.
        with pytest.raises(LLMError):
            LLM(gemini_key="g", use_gemini=False, allow_env=False)


class TestGoldenSet:
    def test_every_source_has_an_id_kind_and_ref(self):
        for entry in load_sources():
            assert entry["id"] and entry["kind"] in {"markdown", "article", "youtube"}
            assert entry["ref"]

    def test_ids_are_unique(self):
        ids = [e["id"] for e in load_sources()]
        assert len(ids) == len(set(ids))

    def test_local_refs_are_resolved_to_absolute_paths(self):
        local = [e for e in load_sources() if not e["ref"].startswith("http")]
        assert local and all(Path(e["ref"]).is_absolute() for e in local)


def test_fresh_clears_a_previous_attempts_files(tmp_path):
    """--fresh regenerates everything, so the old attempt's files must not survive.

    v6 finished 15/15 sitting next to 15 tracebacks from the run that hit Groq's
    per-day wall half an hour earlier, and only report.json said which was true.
    """
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "md-vidsmith.json").write_text("{}", encoding="utf-8")
    (sources / "md-vidsmith.error.txt").write_text("Traceback", encoding="utf-8")
    (sources / "art-ocr.error.txt").write_text("Traceback", encoding="utf-8")

    assert clear_stale(sources) == 3
    assert list(sources.iterdir()) == []


def _result(detail):
    return {
        "judged_by_model": False, "judged_claims": 0, "judged_unsupported": 0,
        "claim_utilisation": None, "targets": ["linkedin"], "format_violations": {},
        "repair_detail": detail,
        "injections": {"planted": 0, "caught": 0, "clean_bodies": 0,
                       "unplanted_error_bodies": 0, "recall_by_rule": {}},
    }


def test_a_kept_repair_that_only_tied_is_not_counted_as_reducing():
    """kept and reduced are different questions and the gap is the tie-break.

    A tie goes to the repair, so kept can exceed reduced without anything being
    wrong. Reporting only kept would make the retry look more useful than it is.
    """
    detail = {"linkedin": {
        "kept": True,
        "first_pass_errors": ["[error/quote] 'a': x"],
        "repair_errors": ["[error/intensity] 'b': y"],
        "spans_changed": ["[quote] a"],
    }}
    rep = aggregate([_result(detail)], [], {"at": "x"})["repair"]
    assert rep["attempted"] == 1
    assert rep["kept"] == 1
    assert rep["reduced_error_count"] == 0
    assert rep["fully_cleared"] == 0


def test_a_drifted_source_is_named_in_the_report():
    """The terminal warned and report.json did not, so a rate whose sources had
    moved read as clean forever after. v6-rejudged was judged with md-reruns
    +2464 chars and md-vidsmith +1952 and nothing in the file said so."""
    a = _result({}); a["id"] = "md-reruns"; a["judged_claims"] = 16
    a["rejudged"] = {"source_char_drift": 2464}
    b = _result({}); b["id"] = "art-ocr"; b["judged_claims"] = 20
    b["rejudged"] = {"source_char_drift": 0}
    report = aggregate([a, b], [], {"at": "x"})
    assert report["sources_drifted"] == {"md-reruns": 2464}
    # The claims at risk, not just the source count: that is what bounds the rate.
    assert report["drifted_claims"] == 16


def test_a_live_run_has_no_drift_because_it_never_rejudged():
    # run_source writes no "rejudged" key at all, so the field must not assume one.
    report = aggregate([_result({})], [], {"at": "x"})
    assert report["sources_drifted"] == {}
    assert report["drifted_claims"] == 0


def test_drift_withholds_the_headline():
    """A drifted source was judged against text that did not produce the bodies.

    The rate is then partly measuring a source nobody generated from, which is a
    different instrument in the same way a mixed judge window is. This was recorded
    but not enforced for one day; enforcing it is the decision.
    """
    a = _result({}); a["id"] = "md-reruns"; a["judged_claims"] = 16
    a["judged_by_model"] = True
    a["rejudged"] = {"source_char_drift": 2464}
    report = aggregate([a], [], {"at": "x"})
    assert report["sources_drifted"]
    assert report["unsupported_claim_rate"] is None


def test_drift_keeps_the_provisional_figure():
    """Withheld, not destroyed. The number is not wrong so much as not comparable,
    and discarding it would cost more than labelling it does."""
    a = _result({}); a["id"] = "md-reruns"; a["judged_claims"] = 16
    a["judged_unsupported"] = 2
    a["judged_by_model"] = True
    a["rejudged"] = {"source_char_drift": 2464}
    report = aggregate([a], [], {"at": "x"})
    assert report["unsupported_claim_rate"] is None
    assert report["unsupported_claim_rate_provisional"] == 2 / 16


def test_an_undrifted_complete_run_still_publishes(self=None):
    a = _result({}); a["id"] = "md-geojit"; a["judged_claims"] = 16
    a["judged_unsupported"] = 1
    a["judged_by_model"] = True
    a["rejudged"] = {"source_char_drift": 0}
    report = aggregate([a], [], {"at": "x"})
    assert report["unsupported_claim_rate"] == 1 / 16


def test_the_inventory_counters_are_summed_across_sources():
    a = _result({}); a["dropped_unanchored"] = 2; a["demoted_quotes"] = 1
    b = _result({}); b["dropped_unanchored"] = 0; b["demoted_quotes"] = 3
    report = aggregate([a, b], [], {"at": "x"})
    assert report["dropped_unanchored"] == 2
    assert report["demoted_quotes"] == 4


def test_a_checkpoint_without_the_counters_is_read_as_zero():
    # Runs before these existed carry neither key, and resuming across a code
    # change is the normal case in this harness.
    report = aggregate([_result({})], [], {"at": "x"})
    assert report["dropped_unanchored"] == 0
    assert report["demoted_quotes"] == 0


def test_repair_effectiveness_is_counted_per_error_not_per_body():
    """A draft with two invented names and one copied run is three things to fix.

    Counting per body would score the repair as having failed on that draft even
    if it removed both names, and would hide that entity clears at 73% while
    copying clears at 17%. An overall rate averages two different mechanisms.
    """
    detail = {"linkedin": {
        "kept": True,
        "first_pass_errors": [
            "[error/entity] 'A': x", "[error/entity] 'B': x", "[error/copying] 'C': x",
        ],
        "repair_errors": ["[error/copying] 'C reworded': x"],
        "spans_changed": [],
    }}
    rep = aggregate([_result(detail)], [], {"at": "x"})["repair"]
    assert rep["cleared_by_rule"]["entity"] == {"seen": 2, "cleared": 2, "rate": 1.0}
    assert rep["cleared_by_rule"]["copying"] == {"seen": 1, "cleared": 0, "rate": 0.0}
    assert rep["still_failing_by_rule"] == {"copying": 1}


def test_a_rule_that_survives_its_own_repair_is_named():
    # The question copying-as-an-error raises: is the retry spending a call to
    # produce the same copied run?
    detail = {"linkedin": {
        "kept": False,
        "first_pass_errors": ["[error/copying] 'x': 12 words", "[error/entity] 'y': z"],
        "repair_errors": ["[error/copying] 'x reworded': 12 words"],
        "spans_changed": [],
    }}
    rep = aggregate([_result(detail)], [], {"at": "x"})["repair"]
    assert rep["still_failing_by_rule"] == {"copying": 1}
    assert rep["reduced_error_count"] == 1
    assert rep["fully_cleared"] == 0


def test_a_run_with_no_repairs_reports_zero_not_none():
    rep = aggregate([_result({})], [], {"at": "x"})["repair"]
    assert rep["attempted"] == 0
    assert rep["reduced_rate"] is None


class TestInventoryCache:
    """Extraction was 49 of v11's 98 calls and depends on almost nothing that a
    normal iteration changes. The cache is only safe if it invalidates on the
    things it does depend on, so those are the tests."""

    def _key(self, **kw):
        import run_eval

        return run_eval._inventory_key(kw.get("ref", "r"), kw.get("models", ["m1"]))

    def test_the_same_inputs_give_the_same_key(self):
        assert self._key() == self._key()

    def test_a_different_source_is_a_different_key(self):
        assert self._key(ref="a") != self._key(ref="b")

    def test_a_different_model_ladder_is_a_different_key(self):
        # An inventory from another model is another instrument, the same argument
        # the judge's comparability rule makes.
        assert self._key(models=["m1"]) != self._key(models=["m2"])

    def test_editing_the_extraction_prompt_invalidates_every_entry(self, tmp_path, monkeypatch):
        import run_eval
        from recut import llm as llm_mod

        prompts = tmp_path / "prompts"
        prompts.mkdir()
        (prompts / "extract.md").write_text("first", encoding="utf-8")
        (prompts / "_faithfulness.md").write_text("shared", encoding="utf-8")
        monkeypatch.setattr(run_eval, "PROMPTS", prompts)
        before = run_eval._inventory_key("r", ["m1"])
        (prompts / "extract.md").write_text("second", encoding="utf-8")
        after = run_eval._inventory_key("r", ["m1"])
        assert before != after, "editing extract.md must not reuse an old inventory"

    def test_the_shared_faithfulness_block_is_in_the_key_too(self, tmp_path, monkeypatch):
        import run_eval

        prompts = tmp_path / "prompts"
        prompts.mkdir()
        (prompts / "extract.md").write_text("same", encoding="utf-8")
        (prompts / "_faithfulness.md").write_text("first", encoding="utf-8")
        monkeypatch.setattr(run_eval, "PROMPTS", prompts)
        before = run_eval._inventory_key("r", ["m1"])
        (prompts / "_faithfulness.md").write_text("second", encoding="utf-8")
        assert before != run_eval._inventory_key("r", ["m1"])


def test_fresh_only_clears_the_sources_the_run_will_rewrite(tmp_path):
    """--fresh --only art-willison must not destroy the other fourteen.

    It did. clear_stale ran before --only was parsed and took no filter, so
    refreshing one source deleted every stored body in the run: the bodies
    rejudge.py and measure_copying read, and which cost a full run to produce.
    """
    sources = tmp_path / "sources"
    sources.mkdir()
    for name in ("art-willison", "md-citerag", "yt-rome"):
        (sources / f"{name}.json").write_text("{}", encoding="utf-8")
    (sources / "art-willison.error.txt").write_text("Traceback", encoding="utf-8")

    assert clear_stale(sources, {"art-willison"}) == 2
    assert sorted(p.name for p in sources.iterdir()) == ["md-citerag.json", "yt-rome.json"]


def test_a_hyphenated_source_id_is_matched_whole(tmp_path):
    # Ids carry hyphens and the two file names carry different suffixes, so the
    # id is the part before the first dot in both cases.
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "art-bank-statement.json").write_text("{}", encoding="utf-8")
    (sources / "art-bank-statement.error.txt").write_text("t", encoding="utf-8")
    (sources / "art-double-entry.json").write_text("{}", encoding="utf-8")

    assert clear_stale(sources, {"art-bank-statement"}) == 2
    assert [p.name for p in sources.iterdir()] == ["art-double-entry.json"]


def test_no_filter_still_clears_everything(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    for name in ("a", "b"):
        (sources / f"{name}.json").write_text("{}", encoding="utf-8")
    assert clear_stale(sources) == 2
    assert list(sources.iterdir()) == []


def test_fresh_keeps_the_per_window_crumbs(tmp_path):
    """partial/ is what makes a walled run resumable. --fresh must not touch it."""
    sources = tmp_path / "sources"
    (sources / "partial").mkdir(parents=True)
    crumb = sources / "partial" / "art-ocr.w3.json"
    crumb.write_text("{}", encoding="utf-8")
    (sources / "art-ocr.error.txt").write_text("Traceback", encoding="utf-8")

    assert clear_stale(sources) == 1
    assert crumb.exists()


def test_clearing_an_empty_directory_is_not_an_event(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    assert clear_stale(sources) == 0


class TestJudgeVariance:
    """Same bodies, same judge, twice. Whatever differs is the instrument.

    The only repeat that exists happened by accident: v6 md-vidsmith judged 1/20
    and then 2/20 by openai/gpt-oss-120b on the same stored body.
    """

    def test_a_judge_that_reproduces_itself_reports_no_movement(self):
        a = {"md-n8n": (3, 15), "art-ocr": (0, 16)}
        result = compare(a, dict(a))
        assert result["claims_that_moved"] == 0
        assert result["sources_that_moved"] == 0
        assert result["first_rate"] == result["second_rate"]

    def test_the_md_vidsmith_case_is_counted_as_one_claim_moving(self):
        result = compare({"md-vidsmith": (1, 20)}, {"md-vidsmith": (2, 20)})
        assert result["claims_that_moved"] == 1
        assert result["sources_that_moved"] == 1
        assert result["first_rate"] == 0.05 and result["second_rate"] == 0.10

    def test_movement_in_both_directions_is_counted_not_cancelled(self):
        # Two sources moving opposite ways leave the rate untouched while the judge
        # has in fact disagreed with itself twice. Summing the signed deltas would
        # report a stable instrument.
        result = compare(
            {"a": (2, 10), "b": (2, 10)}, {"a": (3, 10), "b": (1, 10)}
        )
        assert result["claims_that_moved"] == 2
        assert result["first_rate"] == result["second_rate"]

    def test_a_changed_denominator_is_flagged_separately(self):
        # The passes did not agree on what was even checkable, which is a bigger
        # disagreement than the verdicts.
        result = compare({"md-citerag": (2, 16)}, {"md-citerag": (2, 18)})
        assert result["mismatched_denominators"] == ["md-citerag"]

    def test_only_sources_judged_in_both_are_compared(self):
        result = compare({"a": (1, 10), "b": (1, 10)}, {"a": (1, 10)})
        assert result["sources"] == 1
