"""Score a finished run's bodies again, with the judge as it is now.

A rate is only comparable to another rate that the same judge produced. When
the judge changes, older runs are not wrong, they are on a different scale, and
putting them beside a new number invites a comparison that is not there.

Nothing is regenerated. Every run checkpoints the bodies it produced, so this
costs judging and no generation at all, which also means it works on a day when
only the judge's provider has budget left.

The abandonment rule is not reimplemented here. Results are rebuilt with new
judged counts and handed to run_eval.aggregate, so a source that fails to judge
withholds the headline exactly as it does in a live run.

Source text is re-ingested rather than stored, so this assumes the source has
not drifted since. Verify that before trusting a re-judged number: `chars` in
the old checkpoint against the source today is a cheap first check.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from judge import JUDGE_MODELS, JUDGE_PROVIDER, judge, judge_client  # noqa: E402
from run_eval import RESULTS, aggregate  # noqa: E402

from recut.ingest import ingest  # noqa: E402
from recut.llm import LLMError  # noqa: E402

GREEN, RED, YELLOW, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def rejudge_source(checkpoint: Path, judge_llm, partial_dir: Path | None = None) -> dict:
    """The stored result with its judged numbers replaced. Raises if it cannot.

    `partial_dir` gives each body somewhere to record how far it got, so a quota
    wall part-way through a large source costs the window it was on rather than
    every window already paid for.
    """
    old = json.loads(checkpoint.read_text(encoding="utf-8"))
    document = ingest(old["ref"])

    drift = len(document.raw) - old["chars"]
    judged = {}
    for target, body in old["bodies"].items():
        cache = None if partial_dir is None else partial_dir / f"{old['id']}.{target}.json"
        judged[target] = judge(body, document, judge_llm, cache=cache)

    return {
        **old,
        "judged": judged,
        "judged_claims": sum(v["claims"] for v in judged.values()),
        "judged_unsupported": sum(v["unsupported"] for v in judged.values()),
        "judged_by_model": True,
        "rejudged": {
            "from": old.get("judged_claims"),
            "provider": JUDGE_PROVIDER,
            "models": list(JUDGE_MODELS),
            # Recorded per source rather than asserted once: a run is only as
            # comparable as its least stable source.
            "source_char_drift": drift,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rejudge", description=__doc__)
    ap.add_argument("runs", help="comma separated run names under eval/results")
    ap.add_argument("--suffix", default="-rejudged", help="where to write the new run")
    ap.add_argument("--env", default=".env")
    ap.add_argument(
        "--only",
        default="",
        help="comma separated source ids to judge. Everything already re-judged "
        "still counts toward the report; this only limits what is paid for.",
    )
    args = ap.parse_args(argv)
    load_dotenv(args.env, override=False)

    try:
        judge_llm = judge_client()
    except LLMError as exc:
        print(f"{RED}{exc}{OFF}", file=sys.stderr)
        return 1

    for run in [r.strip() for r in args.runs.split(",") if r.strip()]:
        src_dir = RESULTS / run / "sources"
        if not src_dir.is_dir():
            print(f"{RED}no such run: {run}{OFF}", file=sys.stderr)
            return 1

        out_dir = RESULTS / f"{run}{args.suffix}"
        (out_dir / "sources").mkdir(parents=True, exist_ok=True)
        print(f"\n{run} -> {out_dir.name}")

        # --only limits what is judged, never what is reported. A targeted run
        # still walks every source, so the report is built over everything on
        # disk and a source nobody has judged yet withholds the headline rather
        # than quietly shrinking the denominator to whatever was selected.
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}

        results, failures = [], []
        for checkpoint in sorted(src_dir.glob("*.json")):
            target = out_dir / "sources" / checkpoint.name
            if target.exists():
                results.append(json.loads(target.read_text(encoding="utf-8")))
                print(f"{DIM}  {checkpoint.stem:<20} cached{OFF}")
                continue
            if wanted and checkpoint.stem not in wanted:
                failures.append({"id": checkpoint.stem, "error": "not selected by --only"})
                print(f"{DIM}  {checkpoint.stem:<20} skipped, not selected{OFF}")
                continue
            try:
                result = rejudge_source(checkpoint, judge_llm, out_dir / "partial")
            except Exception as exc:  # noqa: BLE001 - a failure is data
                failures.append({"id": checkpoint.stem, "error": f"{type(exc).__name__}: {exc}"[:900]})
                print(f"{RED}  {checkpoint.stem:<20} failed  {type(exc).__name__}{OFF}")
                continue
            target.write_text(json.dumps(result, indent=2), encoding="utf-8")
            # The source is whole now, so the crumbs that got it there are noise.
            for crumb in (out_dir / "partial").glob(f"{checkpoint.stem}.*.json"):
                crumb.unlink()
            results.append(result)
            rate = result["judged_unsupported"] / result["judged_claims"] if result["judged_claims"] else None
            drift = result["rejudged"]["source_char_drift"]
            note = "" if drift == 0 else f"  {YELLOW}source drifted {drift:+d} chars{OFF}"
            print(f"  {checkpoint.stem:<20} {result['judged_claims']:>3} judged  "
                  f"unsupported {rate:.1%}" if rate is not None else
                  f"  {checkpoint.stem:<20} no judgeable claims")
            if note:
                print(note)

        meta = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rejudged_from": run,
            "judge_provider": JUDGE_PROVIDER,
            "judge_models": list(JUDGE_MODELS),
            "model_calls": judge_llm.budget.calls,
        }
        report = aggregate(results, failures, meta)
        (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

        if report["complete"]:
            print(f"{GREEN}  UNSUPPORTED CLAIM RATE  {report['unsupported_claim_rate']:.1%}{OFF}"
                  f"  over {report['judged_claims']} judged claims")
        else:
            print(f"{YELLOW}  no headline: {len(failures)} source(s) did not judge{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
