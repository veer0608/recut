"""The whole loop with a scripted model. No network, no key, no cost."""

import json

import pytest

from recut.extract import extract, render_claims, render_source, windows
from recut.ingest.markdown import ingest_text
from recut.models import Artifact, ClaimSet, Document
from recut.pipeline import repurpose, write_out

SOURCE = """# Why Your Bank Statement Lies

The merchant name on a transaction is typed by the payment processor, not the shop.

The date you see is usually the settlement date. On a weekend those can be three
days apart.

Reconcile against receipts, not memory.
"""

EXTRACT_REPLY = json.dumps(
    {
        "thesis": "A bank statement records what the bank stored, not what you spent.",
        "claims": [
            {
                "id": "c0",
                "text": "The merchant name is typed by the payment processor, not the shop.",
                "kind": "fact",
                "segment_ids": ["s1"],
                "verbatim": None,
            },
            {
                "id": "c1",
                "text": "Settlement and payment dates can be three days apart on a weekend.",
                "kind": "stat",
                "segment_ids": ["s2"],
                "verbatim": "three\ndays apart",
            },
            {
                "id": "c2",
                "text": "This claim points at a segment that does not exist.",
                "kind": "fact",
                "segment_ids": ["s99"],
                "verbatim": None,
            },
        ],
        "entities": ["Visa"],
        "hook_candidates": ["Your statement is the bank's convenience, not your history."],
    }
)


class ScriptedLLM:
    """Returns queued replies in order and records the prompts it was given."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def text(self, prompt, as_json=False):
        self.prompts.append(prompt)
        if not self.replies:
            raise AssertionError("ScriptedLLM ran out of replies")
        return self.replies.pop(0)

    def structured(self, prompt, model_type):
        return model_type.model_validate_json(self.text(prompt, as_json=True))


@pytest.fixture
def document():
    return ingest_text(SOURCE, source_ref="test")


class TestExtract:
    def test_segment_ids_are_shown_to_the_model(self, document):
        rendered = render_source(document.segments)
        assert "[s0]" in rendered and "[s1]" in rendered

    def test_unanchored_claims_are_dropped(self, document):
        claims = extract(document, ScriptedLLM([EXTRACT_REPLY]))
        assert claims.ids == {"c0", "c1"}
        assert claims.__dict__["_dropped"] == 1

    def test_entities_not_in_the_source_are_dropped(self, document):
        claims = extract(document, ScriptedLLM([EXTRACT_REPLY]))
        assert claims.entities == []

    def test_verbatim_surviving_a_line_break_is_kept(self, document):
        claims = extract(document, ScriptedLLM([EXTRACT_REPLY]))
        assert claims.claim("c1").verbatim == "three\ndays apart"

    def test_rendered_claims_carry_ids_and_anchors(self, document):
        claims = extract(document, ScriptedLLM([EXTRACT_REPLY]))
        rendered = render_claims(claims, document)
        assert "[c0]" in rendered and "<- s1" in rendered
        # The generator must never be handed the raw source.
        assert "Reconcile against receipts" not in rendered


class TestRepurpose:
    def _reply(self, body, ids=("c0",)):
        return json.dumps({"body": body, "claim_ids": list(ids)})

    def _thread(self, posts, ids=("c0",)):
        return json.dumps({"posts": posts, "claim_ids": list(ids)})

    def test_clean_run_needs_one_call_per_target_plus_one_extract(self, document):
        llm = ScriptedLLM(
            [
                EXTRACT_REPLY,
                self._reply("The merchant name is typed by the payment processor. " * 12),
                self._thread(["The merchant name is typed by the processor."] * 5),
            ]
        )
        claims, artifacts = repurpose(document, ["linkedin", "thread"], llm)
        assert len(llm.prompts) == 3
        assert [a.target for a in artifacts] == ["linkedin", "thread"]
        assert all(a.clean for a in artifacts)

    def test_a_fabricated_statistic_triggers_exactly_one_repair(self, document):
        bad = self._reply("Merchant names are wrong on 91% of statements. " * 8)
        good = self._reply("Merchant names come from the processor, not the shop. " * 8)
        llm = ScriptedLLM([EXTRACT_REPLY, bad, good])

        _, artifacts = repurpose(document, ["linkedin"], llm)

        assert len(llm.prompts) == 3
        assert artifacts[0].clean
        assert artifacts[0].meta["repaired"] is True
        assert any("91%" in e for e in artifacts[0].meta["first_pass_errors"])

    def test_the_repair_prompt_names_the_offending_span(self, document):
        bad = self._reply("Merchant names are wrong on 91% of statements. " * 8)
        llm = ScriptedLLM([EXTRACT_REPLY, bad, bad])
        repurpose(document, ["linkedin"], llm)
        assert "'91%'" in llm.prompts[-1]

    def test_a_second_failure_is_surfaced_not_hidden(self, document):
        bad = self._reply("Merchant names are wrong on 91% of statements. " * 8)
        llm = ScriptedLLM([EXTRACT_REPLY, bad, bad])
        _, artifacts = repurpose(document, ["linkedin"], llm)
        assert not artifacts[0].clean
        assert artifacts[0].errors[0].span.startswith("91")

    def test_thread_posts_over_the_limit_are_flagged(self, document):
        llm = ScriptedLLM([EXTRACT_REPLY, self._thread(["x" * 300] + ["short post here"] * 4)])
        _, artifacts = repurpose(document, ["thread"], llm)
        assert "over 280 chars: [1]" in artifacts[0].meta["format_violation"]

    def test_unknown_target_is_refused(self, document):
        with pytest.raises(ValueError, match="unknown target"):
            repurpose(document, ["tiktok"], ScriptedLLM([EXTRACT_REPLY]))


class TestWriteOut:
    def test_every_artifact_gets_a_provenance_sidecar(self, document, tmp_path):
        llm = ScriptedLLM(
            [EXTRACT_REPLY, json.dumps({"body": "A clean post about processors.", "claim_ids": ["c0"]})]
        )
        claims, artifacts = repurpose(document, ["linkedin"], llm)
        out = write_out(tmp_path / "run", document, claims, artifacts)

        assert (out / "linkedin.txt").read_text(encoding="utf-8").startswith("A clean post")
        sidecar = json.loads((out / "linkedin.provenance.json").read_text(encoding="utf-8"))
        assert sidecar["provenance"][0]["claim_id"] == "c0"
        assert sidecar["provenance"][0]["segments"][0]["id"] == "s1"
        assert json.loads((out / "claims.json").read_text(encoding="utf-8"))["claims"]

    def test_provenance_offsets_land_on_the_real_source(self, document, tmp_path):
        llm = ScriptedLLM(
            [EXTRACT_REPLY, json.dumps({"body": "A clean post.", "claim_ids": ["c0"]})]
        )
        claims, artifacts = repurpose(document, ["linkedin"], llm)
        out = write_out(tmp_path / "run", document, claims, artifacts)
        span = json.loads((out / "linkedin.provenance.json").read_text(encoding="utf-8"))
        segment = span["provenance"][0]["segments"][0]
        excerpt = SOURCE[segment["char_start"] : segment["char_end"]]
        assert "merchant name" in excerpt.casefold()


class TestWindowing:
    def test_a_short_source_is_one_window(self, document):
        assert len(windows(document)) == 1

    def test_windows_split_on_segment_boundaries(self, document):
        chunks = windows(document, max_chars=80)
        assert len(chunks) > 1
        flat = [s.id for chunk in chunks for s in chunk]
        assert flat == [s.id for s in document.segments]

    def test_claim_ids_stay_unique_across_windows(self, document):
        # Every window returns its own c0, and the first claim of window one is
        # dropped as unanchored. Ids must still not collide.
        llm = ScriptedLLM([EXTRACT_REPLY] * len(windows(document, max_chars=80)))
        claims = extract(document, llm, max_chars=80)
        ids = [c.id for c in claims.claims]
        assert len(ids) == len(set(ids))
        assert ids == [f"c{i}" for i in range(len(ids))]


VOICE_REPLY = json.dumps(
    {
        "thesis": "Statements record the bank's convenience.",
        "claims": [
            {"id": "c0", "text": "Merchant names come from the processor.",
             "kind": "fact", "segment_ids": ["s1"], "verbatim": None}
        ],
        "entities": [],
        "hook_candidates": [],
        "voice_samples": [
            "Reconcile against receipts, not memory.",
            "This sentence was never in the source at all.",
        ],
    }
)


class TestVoiceSamples:
    def test_only_exact_spans_survive(self, document):
        claims = extract(document, ScriptedLLM([VOICE_REPLY]))
        assert claims.voice_samples == ["Reconcile against receipts, not memory."]

    def test_samples_reach_the_generator_labelled_as_tone(self, document):
        claims = extract(document, ScriptedLLM([VOICE_REPLY]))
        rendered = render_claims(claims, document)
        assert "HOW THE SOURCE SOUNDS" in rendered
        assert "not extra facts" in rendered
        assert "Reconcile against receipts, not memory." in rendered

    def test_no_samples_means_no_section(self, document):
        claims = extract(document, ScriptedLLM([EXTRACT_REPLY]))
        assert claims.voice_samples == []
        assert "HOW THE SOURCE SOUNDS" not in render_claims(claims, document)


class TestQuotaBranching:
    """429 covers two different worlds. Getting them confused either abandons a run
    over a one-minute blip or sleeps through a wall that lasts until tomorrow."""

    def _response(self, status, body):
        import httpx

        return httpx.Response(
            status_code=status, text=body, request=httpx.Request("POST", "https://x")
        )

    def test_a_per_day_gemini_wall_is_fatal(self):
        from recut.llm import Fatal, _raise_for

        body = '{"error":{"details":[{"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}}'
        with pytest.raises(Fatal):
            _raise_for(self._response(429, body), "gemini")

    def test_a_groq_per_minute_cap_is_retryable(self):
        import httpx

        from recut.llm import Fatal, _raise_for

        body = "Rate limit reached for model on tokens per minute (TPM): Limit 8000"
        with pytest.raises(httpx.HTTPStatusError):
            _raise_for(self._response(429, body), "groq")
        # And specifically not Fatal, which would skip the provider entirely.
        try:
            _raise_for(self._response(429, body), "groq")
        except Fatal:  # pragma: no cover
            pytest.fail("a per-minute cap was treated as a daily wall")
        except httpx.HTTPStatusError:
            pass

    def test_an_unlabelled_429_is_treated_as_the_expensive_case(self):
        from recut.llm import Fatal, _raise_for

        with pytest.raises(Fatal):
            _raise_for(self._response(429, "slow down"), "gemini")

    def test_a_400_is_still_fatal(self):
        from recut.llm import Fatal, _raise_for

        with pytest.raises(Fatal):
            _raise_for(self._response(400, "bad request"), "groq")
