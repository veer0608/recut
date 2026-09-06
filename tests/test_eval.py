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
