from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from .build import build as build_video
from .ingest import ingest, source_kind
from .ingest.markdown import slug
from .llm import LLM, LLMError
from .pipeline import GENERATORS, applicable, repurpose, unsupported, write_out

GREEN, RED, YELLOW, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _utf8_stdout() -> None:
    """Windows consoles default to cp1252 and a source title is arbitrary text.

    A title carrying an arrow or an em dash otherwise takes the whole run down with
    a UnicodeEncodeError, after every model call has already been paid for.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                # A stream that will not be reconfigured is not a reason to abort.
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recut", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="repurpose one source into many pieces")
    run.add_argument(
        "source",
        help="a markdown or text file, an article URL, or a YouTube URL",
    )
    run.add_argument(
        "--targets",
        default="linkedin,thread",
        help=f"comma separated, or 'all'. from: {','.join(GENERATORS)}",
    )
    run.add_argument("--out", default="out", help="output directory (default: out)")
    run.add_argument("--env", default=".env", help="env file holding the API keys")
    run.add_argument(
        "--build",
        action="store_true",
        help="render the vidsmith target to an mp4. Needs a vidsmith checkout: see "
        "VIDSMITH_PYTHON and VIDSMITH_HOME",
    )
    run.add_argument("--aspect", help="override the shape vidsmith renders, e.g. 16:9")

    args = parser.parse_args(argv)
    _utf8_stdout()
    load_dotenv(args.env, override=False)

    print(f"reading  {source_kind(args.source)}")
    try:
        document = ingest(args.source)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"{RED}could not read {args.source}: {exc}{OFF}", file=sys.stderr)
        return 1

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    if targets == ["all"]:
        targets = applicable(document)
    unknown = [t for t in targets if t not in GENERATORS]
    if unknown:
        print(f"{RED}unknown target(s): {', '.join(unknown)}{OFF}", file=sys.stderr)
        return 2

    # Skipping is not the same as failing. A target that cannot run on this source
    # is said out loud and the rest of the run continues.
    skipped = [(t, unsupported(t, document)) for t in targets]
    skipped = [(t, why) for t, why in skipped if why]
    targets = [t for t in targets if t not in {name for name, _ in skipped}]
    if not targets:
        print(f"{RED}no target can run on this source{OFF}", file=sys.stderr)
        return 2

    span = ""
    if document.is_timed and document.segments[-1].t_end:
        span = f", {int(document.segments[-1].t_end) // 60} min of speech"
    print(f"source   {document.title}")
    print(f"         {len(document.segments)} segments, {len(document.raw)} chars{span}")

    try:
        llm = LLM()
        claims, artifacts = repurpose(document, targets, llm)
    except LLMError as exc:
        print(f"{RED}llm unavailable: {exc}{OFF}", file=sys.stderr)
        return 1

    dropped = claims.__dict__.get("_dropped", 0)
    note = f", {dropped} dropped as unanchored" if dropped else ""
    print(f"claims   {len(claims.claims)} anchored{note}")

    for target, why in skipped:
        print(f"{target:<9}{DIM}skipped, {why}{OFF}")

    for artifact in artifacts:
        errors = artifact.errors
        notices = [w for w in artifact.warnings if w.severity == "notice"]
        mark = f"{GREEN}verified{OFF}" if not errors else f"{RED}{len(errors)} unsupported{OFF}"
        detail = artifact.meta.get("format_violation")
        print(f"{artifact.target:<9}{mark}" + (f"  {YELLOW}{detail}{OFF}" if detail else ""))
        for warning in errors:
            print(f"         {RED}x{OFF} {warning.span!r} {DIM}{warning.detail}{OFF}")
        for warning in notices:
            print(f"         {DIM}? {warning.span!r} {warning.detail}{OFF}")

    out_dir = write_out(Path(args.out) / slug(document.title), document, claims, artifacts)
    calls = llm.budget.calls
    print(f"done     {out_dir}  ({calls} model call{'s' if calls != 1 else ''})")

    if args.build:
        # A failed render does not retract the text that was already written and
        # verified, so it is reported and given its own exit code rather than
        # turning the whole run into a failure.
        if "vidsmith" not in [a.target for a in artifacts]:
            print(
                f"{YELLOW}--build had nothing to build: the vidsmith target did not "
                f"run{OFF}",
                file=sys.stderr,
            )
            return 2
        print("build    handing the project to vidsmith")
        video, problem = build_video(
            out_dir / "vidsmith",
            aspect=args.aspect,
            echo=lambda line: print(f"         {DIM}{line}{OFF}"),
        )
        if problem:
            print(f"{RED}build failed: {problem}{OFF}", file=sys.stderr)
            return 4
        size = video.stat().st_size / 1e6
        print(f"video    {GREEN}{video}{OFF}  ({size:.1f} MB)")

    return 0 if all(a.clean for a in artifacts) else 3


if __name__ == "__main__":
    raise SystemExit(main())
