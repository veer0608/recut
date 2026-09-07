from recut.models import Artifact, Claim, ClaimSet, Document, Segment
from recut.verify import (
    _prose_words,
    check_citations,
    check_copying,
    check_entities,
    check_intensity,
    check_numbers,
    check_quotes,
    longest_copied_run,
    verify,
)

SOURCE = (
    "Stripe processed 1,000 refunds last quarter, up 38% on the year. "
    'Patrick Collison said "the settlement date is not the payment date". '
    "Three of the five banks we tested drift by two days."
)


def errors(warnings):
    return [w for w in warnings if w.severity == "error"]


class TestNumbers:
    def test_number_present_in_source_passes(self):
        assert check_numbers("They handled 1000 refunds.", SOURCE) == []

    def test_comma_and_bare_forms_are_the_same_number(self):
        assert check_numbers("1,000 refunds", SOURCE) == []

    def test_percent_carried_over_passes(self):
        assert check_numbers("up 38% on the year", SOURCE) == []

    def test_invented_statistic_is_an_error(self):
        found = errors(check_numbers("Refunds rose 47% last quarter.", SOURCE))
        assert len(found) == 1
        assert found[0].span.startswith("47")
        assert found[0].rule == "number"

    def test_invented_large_number_is_an_error(self):
        assert errors(check_numbers("They handled 2.4 million refunds.", SOURCE))

    def test_source_spelling_a_number_out_vouches_for_the_digit(self):
        # Source says "Three of the five banks", output says "3 of the 5 banks".
        assert check_numbers("3 of the 5 banks drift.", SOURCE) == []

    def test_bare_small_integer_is_a_notice_not_an_error(self):
        # "4 things I learned" is list scaffolding, not a claim about the source.
        found = check_numbers("4 things worth knowing.", SOURCE)
        assert found and found[0].severity == "notice"

    def test_each_bad_number_reported_once(self):
        found = errors(check_numbers("47% here and 47% again", SOURCE))
        assert len(found) == 1


class TestQuotes:
    def test_verbatim_quote_passes(self):
        body = 'He said "the settlement date is not the payment date".'
        assert check_quotes(body, SOURCE) == []

    def test_smart_quotes_and_whitespace_do_not_matter(self):
        body = "He said “the  settlement date is not\nthe payment date”."
        assert check_quotes(body, SOURCE) == []

    def test_fabricated_quote_is_an_error(self):
        body = 'He said "we have never lost a single transaction".'
        found = errors(check_quotes(body, SOURCE))
        assert len(found) == 1
        assert found[0].rule == "quote"

    def test_scare_quotes_are_only_a_notice(self):
        found = check_quotes('This is the "real" problem.', SOURCE)
        assert all(w.severity == "notice" for w in found)


class TestEntities:
    def test_name_in_source_passes(self):
        assert check_entities("Patrick Collison was clear about this.", SOURCE) == []

    def test_invented_company_is_an_error(self):
        found = errors(check_entities("Adyen Holdings reported the same.", SOURCE))
        assert len(found) == 1
        assert found[0].rule == "entity"

    def test_common_acronyms_are_allowed(self):
        assert errors(check_entities("The API is the problem for any SaaS.", SOURCE)) == []

    def test_sentence_start_single_words_are_not_flagged(self):
        assert check_entities("Refunds are slow. Banks are slower.", SOURCE) == []


class TestCitations:
    def test_known_claim_id_passes(self):
        claims = ClaimSet(document_id="d", claims=[Claim(id="c0", text="x", segment_ids=["s0"])])
        assert check_citations(["c0"], claims) == []

    def test_unknown_claim_id_is_an_error(self):
        claims = ClaimSet(document_id="d", claims=[Claim(id="c0", text="x", segment_ids=["s0"])])
        found = check_citations(["c0", "c9"], claims)
        assert [w.span for w in found] == ["c9"]


class TestVerifyEndToEnd:
    def _document(self):
        return Document(
            id="d",
            title="Refunds",
            source_type="markdown",
            source_ref="inline",
            segments=[Segment(id="s0", text=SOURCE, char_start=0, char_end=len(SOURCE))],
        )

    def test_clean_artifact_has_no_errors(self):
        claims = ClaimSet(
            document_id="d", claims=[Claim(id="c0", text="Refunds rose", segment_ids=["s0"])]
        )
        artifact = Artifact(target="linkedin", body="Refunds rose 38% last quarter.", claim_ids=["c0"])
        assert verify(artifact, self._document(), claims).clean

    def test_injected_statistic_is_caught(self):
        # The ship gate for phase 0.
        claims = ClaimSet(
            document_id="d", claims=[Claim(id="c0", text="Refunds rose", segment_ids=["s0"])]
        )
        artifact = Artifact(target="linkedin", body="Refunds rose 91% last quarter.", claim_ids=["c0"])
        checked = verify(artifact, self._document(), claims)
        assert not checked.clean
        assert checked.errors[0].rule == "number"
        assert "91" in checked.errors[0].span


class TestEntitySeverity:
    """A word missing from the source is strong evidence of invention. A phrase that
    is missing, but built entirely from words the source does use, is not, and
    treating it as one burned a repair retry on every title-case heading."""

    def test_a_title_case_heading_is_not_a_company(self):
        body = "## The Poison of Economic Decay\n\nRefunds are slow."
        assert errors(check_entities(body, SOURCE + " economic decay poisoned it")) == []

    def test_an_invented_name_in_a_heading_is_still_caught(self):
        body = "## What Andrej Karpathy Got Wrong\n\nRefunds are slow."
        assert any("Karpathy" in w.span for w in errors(check_entities(body, SOURCE)))

    def test_a_novel_arrangement_of_real_words_is_only_a_notice(self):
        # The source says "western" and "roman" but never puts them together.
        found = check_entities(
            "The Western Roman state failed.", "the western empire and the roman world"
        )
        assert [w.severity for w in found] == ["notice"]

    def test_one_absent_word_is_still_an_error(self):
        found = errors(check_entities("Patrick Karpathy said so.", SOURCE))
        assert [w.span for w in found] == ["Patrick Karpathy"]

    def test_a_name_spanning_a_sentence_boundary_is_not_a_name(self):
        # "Rome. That simple story" is two sentences, not a two-word company.
        assert check_entities("Rome did not fall. That is the myth.", "rome did not fall") == []


class TestIntensity:
    """Added because of a measurement: the other four rules were at 100% on planted
    fabrications while the unsupported rate was still 15.5%, and nearly all of it was
    force added at write time rather than facts invented."""

    def test_a_word_the_source_uses_is_fine(self):
        assert check_intensity("This is critical work.", "the critical path matters") == []

    def test_promoting_good_practice_to_necessary_is_an_error(self):
        found = check_intensity(
            "Reconciliation is a necessary practice.", "reconciling often is good practice"
        )
        assert [w.rule for w in found] == ["intensity"]
        assert found[0].severity == "error"

    def test_an_inflection_in_the_source_vouches_for_the_word(self):
        # "necessity" in the source excuses "necessary" in the output.
        assert check_intensity("It is necessary.", "this is a necessity for banks") == []

    def test_is_used_in_becoming_relies_on_is_caught(self):
        assert check_intensity("Modern ML relies on it.", "it is used in ml systems")

    def test_ensures_and_critical_are_notices_not_errors(self):
        # Demoted by measurement: on the v1 corpus these two carried nearly all the
        # noise, firing on faithful and unfaithful output at the same rate. "the
        # check ensures X" and "the critical path" are ordinary technical prose.
        for body, source in [
            ("The check ensures accuracy.", "the check helps accuracy"),
            ("This is critical work.", "this work matters"),
        ]:
            found = check_intensity(body, source)
            assert [w.severity for w in found] == ["notice"], body

    def test_absolutes_stay_errors_after_the_demotion(self):
        for body in ("Banks always settle late.", "This guarantees a fix."):
            assert errors(check_intensity(body, "banks sometimes settle late")), body

    def test_absolutes_are_caught(self):
        assert check_intensity("Banks always settle late.", "banks sometimes settle late")

    def test_each_term_is_reported_once(self):
        found = check_intensity("It is critical. It is critical again.", "nothing here")
        assert len(found) == 1

    def test_a_plain_faithful_sentence_passes(self):
        body = "Merchant names come from the payment processor, not the shop."
        assert check_intensity(body, SOURCE) == []

    def test_it_joins_the_other_rules(self):
        document = Document(
            id="d", title="t", source_type="markdown", source_ref="i",
            segments=[Segment(id="s0", text="reconciling often is good practice",
                              char_start=0, char_end=33)],
        )
        claims = ClaimSet(document_id="d", claims=[Claim(id="c0", text="x", segment_ids=["s0"])])
        artifact = Artifact(
            target="linkedin", body="Reconciliation is essential.", claim_ids=["c0"]
        )
        checked = verify(artifact, document, claims)
        assert not checked.clean
        assert checked.errors[0].rule == "intensity"


def test_no_term_is_both_an_error_and_a_notice():
    from recut.verify import _INTENSITY_ERROR_TERMS, _INTENSITY_NOTICE_TERMS

    assert not set(_INTENSITY_ERROR_TERMS) & set(_INTENSITY_NOTICE_TERMS)


class TestCopying:
    """Repurposing means rewriting. Measured across the v1 and v2 corpora, 80% of
    artifacts carried an 8+ word verbatim run and the worst was 33 words."""

    SRC = "Reconcile against receipts, not memory. If the two disagree, the receipt is almost always right."

    def test_a_verbatim_sentence_is_caught(self):
        found = check_copying(self.SRC, self.SRC)
        assert found and found[0].rule == "copying"
        assert "consecutive words copied" in found[0].detail

    def test_a_genuine_rewrite_passes(self):
        body = "When reconciling, receipts beat memory, and the receipt usually wins a disagreement."
        assert check_copying(body, self.SRC) == []

    def test_quoting_is_the_correct_way_to_reuse_and_is_allowed(self):
        body = 'He put it plainly: "Reconcile against receipts, not memory. If the two disagree, the receipt is almost always right."'
        assert check_copying(body, self.SRC) == []

    def test_a_short_shared_phrase_is_not_copying(self):
        # Any two sentences on a subject share a few words in a row.
        body = "Reconcile against receipts, but trust your own judgement afterwards."
        assert check_copying(body, self.SRC) == []

    def test_the_reported_run_length_is_the_full_run(self):
        length, span = longest_copied_run(self.SRC, self.SRC)
        assert length == 15
        assert span.startswith("Reconcile against receipts")

    def test_it_is_a_notice_until_the_prompt_fix_is_measured(self):
        # Deliberately not an error yet: at an 80% hit rate a gate would send
        # four drafts in five back for repair before the prompt has had a chance
        # to change the behaviour it is punishing.
        assert check_copying(self.SRC, self.SRC)[0].severity == "notice"

    def test_a_short_source_cannot_trigger_it(self):
        assert check_copying("a b c", "a b c") == []


class TestCopyingProseFloor:
    """A recited figure is not a copied sentence.

    The generator prompts require a number to match its `stat` claim exactly, so
    flagging a run of them asks the writer to paraphrase a number. Measured over
    84 copied runs, a floor of four prose words drops only figure recitals.
    """

    SOURCE = (
        "The agent improved from 9.0 to 5.6 and tokens from 13,024 to 8,576 overall. "
        "Most tools generate speech, then run Whisper over that speech to find out "
        "where the words landed, which is a guess dressed up as a measurement."
    )

    def test_a_recited_figure_sequence_is_not_copying(self):
        # Only the figures are shared, which is the real shape: a copied run that
        # starts mid-sentence and carries one word of meaning.
        body = "Turns dropped from 9.0 to 5.6 and tokens from 13,024 to 8,576 in testing."
        assert check_copying(body, self.SOURCE) == []

    def test_a_copied_sentence_is_still_copying(self):
        body = (
            "Most tools generate speech, then run Whisper over that speech to find "
            "out where the words landed."
        )
        warnings = check_copying(body, self.SOURCE)
        assert len(warnings) == 1
        assert warnings[0].rule == "copying"

    def test_a_figure_recital_cannot_hide_a_copied_sentence(self):
        # The scan must continue past a disqualified run. Returning the longest
        # run and then rejecting it would let the numbers mask the prose.
        body = (
            "Turns fell from 9.0 to 5.6 and tokens from 13,024 to 8,576 across the set. "
            "Most tools generate speech, then run Whisper over that speech to find "
            "out where the words landed."
        )
        warnings = check_copying(body, self.SOURCE)
        assert len(warnings) == 1
        assert "Whisper" in warnings[0].span

    def test_the_floor_counts_meaning_not_length(self):
        assert _prose_words(["from", "9.0", "to", "5.6", "and", "tokens"]) == 1
        assert _prose_words(["most", "tools", "generate", "speech"]) == 3
