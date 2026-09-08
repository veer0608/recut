"""Known fabrications, planted on purpose, so the verifier can be scored.

The entailment judge in judge.py produces the headline number, but a judge is
itself a model and an unvalidated judge is a number with nothing behind it. This
module builds the one part of the eval where the truth is known by construction:
every injection here is a fabrication, so anything the verifier misses is a false
negative with no judgement call about it.

The other direction is weaker and used to be overstated here. Injections are planted
on top of generated bodies, and a body with nothing planted in it is only assumed
clean: v6 raised errors on five of them and three were invented quotations the quote
rule caught correctly, one attributed to a named person. So errors on unplanted
bodies are an upper bound on false alarms, not a count of them, and the fields are
named for that.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from recut.models import Artifact, ClaimSet, Document
from recut.verify import _fold, verify

# Names chosen to be absent from any plausible source about software or history,
# and to be two words so they read as a proper noun rather than a sentence opener.
FAKE_NAMES = [
    "Marguerite Oyelaran",
    "Thaddeus Blancpain",
    "Ingrid Vasquez-Thorne",
    "Cornelius Adeyemi",
    "Rosalind Kettleburn",
]
FAKE_QUOTES = [
    "we have never once lost a single transaction in eleven years",
    "the whole industry is quietly built on a spreadsheet nobody owns",
    "our own engineers cannot explain why it works on Tuesdays",
]
FAKE_NUMBERS = ["91.4%", "2.7 million", "$48,300", "173%", "6.8 billion"]
# Inflation, the failure the v1 run showed dominates. Each of these adds force that
# a source would have to have used itself for the sentence to be honest.
FAKE_INTENSITY = [
    "This is the ultimate metric, and every serious team relies on it.",
    "Getting this wrong is always fatal and the fix is essential.",
    "This single change guarantees a dramatic improvement.",
]


@dataclass
class Injection:
    artifact_id: str
    kind: str  # number | quote | entity | citation
    planted: str
    body: str
    claim_ids: list[str]


def _absent(candidate: str, folded_source: str) -> bool:
    return _fold(candidate) not in folded_source


def _first_absent(options: list[str], folded_source: str, offset: int) -> str | None:
    """Rotate through the options so repeated calls do not plant the same token."""
    ordered = options[offset % len(options) :] + options[: offset % len(options)]
    for option in ordered:
        if _absent(option, folded_source):
            return option
    return None


def _insertion_point(body: str) -> int:
    """After the first sentence, so the injection sits in prose rather than a hook."""
    match = re.search(r"[.!?]\s", body)
    return match.end() if match else len(body)


def plant(artifact: Artifact, document: Document, seed: int = 0) -> list[Injection]:
    """One injection per rule, each a fabrication by construction."""
    folded = _fold(document.text)
    rng = random.Random(seed)
    offset = rng.randrange(1000)
    at = _insertion_point(artifact.body)
    head, tail = artifact.body[:at], artifact.body[at:]
    out: list[Injection] = []

    number = _first_absent(FAKE_NUMBERS, folded, offset)
    if number:
        out.append(
            Injection(
                artifact.target,
                "number",
                number,
                f"{head}Independent testing put the figure at {number}. {tail}",
                list(artifact.claim_ids),
            )
        )

    quote = _first_absent(FAKE_QUOTES, folded, offset)
    if quote:
        out.append(
            Injection(
                artifact.target,
                "quote",
                quote,
                f'{head}One of them put it plainly: "{quote}". {tail}',
                list(artifact.claim_ids),
            )
        )

    name = _first_absent(FAKE_NAMES, folded, offset)
    if name:
        out.append(
            Injection(
                artifact.target,
                "entity",
                name,
                f"{head}{name} reached the same conclusion independently. {tail}",
                list(artifact.claim_ids),
            )
        )

    intensity = _first_absent(FAKE_INTENSITY, folded, offset)
    if intensity:
        out.append(
            Injection(
                artifact.target,
                "intensity",
                intensity,
                f"{head}{intensity} {tail}",
                list(artifact.claim_ids),
            )
        )

    out.append(
        Injection(
            artifact.target,
            "citation",
            "c9999",
            artifact.body,
            list(artifact.claim_ids) + ["c9999"],
        )
    )
    return out


def score(
    artifacts: list[Artifact], document: Document, claims: ClaimSet, seed: int = 0
) -> dict:
    """Verifier recall on planted fabrications, and errors on unplanted bodies.

    Recall is per rule as well as overall, because an overall number hides a rule
    that has quietly stopped working.
    """
    caught: dict[str, list[int]] = {}
    missed: list[dict] = []

    for artifact in artifacts:
        for injection in plant(artifact, document, seed=seed):
            probe = Artifact(
                target=artifact.target, body=injection.body, claim_ids=injection.claim_ids
            )
            errors = verify(probe, document, claims).errors
            # The rule that fires matters. A number injection caught by the entity
            # rule is luck, not a working number check.
            hit = any(w.rule == injection.kind for w in errors)
            caught.setdefault(injection.kind, []).append(1 if hit else 0)
            if not hit:
                missed.append(
                    {
                        "target": artifact.target,
                        "kind": injection.kind,
                        "planted": injection.planted,
                        "flagged_instead": [w.rule for w in errors],
                    }
                )

    # Errors raised on a body nothing was planted in. NOT false alarms, though the
    # metric was called that until v6 showed why it matters: three of the five were
    # invented quotations the quote rule caught correctly, including one attributed
    # to a named person. A body with no injection is assumed clean and nothing
    # establishes that, because injections are planted on top of generated bodies
    # and the base body carries its own faults. This is an upper bound on false
    # alarms; separating them needs the bodies labelled by hand. Named for what it
    # counts, the way claim_utilisation is named for what it counts.
    #
    # Copying is excluded outright. It is never planted, and a body reproducing the
    # source really did reproduce it, so counting it here would make the number
    # climb every time the copying rule worked.
    fabrication_errors = [
        (a, [w for w in verify(
            Artifact(target=a.target, body=a.body, claim_ids=a.claim_ids), document, claims
        ).errors if w.rule != "copying"])
        for a in artifacts
    ]
    false_positives = [
        {
            "target": a.target,
            "rules": sorted({w.rule for w in errors}),
            "spans": [str(w) for w in errors],
        }
        for a, errors in fabrication_errors
        if errors
    ]

    # Which rule cried wolf, not just how many bodies it happened in. Recall was
    # per rule from the start, "because an overall number hides a rule that has
    # quietly stopped working", and the same argument applies in this direction:
    # v4 and v6 differed by two bodies out of thirty and nothing on disk said
    # whether that was intensity, entity, or something new. Counted per body, so
    # a rule firing twice in one body counts once and the figures stay
    # commensurable with unplanted_error_bodies.
    fp_by_rule: dict[str, int] = {}
    for _, errors in fabrication_errors:
        for rule in {w.rule for w in errors}:
            fp_by_rule[rule] = fp_by_rule.get(rule, 0) + 1

    per_rule = {rule: sum(hits) / len(hits) for rule, hits in caught.items() if hits}
    total = sum(sum(h) for h in caught.values())
    planted = sum(len(h) for h in caught.values())

    return {
        "planted": planted,
        "caught": total,
        "recall": total / planted if planted else None,
        "recall_by_rule": per_rule,
        "missed": missed,
        "clean_bodies": len(artifacts),
        "unplanted_error_bodies": len(false_positives),
        "unplanted_errors_by_rule": dict(sorted(fp_by_rule.items())),
        "unplanted_errors": false_positives,
    }
