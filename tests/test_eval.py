"""The eval harness itself, offline. A metric nobody tested is a rumour."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from inject import plant, score  # noqa: E402
from judge import judge, sentences_of  # noqa: E402
from run_eval import aggregate, load_sources  # noqa: E402

from recut.ingest.markdown import ingest_text  # noqa: E402
from recut.models import Artifact, Claim, ClaimSet  # noqa: E402

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

    def test_a_clean_body_produces_no_false_positive(self, clean_artifact, document, claims):
        assert score([clean_artifact], document, claims)["false_positive_bodies"] == 0

    def test_credit_needs_the_right_rule_to_fire(self, document, claims):
        # A body whose entity rule fires for an unrelated reason must not be scored
        # as having caught the number injection.
        noisy = Artifact(target="linkedin", body="Adyen Holdings said so about it.", claim_ids=[])
        result = score([noisy], document, claims)
        assert result["recall_by_rule"]["number"] == 1.0
        assert result["false_positive_bodies"] == 1


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
                "false_positive_bodies": 0,
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
        assert report["verifier_false_positive_rate"] == 0.0
        assert report["format_compliance"] == 1.0


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
                "false_positive_bodies": 0,
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
