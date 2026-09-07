"""What the copying rule would catch, measured over runs that already happened.

The `copying` check is a notice rather than an error because 80% of artifacts
tripped it, and a gate at that rate sends four drafts in five back for repair.
Promoting it needs a measured drop, so this script exists to produce that number
cheaply and repeatedly.

It costs nothing. The eval checkpoints already store every generated body, and
the source is re-ingested rather than re-generated, so no model is called and the
same command can be run after every prompt edit.

Sources are cached under eval/.cache/ because ingesting an article is a network
fetch and the article's text is the one input that must not drift between a
before and an after measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from recut.ingest import ingest
from recut.verify import MIN_COPIED_RUN, longest_copied_run

ROOT = pathlib.Path(__file__).resolve().parent
CACHE = ROOT / ".cache"


def source_text(ref: str, kind: str) -> str | None:
    """The source as text, from cache when we have already paid for it."""
    key = hashlib.sha256(f"{kind}:{ref}".encode()).hexdigest()[:16]
    hit = CACHE / f"{key}.txt"
    if hit.exists():
        return hit.read_text(encoding="utf-8")
    try:
        text = ingest(ref).text
    except Exception as exc:  # a rotted URL is a skipped row, not a crash
        print(f"    ingest failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    CACHE.mkdir(exist_ok=True)
    hit.write_text(text, encoding="utf-8")
    return text


def rows_for(run: str) -> list[dict]:
    out = []
    for path in sorted((ROOT / "results" / run / "sources").glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        text = source_text(d["ref"], d["kind"])
        if text is None:
            continue
        for target, body in d["bodies"].items():
            length, span = longest_copied_run(body, text)
            out.append(
                {
                    "run": run,
                    "source": d["id"],
                    "kind": d["kind"],
                    "target": target,
                    "longest": length,
                    "span": span,
                    "trips": length >= MIN_COPIED_RUN,
                }
            )
    return out


def inventory_rows(run: str) -> list[dict]:
    """How close the claim inventory itself sits to the source.

    A generator never sees the source, so a long verbatim run in an output arrived
    through a claim. Measuring the inventory says whether the copying is authored
    at generation or inherited from extraction, which are different fixes in
    different prompts.

    Runs recorded before checkpoints captured the inventory return nothing, which
    is a missing measurement and not a zero.
    """
    out = []
    for path in sorted((ROOT / "results" / run / "sources").glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        inv = d.get("inventory")
        if not inv:
            continue
        text = source_text(d["ref"], d["kind"])
        if text is None:
            continue
        for claim in inv["claims"]:
            length, span = longest_copied_run(claim["text"], text)
            out.append(
                {
                    "run": run,
                    "source": d["id"],
                    "kind": d["kind"],
                    "claim": claim["id"],
                    "claim_kind": claim["kind"],
                    "longest": length,
                    "words": len(claim["text"].split()),
                    "span": span,
                    "trips": length >= MIN_COPIED_RUN,
                }
            )
    return out


def report_inventory(rows: list[dict]) -> None:
    if not rows:
        print()
        print(
            "inventory: not captured in these runs. Checkpoints only began "
            "storing claim text later, and an absent measurement is not a zero."
        )
        return
    n = sum(r["trips"] for r in rows)
    print()
    print(f"claims measured: {len(rows)}")
    print(f"claims carrying a {MIN_COPIED_RUN}+ word verbatim run: {n}/{len(rows)} = {n / len(rows):.0%}")
    ratios = [r["longest"] / r["words"] for r in rows if r["words"]]
    if ratios:
        print(f"mean share of a claim that is verbatim source: {sum(ratios) / len(ratios):.0%}")
    for kind in sorted({r["claim_kind"] for r in rows}):
        sub = [r for r in rows if r["claim_kind"] == kind]
        k = sum(r["trips"] for r in sub)
        print(f"  {kind:10} {k}/{len(sub)} = {k / len(sub):.0%}")


def report(rows: list[dict], show_spans: int) -> None:
    def rate(subset):
        if not subset:
            return "n/a"
        n = sum(r["trips"] for r in subset)
        return f"{n}/{len(subset)} = {n / len(subset):.0%}"

    print(f"\nartifacts measured: {len(rows)}   threshold: {MIN_COPIED_RUN} words")
    print(f"overall            {rate(rows)}")
    for key in ("run", "kind", "target"):
        print(f"\nby {key}")
        for value in sorted({r[key] for r in rows}):
            subset = [r for r in rows if r[key] == value]
            longest = max((r["longest"] for r in subset), default=0)
            print(f"  {value:10} {rate(subset):14} worst run {longest} words")

    tripped = sorted((r for r in rows if r["trips"]), key=lambda r: -r["longest"])
    if tripped:
        lengths = [r["longest"] for r in tripped]
        print(f"\nwhen it trips: median {sorted(lengths)[len(lengths) // 2]} words, max {max(lengths)}")
    if show_spans:
        print(f"\nthe {min(show_spans, len(tripped))} longest copied runs")
        for r in tripped[:show_spans]:
            print(f"  [{r['longest']:2}w] {r['run']} {r['source']}/{r['target']}")
            print(f"       {r['span'][:150]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", default="v1,v2", help="comma separated run names under eval/results")
    ap.add_argument("--spans", type=int, default=12, help="how many copied runs to print")
    ap.add_argument("--json", help="write the per-artifact rows here")
    args = ap.parse_args()

    rows: list[dict] = []
    inv: list[dict] = []
    for run in args.runs.split(","):
        run = run.strip()
        if not (ROOT / "results" / run).exists():
            print(f"no such run: {run}", file=sys.stderr)
            continue
        print(f"reading {run} ...", file=sys.stderr)
        rows += rows_for(run)
        inv += inventory_rows(run)

    if not rows:
        sys.exit("nothing measured")
    report(rows, args.spans)
    report_inventory(inv)
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nrows written to {args.json}")


if __name__ == "__main__":
    main()
