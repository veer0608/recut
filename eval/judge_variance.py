"""How much does the judge move when nothing else does?

    python eval/judge_variance.py v2-rejudged v2-rejudged-b

Every rate this project publishes assumes the judge is a fixed instrument. Nothing
has ever tested that. The one accidental repeat, v6 `md-vidsmith` judged twice by
`openai/gpt-oss-120b` on the same stored body, came back 1/20 and then 2/20: one
sentence, and the source's rate doubled.

This compares two judgements of the *same bodies* by the same judge. Generation is
not repeated and cannot contribute, so whatever differs is the judge alone.

Unlike a rate, this does not need the full golden set. Self-agreement is measured
per source, so `--only` on a handful of sources answers the question at a fraction
of the budget:

    python eval/rejudge.py v2 --suffix -rejudged-b --only md-n8n,md-geojit,art-ocr

No model calls happen here. This reads what those runs already stored.
"""

from __future__ import annotations

import argparse
import json
import math
from math import erfc
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

GREEN, RED, YELLOW, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def judged(run: str) -> dict[str, tuple[int, int]]:
    """Source id to (unsupported, judged) for every source that was judged."""
    out: dict[str, tuple[int, int]] = {}
    for path in sorted((RESULTS / run / "sources").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("judged_claims"):
            out[data["id"]] = (data["judged_unsupported"], data["judged_claims"])
    return out


def compare(first: dict, second: dict) -> dict:
    """Per-source disagreement between two judgements of the same bodies."""
    shared = sorted(set(first) & set(second))
    rows = []
    for source in shared:
        (u1, c1), (u2, c2) = first[source], second[source]
        rows.append(
            {
                "id": source,
                "first": (u1, c1),
                "second": (u2, c2),
                # A differing sentence count means the two passes did not even agree
                # on what was checkable, which is a bigger disagreement than the
                # verdicts and is why it is reported separately.
                "same_denominator": c1 == c2,
                "delta": u2 - u1,
            }
        )
    moved = [r for r in rows if r["delta"]]
    u1 = sum(r["first"][0] for r in rows)
    c1 = sum(r["first"][1] for r in rows)
    u2 = sum(r["second"][0] for r in rows)
    c2 = sum(r["second"][1] for r in rows)
    return {
        "sources": len(rows),
        "rows": rows,
        "sources_that_moved": len(moved),
        "claims_that_moved": sum(abs(r["delta"]) for r in rows),
        "mismatched_denominators": [r["id"] for r in rows if not r["same_denominator"]],
        "first_rate": (u1 / c1) if c1 else None,
        "second_rate": (u2 / c2) if c2 else None,
        "first_totals": (u1, c1),
        "second_totals": (u2, c2),
    }


def _z(a: int, na: int, b: int, nb: int) -> tuple[float, float]:
    pooled = (a + b) / (na + nb)
    se = math.sqrt(pooled * (1 - pooled) * (1 / na + 1 / nb))
    if se == 0:
        return 0.0, 1.0
    z = (a / na - b / nb) / se
    return z, erfc(abs(z) / math.sqrt(2))


def report(result: dict) -> None:
    print(f"{result['sources']} source(s) judged twice by the same judge, same bodies\n")
    for row in result["rows"]:
        u1, c1 = row["first"]
        u2, c2 = row["second"]
        mark = "" if not row["delta"] else f"  {YELLOW}{row['delta']:+d}{OFF}"
        note = "" if row["same_denominator"] else f"  {RED}denominator moved{OFF}"
        print(f"  {row['id']:<20} {u1}/{c1}  ->  {u2}/{c2}{mark}{note}")

    u1, c1 = result["first_totals"]
    u2, c2 = result["second_totals"]
    print()
    print(f"the judge changed its mind on {result['claims_that_moved']} claim(s) "
          f"across {result['sources_that_moved']} of {result['sources']} source(s)")
    if result["mismatched_denominators"]:
        print(f"{RED}  and did not agree what was checkable in: "
              f"{', '.join(result['mismatched_denominators'])}{OFF}")
    print(f"rate  {u1}/{c1} = {u1/c1:.1%}   ->   {u2}/{c2} = {u2/c2:.1%}")

    # Not a significance test of an improvement. Both numbers describe the same
    # bodies, so any gap here is the instrument, and a p-value only says whether
    # the instrument's own wobble is large enough to notice at this sample size.
    z, p = _z(u1, c1, u2, c2)
    swing = abs(u1 / c1 - u2 / c2)
    print(f"{DIM}  same bodies, so this gap is the judge: {swing:.1%} "
          f"(z={z:.2f}, p={p:.3f}){OFF}")
    if swing:
        print(f"{YELLOW}  treat differences below about {swing:.1%} between runs as "
              f"indistinguishable from the judge moving{OFF}")
    else:
        print(f"{GREEN}  the judge reproduced itself exactly on these sources{OFF}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("first", help="a judged run, e.g. v2-rejudged")
    ap.add_argument("second", help="a second judgement of the same bodies")
    args = ap.parse_args(argv)

    first, second = judged(args.first), judged(args.second)
    if not first or not second:
        print(f"{RED}nothing judged in one of those runs{OFF}")
        return 1
    shared = set(first) & set(second)
    if not shared:
        print(f"{RED}no source was judged in both{OFF}")
        return 1
    report(compare(first, second))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
