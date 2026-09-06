"""Per-term precision for the intensity rule, from labels already paid for.

The v1 run left 258 sentences on disk with a supported/unsupported judgement
attached to each. That is a labelled corpus, and it can answer the question the
rule's severity split actually turns on: when a given word of force appears in a
sentence and the source never used that word, how often is that sentence really
unsupported?

Terms that answer "usually" earn error severity, because sending the draft back is
worth it. Terms that answer "rarely" are noise wearing a rule's clothing, and they
belong at notice, where they inform without costing a regeneration.

Re-ingesting the sources costs no model calls, so this runs on a spent quota.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recut.ingest import ingest  # noqa: E402
from recut.verify import _INTENSITY_TERMS, _fold  # noqa: E402

HERE = Path(__file__).resolve().parent


def collect(run: str = "v1") -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"unsupported": 0, "supported": 0})

    for path in sorted((HERE / "results" / run / "sources").glob("*.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        try:
            source = _fold(ingest(result["ref"]).text)
        except Exception as exc:  # noqa: BLE001
            print(f"  skipped {result['id']}: {type(exc).__name__}: {exc}"[:110])
            continue

        for judged in result["judged"].values():
            for verdict in judged["verdicts"]:
                label = verdict["verdict"]
                if label not in ("supported", "unsupported"):
                    continue
                sentence = verdict["sentence"]
                for term in _INTENSITY_TERMS:
                    pattern = re.compile(rf"\b{term}\b", re.IGNORECASE)
                    # The rule only fires when the source never used the word, so
                    # only those occurrences belong in the precision estimate.
                    if pattern.search(sentence) and not pattern.search(source):
                        counts[term][label] += 1
    return counts


def main() -> int:
    print("re-ingesting v1 sources (no model calls)...")
    counts = collect()

    rows = []
    for term, tally in counts.items():
        hits = tally["unsupported"] + tally["supported"]
        rows.append((tally["unsupported"] / hits, hits, tally["unsupported"], term))
    rows.sort(key=lambda r: (-r[0], -r[1]))

    print(f"\n{'term':<28}{'fires':>6}{'unsup':>7}{'precision':>11}")
    print("-" * 52)
    for precision, hits, unsupported, term in rows:
        print(f"{term:<28}{hits:>6}{unsupported:>7}{precision:>10.0%}")

    never = [t for t in _INTENSITY_TERMS if t not in counts]
    print(f"\nnever fired on the v1 corpus ({len(never)}):")
    print("  " + ", ".join(never))

    body_level()

    fired = sum(h for _, h, _, _ in rows)
    caught = sum(u for _, _, u, _ in rows)
    print(f"\noverall: {fired} firings, {caught} on genuinely unsupported sentences "
          f"({caught / fired:.0%})" if fired else "\nno firings")
    return 0



def body_level(run: str = "v1") -> None:
    """Does the rule fire on bodies the judge found faithful?

    The v2 report called every firing on an unrepaired body a false positive, which
    assumes those bodies were faithful. v1 measured that 15.5% of sentences are not,
    so that assumption is wrong and the 21.4% overstates the problem. This asks the
    question properly: restrict to artifacts the judge passed with zero unsupported
    sentences, and only then is a firing definitely wrong.
    """
    from recut.verify import check_intensity

    fired_on_clean = fired_on_dirty = clean = dirty = 0
    examples: list[str] = []

    for path in sorted((HERE / "results" / run / "sources").glob("*.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        try:
            source = ingest(result["ref"]).text
        except Exception:  # noqa: BLE001
            continue
        for target, body in result.get("bodies", {}).items():
            judged = result["judged"].get(target)
            if not judged or not judged["claims"]:
                continue
            # Only errors send a draft back, so only errors are a cost.
            hits = [w for w in check_intensity(body, source) if w.severity == "error"]
            faithful = judged["unsupported"] == 0
            if faithful:
                clean += 1
                if hits:
                    fired_on_clean += 1
                    examples.append(f"{result['id']}/{target}: {hits[0].span!r}")
            else:
                dirty += 1
                fired_on_dirty += 1 if hits else 0

    print("\nbody level, against the judge's own verdicts")
    print("-" * 52)
    print(f"artifacts the judge passed clean   {clean}")
    print(f"  ... the rule fired on            {fired_on_clean}"
          f"  ({fired_on_clean / clean:.0%})" if clean else "")
    print(f"artifacts with unsupported claims  {dirty}")
    print(f"  ... the rule fired on            {fired_on_dirty}"
          f"  ({fired_on_dirty / dirty:.0%})" if dirty else "")
    for line in examples[:8]:
        print(f"      wrongly flagged {line}")
if __name__ == "__main__":
    raise SystemExit(main())
