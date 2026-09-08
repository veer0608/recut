"""The phase 2 targets, driven by a scripted model. No network."""

import json

import pytest
import yaml

from recut.generate import article, newsletter, vidsmith
from recut.ingest.markdown import ingest_text
from recut.ingest.youtube import ingest_youtube
from recut.models import ClaimSet
from recut.pipeline import applicable, repurpose, unsupported, write_out

from .test_pipeline import EXTRACT_REPLY, SOURCE, ScriptedLLM

CUES = [
    {"text": "the merchant name on a transaction is typed by the processor.", "start": 0.0, "duration": 4.0},
    {"text": "the date you see is usually the settlement date.", "start": 4.0, "duration": 4.0},
]


@pytest.fixture
def text_doc():
    return ingest_text(SOURCE, source_ref="test")


@pytest.fixture
def video_doc():
    return ingest_youtube("https://youtu.be/0PkBP0dk4Lw", cues=CUES, title="Statements")


@pytest.fixture
def claims(text_doc):
    from recut.extract import extract

    return extract(text_doc, ScriptedLLM([EXTRACT_REPLY]))


def paragraph(words):
    return " ".join(["processors"] * words)


class TestTargetGuards:
    def test_article_needs_a_timed_source(self, text_doc):
        assert unsupported("article", text_doc) == "only offered for video and audio sources"

    def test_article_is_offered_for_video(self, video_doc):
        assert unsupported("article", video_doc) is None

    def test_all_expands_differently_per_source(self, text_doc, video_doc):
        assert "article" not in applicable(text_doc)
        assert "article" in applicable(video_doc)
        assert set(applicable(text_doc)) == {"linkedin", "thread", "newsletter", "vidsmith"}

    def test_an_unrunnable_target_is_refused_by_name(self, text_doc):
        with pytest.raises(ValueError, match="only offered for video"):
            repurpose(text_doc, ["article"], ScriptedLLM([EXTRACT_REPLY]))


class TestNewsletter:
    def _reply(self, **over):
        payload = {
            "subject": "The date on your statement is not the date you paid",
            "preview": "Settlement dates drift, and nobody tells you",
            "body": "## Why\n\n" + paragraph(320),
            "claim_ids": ["c0"],
        }
        payload.update(over)
        return json.dumps(payload)

    def test_subject_and_preview_are_verified_with_the_body(self, text_doc, claims):
        llm = ScriptedLLM([self._reply(subject="Refunds rose 91% last quarter")])
        art = newsletter.build(text_doc, claims, llm)
        assert art.body.startswith("Subject: Refunds rose 91%")

    def test_a_long_subject_is_flagged(self, text_doc, claims):
        llm = ScriptedLLM([self._reply(subject="x" * 80)])
        art = newsletter.build(text_doc, claims, llm)
        assert "subject is 80 chars" in art.meta["format_violation"]

    def test_a_long_preview_is_flagged(self, text_doc, claims):
        llm = ScriptedLLM([self._reply(preview="y" * 120)])
        assert "preview is 120 chars" in newsletter.build(text_doc, claims, llm).meta[
            "format_violation"
        ]

    def test_a_short_issue_is_flagged(self, text_doc, claims):
        llm = ScriptedLLM([self._reply(body="too short")])
        assert "2 words" in newsletter.build(text_doc, claims, llm).meta["format_violation"]

    def test_a_well_formed_issue_has_no_violation(self, text_doc, claims):
        art = newsletter.build(text_doc, claims, ScriptedLLM([self._reply()]))
        assert "format_violation" not in art.meta
        assert art.meta["words"] == 322


class TestArticle:
    def _reply(self, **over):
        payload = {
            "title": "Settlement dates drift and nobody tells you",
            "body": "## One\n\n" + paragraph(200) + "\n\n## Two\n\n" + paragraph(200),
            "claim_ids": ["c0"],
        }
        payload.update(over)
        return json.dumps(payload)

    def test_title_becomes_an_h1(self, video_doc, claims):
        art = article.build(video_doc, claims, ScriptedLLM([self._reply()]))
        assert art.body.startswith("# Settlement dates drift")

    def test_sections_are_counted(self, video_doc, claims):
        art = article.build(video_doc, claims, ScriptedLLM([self._reply()]))
        assert art.meta["sections"] == 2
        assert "format_violation" not in art.meta

    def test_one_section_is_flagged(self, video_doc, claims):
        llm = ScriptedLLM([self._reply(body="## Only\n\n" + paragraph(600))])
        assert "1 sections" in article.build(video_doc, claims, llm).meta["format_violation"]

    def test_length_follows_the_material(self, video_doc, claims):
        # Two claims cannot honestly fill 800 words, so the floor drops rather than
        # inviting the model to pad.
        art = article.build(video_doc, claims, ScriptedLLM([self._reply()]))
        assert art.meta["target_words"] == 350

    def test_the_prompt_asks_for_the_same_range_it_checks(self, video_doc, claims):
        llm = ScriptedLLM([self._reply()])
        article.build(video_doc, claims, llm)
        assert "210 to 525 words" in llm.prompts[0]

    def test_transcript_tells_are_flagged(self, video_doc, claims):
        # An article that says "in this video" has not been repurposed, it has been
        # transcribed with extra steps.
        llm = ScriptedLLM([self._reply(body="## One\n\nIn this video we look at it. " + paragraph(600))])
        violation = article.build(video_doc, claims, llm).meta["format_violation"]
        assert "reads as a transcript: in this video" in violation


class TestVidsmith:
    def _scenes(self, count=6, narration="Merchant names come from the processor and not the shop."):
        return [
            {"heading": f"Beat {i}", "visual": "farmer walking away from a field", "narration": narration}
            for i in range(count)
        ]

    def _reply(self, **over):
        payload = {"title": "Statements lie about dates", "scenes": self._scenes(), "claim_ids": ["c0"]}
        payload.update(over)
        return json.dumps(payload)

    def test_it_emits_a_project_not_a_paragraph(self, text_doc, claims):
        art = vidsmith.build(text_doc, claims, ScriptedLLM([self._reply()]))
        assert set(art.files) == {"vidsmith/script.md", "vidsmith/config.yaml"}

    def test_the_script_is_in_vidsmith_format(self, text_doc, claims):
        script = vidsmith.build(text_doc, claims, ScriptedLLM([self._reply()])).files[
            "vidsmith/script.md"
        ]
        assert script.startswith("# Statements lie about dates")
        assert script.count("## ") == 6
        assert script.count("[visual: ") == 6

    def test_the_config_is_valid_yaml_and_names_the_source(self, text_doc, claims):
        raw = vidsmith.build(text_doc, claims, ScriptedLLM([self._reply()])).files[
            "vidsmith/config.yaml"
        ]
        config = yaml.safe_load(raw)
        assert config["title"] == "Statements lie about dates"
        assert config["render"]["aspect"] == "9:16"
        assert "test" in raw

    def test_only_narration_is_verified(self, text_doc, claims):
        # A visual query names things the source never did. That is correct: it is a
        # stock search string, not a published sentence.
        art = vidsmith.build(text_doc, claims, ScriptedLLM([self._reply()]))
        assert "[visual:" not in art.body
        assert "farmer walking" not in art.body

    def test_too_few_scenes_is_flagged(self, text_doc, claims):
        llm = ScriptedLLM([self._reply(scenes=self._scenes(3))])
        assert "3 scenes" in vidsmith.build(text_doc, claims, llm).meta["format_violation"]

    def test_symbols_a_voice_cannot_read_are_flagged(self, text_doc, claims):
        scenes = self._scenes()
        scenes[2]["narration"] = "Refunds rose 38% and nobody noticed the drift at all."
        llm = ScriptedLLM([self._reply(scenes=scenes)])
        assert "voice cannot read" in vidsmith.build(text_doc, claims, llm).meta["format_violation"]

    def test_an_unfilmable_visual_query_is_flagged(self, text_doc, claims):
        scenes = self._scenes()
        scenes[1]["visual"] = "the concept of economic decline"
        llm = ScriptedLLM([self._reply(scenes=scenes)])
        assert "unfilmable visual queries" in vidsmith.build(text_doc, claims, llm).meta[
            "format_violation"
        ]

    def test_a_missing_visual_query_is_flagged(self, text_doc, claims):
        scenes = self._scenes()
        scenes[0]["visual"] = ""
        llm = ScriptedLLM([self._reply(scenes=scenes)])
        assert "no visual query" in vidsmith.build(text_doc, claims, llm).meta["format_violation"]

    def test_runtime_is_estimated_from_the_word_count(self, text_doc, claims):
        art = vidsmith.build(text_doc, claims, ScriptedLLM([self._reply()]))
        assert art.meta["words"] == 60
        assert art.meta["estimated_seconds"] == pytest.approx(23.1, abs=0.1)

    def test_the_project_lands_on_disk(self, text_doc, claims, tmp_path):
        llm = ScriptedLLM([EXTRACT_REPLY, self._reply()])
        claim_set, artifacts = repurpose(text_doc, ["vidsmith"], llm)
        out = write_out(tmp_path / "run", text_doc, claim_set, artifacts)
        assert (out / "vidsmith" / "script.md").exists()
        assert (out / "vidsmith" / "config.yaml").exists()


class TestProvenanceTravels:
    """The directory a target emits carries its own anchors.

    A sidecar written beside the project does not travel with it. The vidsmith
    project becomes a published video, so without this it was the one output
    that could not be traced back, in a product whose whole claim is that
    everything can.
    """

    def _document(self):
        from recut.ingest.markdown import ingest_text

        return ingest_text(
            "# Captions\n\nThe speech engine reports when each word starts.\n\n"
            "Transcribing the audio back is a guess.\n",
            source_ref="test",
        )

    def _claims(self):
        from recut.models import Claim, ClaimSet

        return ClaimSet(
            document_id="d",
            claims=[
                Claim(id="c0", text="The engine reports word timings.", segment_ids=["s1"]),
                Claim(id="c1", text="Transcribing back is a guess.", segment_ids=["s2"]),
            ],
        )

    def _artifact(self, files):
        from recut.models import Artifact

        return Artifact(target="vidsmith", body="Narration.", claim_ids=["c0"], files=files)

    def test_an_emitted_directory_gets_a_provenance_file(self):
        from recut.pipeline import _carry_provenance

        artifact = self._artifact({"vidsmith/script.md": "# S\n", "vidsmith/config.yaml": "t: 1\n"})
        _carry_provenance(artifact, self._document(), self._claims())
        assert "vidsmith/provenance.json" in artifact.files

    def test_it_names_the_segment_behind_each_claim_used(self):
        import json

        from recut.pipeline import _carry_provenance

        artifact = self._artifact({"vidsmith/script.md": "# S\n"})
        _carry_provenance(artifact, self._document(), self._claims())
        written = json.loads(artifact.files["vidsmith/provenance.json"])
        assert [p["claim_id"] for p in written["provenance"]] == ["c0"]
        assert written["provenance"][0]["segments"][0]["id"] == "s1"

    def test_a_prose_target_emits_nothing_and_gains_nothing(self):
        from recut.pipeline import _carry_provenance

        artifact = self._artifact({})
        _carry_provenance(artifact, self._document(), self._claims())
        assert artifact.files == {}

    def test_the_span_can_be_read_back_out_of_the_source(self):
        import json

        from recut.pipeline import _carry_provenance

        document = self._document()
        artifact = self._artifact({"vidsmith/script.md": "# S\n"})
        _carry_provenance(artifact, document, self._claims())
        segment = json.loads(artifact.files["vidsmith/provenance.json"])["provenance"][0]["segments"][0]
        # The whole point of the anchors: a reader can go and look.
        quoted = document.raw[segment["char_start"]:segment["char_end"]]
        assert "speech engine" in quoted


class TestSourceTravelsToTheVideo:
    """The emitted project names what it was built from.

    vidsmith writes that string verbatim into the description of anything
    published from the project, so a video made out of someone else's writing
    credits it. recut is the only thing that knows the source.
    """

    def test_the_config_carries_the_source(self):
        import yaml

        from recut.generate.vidsmith import render_config

        parsed = yaml.safe_load(render_config("A title", "https://example.com/post"))
        assert parsed["source"] == "https://example.com/post"

    def test_a_query_string_survives_intact(self):
        import yaml

        from recut.generate.vidsmith import render_config

        # An attribution is only an attribution if it still resolves. The
        # comment on line one is prose for a human; this field is the one read.
        ref = "https://example.com/a?b=1&c=2#x"
        assert yaml.safe_load(render_config("T", ref))["source"] == ref

    def test_a_title_with_a_colon_still_yields_valid_yaml(self):
        import yaml

        from recut.generate.vidsmith import render_config

        parsed = yaml.safe_load(render_config("Rome: how it fell", "ref"))
        assert parsed["title"] == "Rome: how it fell"
        assert parsed["source"] == "ref"

    def test_a_local_path_is_carried_as_written(self):
        import yaml

        from recut.generate.vidsmith import render_config

        assert yaml.safe_load(render_config("T", "../schemablind/README.md"))["source"] == (
            "../schemablind/README.md"
        )
