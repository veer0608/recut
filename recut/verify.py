"""Deterministic provenance checks. No model call, no network, no cost.

Everything here answers one question: does this sentence of output have anything
in the source standing behind it? Cheap checks run online; the expensive
entailment judgement lives in the eval harness and never touches the request path.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from .models import Artifact, ClaimSet, Document, Warning

# ---------------------------------------------------------------- normalisation

_MULTIPLIERS = {
    "k": Decimal(1_000),
    "m": Decimal(1_000_000),
    "b": Decimal(1_000_000_000),
    "thousand": Decimal(1_000),
    "million": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000),
    "lakh": Decimal(100_000),
    "crore": Decimal(10_000_000),
}

_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}

# A number token, optionally followed by a scale word, an ordinal suffix and a
# percent sign. The percent is captured but not parsed: it exists so the span we
# hand back to the user reads "91%" rather than a bare "91".
_NUMBER_RE = re.compile(
    r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*"
    r"(k|m|b|thousand|million|billion|lakh|crore)?"
    r"(?:st|nd|rd|th)?(?![\w])\s?%?",
    re.IGNORECASE,
)

_QUOTE_RE = re.compile("[“\"]([^“”\"]{4,400})[”\"]")

# Two or more capitalised words in a row, or a bare acronym. A full stop is NOT a
# word character here: with it, "Rome. That simple story" reads as one two-word
# name spanning the sentence boundary, and gets reported as an invented company.
_ENTITY_RE = re.compile(
    r"\b([A-Z][\w'’-]*(?:\s+(?:of|the|and|for|de|van)?\s*[A-Z][\w'’-]*)+)\b"
)
_ACRONYM_RE = re.compile(r"\b([A-Z]{2,8}s?)\b")

# Acronyms and capitalised pairs that are language, not claims.
_ENTITY_ALLOW = {
    "i", "a", "the", "ai", "ml", "api", "url", "ceo", "cto", "cfo", "hr", "it",
    "us", "uk", "eu", "ok", "faq", "diy", "pdf", "csv", "html", "css", "js",
    "tl", "tldr", "roi", "kpi", "b2b", "b2c", "saas", "gpt", "llm", "llms",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
}

# Capitalised only because they open a sentence. Stripped from the front of a
# candidate name so "The API" is judged as "API" and not as a company.
_LEADING_FUNCTION_WORDS = {
    "the", "a", "an", "this", "that", "these", "those", "our", "your", "their",
    "my", "his", "her", "its", "and", "but", "or", "if", "so", "in", "on", "at",
    "for", "to", "of", "we", "they", "it", "he", "she", "i", "you", "there",
    "here", "when", "while", "after", "before", "every", "no", "not", "yes",
    "most", "many", "some", "each", "both", "what", "why", "how",
}


def _fold(text: str) -> str:
    """Casefold, flatten unicode punctuation and collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = re.sub("[‐-―]", "-", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _to_decimal(raw: str, scale: str | None) -> Decimal | None:
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None
    if scale:
        value *= _MULTIPLIERS[scale.casefold()]
    return value


def numbers_in(text: str) -> list[tuple[str, Decimal]]:
    """Every digit-bearing quantity, as (surface form, canonical value)."""
    found: list[tuple[str, Decimal]] = []
    for match in _NUMBER_RE.finditer(text):
        value = _to_decimal(match.group(1), match.group(2))
        if value is not None:
            found.append((match.group(0).strip(), value))
    return found


def _source_number_values(text: str) -> set[Decimal]:
    """Numbers the source vouches for, including ones it spells out in words.

    Expanding the accepted set (rather than the checked set) is deliberate: it
    removes false alarms when the source writes "three" and the output writes "3",
    without ever letting an unsupported figure through.
    """
    values = {value for _, value in numbers_in(text)}
    lowered = _fold(text)
    for word, number in _WORD_NUMBERS.items():
        if re.search(rf"\b{word}\b", lowered):
            values.add(Decimal(number))
    # A year in the source also vouches for its two-digit short form.
    for value in list(values):
        if value == value.to_integral_value() and 1900 <= value <= 2100:
            values.add(value % 100)
    return values


def _is_high_stakes(surface: str, value: Decimal, text: str, at: int) -> bool:
    """A bare small integer is usually list scaffolding. Anything else is a claim."""
    if "." in surface or "," in surface:
        return True
    if re.search(r"[kmb]|thousand|million|billion|lakh|crore", surface, re.IGNORECASE):
        return True
    window = text[max(0, at - 12) : at + len(surface) + 12]
    if re.search(r"[%$£€₹]|percent|per cent", window, re.IGNORECASE):
        return True
    return value > 10


# ------------------------------------------------------------------- the rules


def check_numbers(body: str, source: str) -> list[Warning]:
    supported = _source_number_values(source)
    warnings: list[Warning] = []
    seen: set[Decimal] = set()
    for match in _NUMBER_RE.finditer(body):
        value = _to_decimal(match.group(1), match.group(2))
        if value is None or value in supported or value in seen:
            continue
        seen.add(value)
        surface = match.group(0).strip()
        high = _is_high_stakes(surface, value, body, match.start())
        warnings.append(
            Warning(
                rule="number",
                severity="error" if high else "notice",
                span=surface,
                detail="no matching figure in the source",
            )
        )
    return warnings


def check_quotes(body: str, source: str) -> list[Warning]:
    folded_source = _fold(source)
    warnings: list[Warning] = []
    for match in _QUOTE_RE.finditer(body):
        quoted = match.group(1).strip()
        folded = _fold(quoted).strip(" .,;:!?")
        if not folded or folded in folded_source:
            continue
        # Fewer than three words inside quotes is emphasis, not attribution.
        severity = "error" if len(folded.split()) >= 3 else "notice"
        warnings.append(
            Warning(
                rule="quote",
                severity=severity,
                span=quoted,
                detail="not a verbatim span of the source",
            )
        )
    return warnings


# Words that turn a description into a verdict. Each entry is matched case
# insensitively against both the output and the source: the question is never "is
# this word bad" but "did the SOURCE reach for it too". Stems are used where a
# source is likely to carry a different inflection ("necessity" vouches for
# "necessary"), which keeps this from firing on a source that made the same point.
# Demoted to notice by measurement, not taste. On the v1 corpus the full list fired
# on 55% of artifacts the judge passed clean and only 47% of the ones it failed,
# which is no discrimination at all. These two were carrying almost all of that
# noise: "the check ensures X" and "the critical path" are ordinary technical prose,
# not claims of strength. Dropping exactly these two takes the rule to 0% on clean
# artifacts and 37% on unfaithful ones. Everything else measured worse, in both
# directions, so the list stops here.
_INTENSITY_NOTICE_TERMS = (
    r"ensures?",
    r"critical",
)

_INTENSITY_ERROR_TERMS = (
    r"necess(?:ary|ity|itates)",
    r"essential",
    r"crucial",
    r"vital",
    r"imperative",
    r"mandatory",
    r"ultimate",
    r"revolutionar",
    r"game[- ]chang",
    r"ground[- ]breaking",
    r"transformative",
    r"unprecedented",
    r"dramatic(?:ally)?",
    r"massive(?:ly)?",
    r"enormous",
    r"huge",
    r"significant(?:ly)?",
    r"major",
    r"biggest",
    r"greatest",
    r"always",
    r"never",
    r"every single",
    r"guarantee(?:s|d|ing)?",
    r"eliminates?",
    r"proves?",
    r"relies on",
    r"depends entirely",
    r"cannot function without",
    r"the key to",
    r"the only way",
    r"a must",
)
# Severity travels with the term rather than being inferred somewhere else, so the
# tuner and the rule can never disagree about which list a word is on.
_INTENSITY_PATTERNS = [
    (re.compile(rf"\b{term}\b", re.IGNORECASE), term, "error")
    for term in _INTENSITY_ERROR_TERMS
] + [
    (re.compile(rf"\b{term}\b", re.IGNORECASE), term, "notice")
    for term in _INTENSITY_NOTICE_TERMS
]

# The whole vocabulary, for the tuner to sweep.
_INTENSITY_TERMS = _INTENSITY_ERROR_TERMS + _INTENSITY_NOTICE_TERMS


def _has_absent_word(span: str, folded_source: str) -> bool:
    """Does any word of this name fail to appear in the source at all?

    This is what separates an invented name from a novel arrangement of real ones.
    "Adyen Holdings" against a source that mentions neither word is a fabrication.
    "Western Roman" against a source that says "western" and "roman" separately is
    the writer composing a standard descriptor, which is allowed. A missing word is
    strong evidence; a missing phrase, on its own, is weak.
    """
    words = [w for w in _fold(span).split() if len(w) > 2 and w not in _ENTITY_ALLOW]
    return any(not re.search(rf"\b{re.escape(w)}", folded_source) for w in words)


def _strip_leading_function_words(span: str) -> str:
    words = span.split()
    while words and words[0].casefold().strip(".,'") in _LEADING_FUNCTION_WORDS:
        words.pop(0)
    return " ".join(words)


def check_entities(body: str, source: str) -> list[Warning]:
    folded_source = _fold(source)
    warnings: list[Warning] = []
    seen: set[str] = set()

    def flag(span: str, severity: str) -> None:
        key = _fold(span)
        if not key or key in seen or key in _ENTITY_ALLOW:
            return
        if key in folded_source:
            return
        seen.add(key)
        warnings.append(
            Warning(
                rule="entity",
                severity=severity,
                span=span,
                detail="name does not appear in the source",
            )
        )

    for line in body.splitlines():
        for match in _ENTITY_RE.finditer(line):
            span = _strip_leading_function_words(match.group(1))
            # One word left over is a sentence opener, not a name. The acronym pass
            # below picks it up if it is actually an acronym.
            if not span or len(span.split()) < 2:
                continue
            # This also settles markdown headings, which are written in title case
            # and so turn every heading into an apparent proper noun. Their words
            # come from the source, so they land as notices instead of errors.
            flag(span, "error" if _has_absent_word(span, folded_source) else "notice")

    for match in _ACRONYM_RE.finditer(body):
        flag(match.group(1), "notice")
    return warnings


def check_intensity(body: str, source: str) -> list[Warning]:
    """Words that raise a claim's strength, used when the source never used them.

    This exists because of a measurement, not a hunch. On the v1 golden run the
    other four rules caught 120 of 120 planted fabrications and the unsupported rate
    was still 15.5%, because almost every failure was inflation rather than
    invention: "good practice" became "necessary", "may arise" became "often",
    "is used in" became "relies on". Every token was real, so no token check could
    see it. This one asks a narrower question that a token check *can* answer: did
    the output reach for a word of force that the source never reached for?

    It is deliberately one-directional. It says nothing about whether the claim is
    supported, only that the strength was added here rather than carried over, which
    is enough to send the draft back once.
    """
    folded_source = _fold(source)
    warnings: list[Warning] = []
    seen: set[str] = set()
    for pattern, surface, severity in _INTENSITY_PATTERNS:
        match = pattern.search(body)
        if not match or surface in seen:
            continue
        if pattern.search(folded_source):
            continue
        seen.add(surface)
        warnings.append(
            Warning(
                rule="intensity",
                severity=severity,
                span=match.group(0).strip(),
                detail=f"the source never says {surface!r}; do not add force it did not use",
            )
        )
    return warnings


def check_citations(claim_ids: list[str], claims: ClaimSet) -> list[Warning]:
    known = claims.ids
    return [
        Warning(
            rule="citation",
            severity="error",
            span=claim_id,
            detail="cites a claim id that does not exist",
        )
        for claim_id in claim_ids
        if claim_id not in known
    ]


def verify(artifact: Artifact, document: Document, claims: ClaimSet) -> Artifact:
    """Attach every unanchored span to the artifact. Never raises, never rewrites."""
    source = document.text
    artifact.warnings = (
        check_numbers(artifact.body, source)
        + check_quotes(artifact.body, source)
        + check_entities(artifact.body, source)
        + check_intensity(artifact.body, source)
        + check_citations(artifact.claim_ids, claims)
    )
    return artifact


def repair_note(artifact: Artifact) -> str:
    """The correction handed back to the model for its one retry."""
    lines = [f"- {w.span!r}: {w.detail}" for w in artifact.errors]
    return (
        "The previous draft contained spans that are not supported by the source:\n"
        + "\n".join(lines)
        + "\nRewrite it. Remove or replace every span listed above. Do not invent "
        "replacements: if a figure or name is not in the claims you were given, "
        "say the thing without it."
    )
