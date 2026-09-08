"""Run the golden set and report one number, or refuse to report anything.

Checkpointing is per source. A token cliff mid-run costs the source it was on, not
the run: rerun the same command and it resumes from where it stopped. The
abandonment rule is enforced here rather than left to discipline. If any source did
not complete, the headline rate is withheld and the report says how many are
missing. A rate over the sources that happened to succeed is a rate over the easy
ones, and publishing it would be worse than publishing nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from recut.extract import extract
from recut.ingest import ingest
from recut.llm import GEMINI_MODELS, GROQ_MODEL, LLM, LLMError
from recut.models import Artifact, ClaimSet, Document
from recut.pipeline import applicable, repurpose

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inject import score as score_injections  # noqa: E402
from judge import (  # noqa: E402
    JUDGE_GROQ_MODEL,
    JUDGE_MODELS,
    JUDGE_PROVIDER,
    judge,
    judge_client,
)

# The generators' fallback, deliberately not the judge's model. They walk Gemini
# first and reach this only when Gemini's daily budget is gone, which is exactly
# the case that wiped a whole run when the fallback was taken away. Also not
# openai/gpt-oss-20b, which the sibling reruns benchmark measures against daily.
GENERATOR_GROQ_MODEL = GROQ_MODEL

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden" / "sources.yaml"
RESULTS = HERE / "results"

GREEN, RED, YELLOW, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def load_sources() -> list[dict]:
    raw = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    out = []
    for entry in raw["sources"]:
        ref = entry["ref"]
        if not ref.startswith("http"):
            ref = str((GOLDEN.parent / ref).resolve())
        out.append({**entry, "ref": ref})
    return out


def run_source(
    entry: dict,
    targets: list[str],
    llm: LLM,
    judge_llm: LLM | None,
    seed: int,
) -> dict:
    """Everything measured for one source. Raises if the source cannot complete."""
    document = ingest(entry["ref"])
    wanted = [t for t in targets if t in applicable(document)] or applicable(document)
    claims = repurpose_claims = extract(document, llm)
    _, artifacts = repurpose(document, wanted, llm, claims=claims)

    judged = {}
    if judge_llm is not None:
        for artifact in artifacts:
            judged[artifact.target] = judge(artifact.body, document, judge_llm)

    supported_claims = sum(v["claims"] for v in judged.values())
    unsupported = sum(v["unsupported"] for v in judged.values())

    return {
        "id": entry["id"],
        # Stamped on every checkpoint so an unjudged one can never be silently
        # aggregated into a headline rate later. See the resume guard in main().
        "judged_by_model": judge_llm is not None,
        "kind": entry["kind"],
        "ref": entry["ref"],
        "title": document.title,
        "segments": len(document.segments),
        "chars": len(document.raw),
        "extracted_claims": len(repurpose_claims.claims),
        "dropped_unanchored": repurpose_claims.__dict__.get("_dropped", 0),
        # A quote claim with no verbatim is relabelled opinion rather than dropped.
        # Counted so the fix stays visible: if this goes to zero the extractor has
        # started filling verbatim, and if it climbs the generators are being handed
        # fewer quotations than the source actually contains.
        "demoted_quotes": repurpose_claims.__dict__.get("_demoted_quotes", 0),
        "targets": [a.target for a in artifacts],
        "verifier_errors": {a.target: [str(w) for w in a.errors] for a in artifacts},
        "format_violations": {
            a.target: a.meta["format_violation"]
            for a in artifacts
            if "format_violation" in a.meta
        },
        "repaired": [a.target for a in artifacts if a.meta.get("repaired")],
        # A repair that was attempted and refused used to look exactly like one
        # that never ran. art-willison/linkedin shipped an invented quotation and
        # the stored run could not say whether anything had tried to stop it.
        "repair_attempted": [a.target for a in artifacts if a.meta.get("repair_attempted")],
        "repair_detail": {
            a.target: {
                "kept": a.meta.get("repaired"),
                "first_pass_errors": a.meta.get("first_pass_errors", []),
                "repair_errors": a.meta.get("repair_errors", []),
                "spans_changed": a.meta.get("repair_spans_changed", []),
            }
            for a in artifacts
            if a.meta.get("repair_attempted")
        },
        "claim_utilisation": _utilisation(artifacts, repurpose_claims),
        "judged": judged,
        "judged_claims": supported_claims,
        "judged_unsupported": unsupported,
        "injections": score_injections(artifacts, document, claims, seed=seed),
        "bodies": {a.target: a.body for a in artifacts},
        # The inventory is stored so questions about it can be answered later
        # without paying for extraction again. The copying rate turned out to be
        # a question about claim text, not about generator willpower, and the
        # runs that would have answered it had thrown the inventory away.
        "inventory": {
            # verbatim is stored because its absence is the whole question for a
            # quote claim: text is a restatement by construction, so a quote claim
            # with no verbatim has nothing a generator can legitimately put in
            # quotation marks. art-willison published an invented quotation and the
            # stored inventory could not say which of the two had happened.
            "claims": [
                {"id": c.id, "kind": c.kind, "text": c.text, "verbatim": c.verbatim}
                for c in repurpose_claims.claims
            ],
            "hook_candidates": list(repurpose_claims.hook_candidates),
            "voice_samples": list(repurpose_claims.voice_samples),
            "thesis": repurpose_claims.thesis,
        },
    }


def _utilisation(artifacts: list[Artifact], claims: ClaimSet) -> float | None:
    """Share of extracted claims that at least one output actually used.

    This is utilisation, not recall. Recall would need a hand-labelled inventory of
    what a source *should* yield, which the golden set does not have, and calling
    this recall would overstate what it measures.
    """
    if not claims.claims:
        return None
    used = {cid for a in artifacts for cid in a.claim_ids} & claims.ids
    return len(used) / len(claims.claims)


def clear_stale(sources_dir: Path, ids: set[str] | None = None) -> int:
    """Remove a previous attempt's per-source files. Returns how many went.

    Only the two names this harness writes, and only in the run's own sources/
    directory. `partial/` is deliberately untouched: those are per-window judge
    crumbs, and discarding them is what --fresh is not for.

    `ids` limits it to the sources this run will actually regenerate. Without that
    limit, `--fresh --only art-willison` cleared all fifteen and regenerated one,
    destroying fourteen sources' stored bodies to refresh a single source. Those
    bodies are what rejudge.py and measure_copying read, and they cost a full run
    to produce. A run only ever clears what it is about to rewrite.
    """
    stale = sorted(
        p
        for pattern in ("*.json", "*.error.txt")
        for p in sources_dir.glob(pattern)
        if ids is None or p.name.split(".")[0] in ids
    )
    for path in stale:
        path.unlink()
    return len(stale)


def aggregate(results: list[dict], failures: list[dict], run: dict) -> dict:
    complete = len(failures) == 0
    # A run that skipped the judge measured the deterministic layer only. It has
    # no opinion on entailment, so it must not produce a rate that looks like one.
    judged_run = all(r.get("judged_by_model", True) for r in results) and bool(results)

    # Window size moves the rate, because support is existential per window: a
    # sentence needing two distant paragraphs is supported when one window holds
    # both and not when none does. Sources judged at different sizes are not the
    # same measurement, and mixing them produces a number that means nothing in
    # particular. Enforced here rather than remembered, like the abandonment
    # rule, because the mix is invisible in the output unless something looks.
    sizes = {
        v["window_chars"]
        for r in results
        for v in r.get("judged", {}).values()
        if isinstance(v, dict) and v.get("window_chars")
    }
    one_instrument = len(sizes) <= 1

    judged_claims = sum(r["judged_claims"] for r in results)
    judged_unsupported = sum(r["judged_unsupported"] for r in results)
    planted = sum(r["injections"]["planted"] for r in results)
    caught = sum(r["injections"]["caught"] for r in results)
    bodies = sum(r["injections"]["clean_bodies"] for r in results)
    # Checkpoints written before the rename carry the old key. A run that walls
    # mid-way and resumes across a code change is the normal case here, not an
    # exotic one, so the rename must not turn a resumable run into a crash.
    fp_bodies = sum(
        r["injections"].get("unplanted_error_bodies", r["injections"].get("false_positive_bodies", 0))
        for r in results
    )
    artifacts = sum(len(r["targets"]) for r in results)
    violations = sum(len(r["format_violations"]) for r in results)
    utilisations = [r["claim_utilisation"] for r in results if r["claim_utilisation"] is not None]

    by_rule: dict[str, list[float]] = {}
    for result in results:
        for rule, value in result["injections"]["recall_by_rule"].items():
            by_rule.setdefault(rule, []).append(value)

    # Summed rather than averaged: these are body counts, and a mean over sources
    # would weight a source with three artifacts the same as one with thirty.
    fp_by_rule: dict[str, int] = {}
    for result in results:
        injections = result["injections"]
        by_name = (
            injections["unplanted_errors_by_rule"]
            if "unplanted_errors_by_rule" in injections
            else injections.get("false_positives_by_rule", {})
        )
        for rule, count in by_name.items():
            fp_by_rule[rule] = fp_by_rule.get(rule, 0) + count

    headline = (judged_unsupported / judged_claims) if judged_claims else None
    if not judged_run or not one_instrument:
        headline = None

    return {
        "run": run,
        "complete": complete,
        "judged_by_model": judged_run,
        "judge_window_chars": sorted(sizes),
        "one_instrument": one_instrument,
        "sources_attempted": len(results) + len(failures),
        "sources_completed": len(results),
        "failures": failures,
        # The abandonment rule, enforced rather than remembered. A rate over the
        # sources that happened to survive is a rate over the easy ones.
        "unsupported_claim_rate": headline if complete else None,
        "unsupported_claim_rate_provisional": headline,
        "judged_claims": judged_claims,
        "judged_unsupported": judged_unsupported,
        "verifier_recall_on_injections": (caught / planted) if planted else None,
        "verifier_recall_by_rule": {
            rule: sum(v) / len(v) for rule, v in by_rule.items() if v
        },
        # Not a false-positive rate. See inject.score: three of v6's five were
        # real catches. An upper bound on false alarms, nothing narrower.
        "unplanted_error_rate": (fp_bodies / bodies) if bodies else None,
        "unplanted_errors_by_rule": dict(sorted(fp_by_rule.items())),
        "format_compliance": (1 - violations / artifacts) if artifacts else None,
        "claim_utilisation": (sum(utilisations) / len(utilisations)) if utilisations else None,
        "artifacts": artifacts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_eval", description=__doc__)
    parser.add_argument("--run", default="latest", help="run name, reused to resume")
    parser.add_argument("--targets", default="linkedin,thread", help="comma separated")
    parser.add_argument("--only", default="", help="comma separated source ids")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--fresh", action="store_true", help="ignore existing checkpoints")
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="generate and score deterministically, skip the judge. Cheap, and "
        "produces no unsupported-claim rate at all.",
    )
    args = parser.parse_args(argv)

    load_dotenv(args.env, override=False)
    out_dir = RESULTS / args.run
    (out_dir / "sources").mkdir(parents=True, exist_ok=True)

    # --fresh regenerates every source, so anything already in sources/ describes a
    # run that no longer exists. Left in place, a failed attempt's tracebacks sit
    # beside a later attempt's results and nothing but report.json says which is
    # which: v6 finished 15/15 next to 15 error files from the Groq per-day wall
    # half an hour earlier. Only this run's own per-source files are removed;
    # partial/ holds per-window judge crumbs that are the point of resuming.
    sources = load_sources()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        sources = [s for s in sources if s["id"] in wanted]

    # After --only, never before it: clearing is scoped to the sources this run is
    # about to rewrite, so a one-source refresh cannot take the other fourteen.
    if args.fresh:
        cleared = clear_stale(out_dir / "sources", {s["id"] for s in sources})
        if cleared:
            print(f"{DIM}cleared {cleared} file(s) from a previous {args.run}{OFF}")

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]

    try:
        # Generators walk Gemini and fall back to a Groq model the judge is
        # never given. Checked rather than trusted: the eval means nothing if
        # these two are ever the same string.
        if GENERATOR_GROQ_MODEL == JUDGE_GROQ_MODEL:
            print(
                f"{RED}generators and judge are both pinned to "
                f"{JUDGE_GROQ_MODEL}; a model would be grading its own work{OFF}",
                file=sys.stderr,
            )
            return 1
        llm = LLM(groq_model=GENERATOR_GROQ_MODEL)
        judge_llm = None if args.no_judge else judge_client()
    except LLMError as exc:
        print(f"{RED}{exc}{OFF}", file=sys.stderr)
        return 1

    results: list[dict] = []
    failures: list[dict] = []

    for entry in sources:
        checkpoint = out_dir / "sources" / f"{entry['id']}.json"
        if checkpoint.exists() and not args.fresh:
            cached = json.loads(checkpoint.read_text(encoding="utf-8"))
            # Resuming a judged run on top of --no-judge checkpoints would build a
            # headline out of sources nothing ever judged. Regenerate instead.
            if judge_llm is not None and not cached.get("judged_by_model", True):
                print(f"{DIM}{entry['id']:<20} cached but unjudged, regenerating{OFF}")
            else:
                results.append(cached)
                print(f"{DIM}{entry['id']:<20} cached{OFF}")
                continue

        try:
            result = run_source(entry, targets, llm, judge_llm, args.seed)
        except Exception as exc:  # noqa: BLE001 - a failure here is data, not a crash
            failures.append(
                {"id": entry["id"], "error": f"{type(exc).__name__}: {exc}"[:900]}
            )
            print(f"{RED}{entry['id']:<20} failed  {type(exc).__name__}: {exc}{OFF}"[:200])
            (out_dir / "sources" / f"{entry['id']}.error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
            continue

        # Written before the next source starts, so a cliff costs one source.
        checkpoint.write_text(json.dumps(result, indent=2), encoding="utf-8")
        # A source that failed and now succeeds must stop claiming it failed. The
        # resume path reaches here too, where --fresh never ran to clear it.
        (out_dir / "sources" / f"{entry['id']}.error.txt").unlink(missing_ok=True)
        results.append(result)
        rate = result["judged_unsupported"] / result["judged_claims"] if result["judged_claims"] else None
        shown = f"{rate:.1%}" if rate is not None else "n/a"
        print(
            f"{entry['id']:<20} {result['extracted_claims']:>3} claims  "
            f"{result['judged_claims']:>3} judged  unsupported {shown}"
        )

    run = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": args.seed,
        "targets": targets,
        "judge_provider": None if judge_llm is None else JUDGE_PROVIDER,
        "judge_models": [] if judge_llm is None else list(JUDGE_MODELS),
        "generator_models": [*GEMINI_MODELS, GENERATOR_GROQ_MODEL],
        "model_calls": llm.budget.calls + (judge_llm.budget.calls if judge_llm else 0),
    }
    report = aggregate(results, failures, run)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print()
    print(f"sources     {report['sources_completed']}/{report['sources_attempted']} completed")
    print(f"artifacts   {report['artifacts']}")
    print(
        f"verifier    recall {_pct(report['verifier_recall_on_injections'])} on "
        f"{sum(r['injections']['planted'] for r in results)} planted fabrications, "
        f"errors on unplanted bodies {_pct(report['unplanted_error_rate'])}"
    )
    for rule, value in sorted(report["verifier_recall_by_rule"].items()):
        print(f"            {rule:<10} {_pct(value)}")
    for rule, count in report["unplanted_errors_by_rule"].items():
        print(f"{DIM}            {rule:<10} fired on {count} unplanted bod"
              f"{'y' if count == 1 else 'ies'}{OFF}")
    print(f"format      {_pct(report['format_compliance'])} compliant")
    print(f"utilisation {_pct(report['claim_utilisation'])} of extracted claims used")

    if not report["judged_by_model"]:
        print(f"{YELLOW}no unsupported-claim rate: this run skipped the judge{OFF}")
        print(
            f"{DIM}            the deterministic scores above stand; entailment "
            f"was never measured{OFF}"
        )
    elif report["complete"]:
        print(f"{GREEN}UNSUPPORTED CLAIM RATE  {_pct(report['unsupported_claim_rate'])}{OFF}")
        print(f"            over {report['judged_claims']} judged claims")
    else:
        print(
            f"{YELLOW}no headline rate: {len(failures)} source(s) did not complete{OFF}\n"
            f"{DIM}            provisional over the rest was "
            f"{_pct(report['unsupported_claim_rate_provisional'])}, which is a rate over "
            f"the sources that survived and is not the number{OFF}"
        )
        for failure in failures:
            print(f"{DIM}            {failure['id']}: {failure['error'][:110]}{OFF}")

    print(f"report      {out_dir / 'report.json'}  ({run['model_calls']} model calls)")
    return 0 if report["complete"] else 3


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


if __name__ == "__main__":
    raise SystemExit(main())
