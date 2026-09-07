"""Hand the emitted project to vidsmith, without taking on vidsmith.

recut writes a vidsmith project and stops there. Rendering it is a second tool's
job, and it pulls in ffmpeg, a speech engine and a video library. A text tool
that cannot start without those is a worse text tool, so this shells out to
vidsmith's own interpreter rather than importing it. That also keeps the two
licences at arm's length: emitting a script is not rendering a video, and the
video half needs its own permission.

The interpreter is looked for in this order, first hit wins:

    VIDSMITH_PYTHON   an explicit path to the interpreter to use
    VIDSMITH_HOME     a checkout, whose .venv is used
    ../vidsmith       a sibling checkout, which is how these repos sit locally
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

# Where a venv puts python, on either platform. Checked in order.
_VENV_PYTHON = ("Scripts/python.exe", "Scripts/python", "bin/python3", "bin/python")


def _venv_python(home: Path) -> Path | None:
    for rel in _VENV_PYTHON:
        candidate = home / ".venv" / rel
        if candidate.exists():
            return candidate
    return None


def find_vidsmith(env: dict[str, str] | None = None) -> Path | None:
    """The interpreter that can run vidsmith, or None if there is not one."""
    env = os.environ if env is None else env

    explicit = env.get("VIDSMITH_PYTHON")
    if explicit and Path(explicit).exists():
        return Path(explicit)

    home = env.get("VIDSMITH_HOME")
    if home:
        found = _venv_python(Path(home))
        if found:
            return found

    sibling = Path(__file__).resolve().parents[2] / "vidsmith"
    return _venv_python(sibling)


def delivered(project: Path) -> Path | None:
    """The newest mp4 vidsmith left behind, or None.

    vidsmith names the file from the config title and the aspect tag, and the
    title is arbitrary text from a model. Reconstructing that name here would be
    a second implementation of a rule that already lives in the other repo and
    would drift from it, so the file is found rather than predicted.
    """
    out = project / "out"
    if not out.is_dir():
        return None
    videos = sorted(out.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return videos[0] if videos else None


def build(
    project: Path,
    *,
    aspect: str | None = None,
    python: Path | None = None,
    echo: Callable[[str], None] = print,
) -> tuple[Path | None, str | None]:
    """Render the project. Returns (mp4, error), exactly one of which is set.

    A build takes minutes, so vidsmith's own output is streamed through rather
    than captured. Watching it work is the difference between a slow command and
    a hung one.
    """
    project = Path(project)
    if not (project / "script.md").exists():
        return None, f"{project} is not a vidsmith project: no script.md"

    python = python or find_vidsmith()
    if python is None:
        return None, (
            "vidsmith not found. Point VIDSMITH_PYTHON at its interpreter, or "
            "VIDSMITH_HOME at the checkout, or put the checkout beside recut."
        )

    command: Iterable[str] = [
        str(python), "-m", "vidsmith", "build", str(project.resolve()),
        *(("--aspect", aspect) if aspect else ()),
    ]

    try:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(python.resolve().parents[2]),
        )
    except OSError as exc:
        return None, f"could not start vidsmith: {exc}"

    assert process.stdout is not None
    for line in process.stdout:
        echo(line.rstrip())
    code = process.wait()

    if code != 0:
        return None, f"vidsmith build exited {code}"

    video = delivered(project)
    if video is None:
        # A zero exit with nothing delivered is worth saying plainly rather than
        # reporting success and leaving the caller to find no file.
        return None, "vidsmith reported success but left no mp4 in out/"
    return video, None


if __name__ == "__main__":  # a thin manual entry point, useful when debugging
    target = Path(sys.argv[1])
    mp4, problem = build(target)
    print(problem or mp4)
    raise SystemExit(1 if problem else 0)
