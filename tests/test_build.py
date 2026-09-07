"""The bridge to vidsmith, without vidsmith.

Nothing here renders anything. A test that shells out to a video pipeline is a
test that takes two minutes, needs ffmpeg and a network, and fails for reasons
that have nothing to do with recut.
"""

import sys
from pathlib import Path

import pytest

from recut.build import build, delivered, find_vidsmith


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "vidsmith"
    root.mkdir()
    (root / "script.md").write_text("# A script\n", encoding="utf-8")
    (root / "config.yaml").write_text("theme: {}\n", encoding="utf-8")
    return root


def _fake_venv(home: Path) -> Path:
    """A checkout whose .venv holds this interpreter, so it is real enough to run."""
    rel = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    target = home / ".venv" / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(Path(sys.executable).read_bytes())
    return target


class TestFinding:
    def test_an_explicit_interpreter_wins(self, tmp_path):
        exe = tmp_path / "python.exe"
        exe.write_text("", encoding="utf-8")
        assert find_vidsmith({"VIDSMITH_PYTHON": str(exe)}) == exe

    def test_a_checkout_is_searched_for_its_venv(self, tmp_path):
        expected = _fake_venv(tmp_path / "vidsmith")
        assert find_vidsmith({"VIDSMITH_HOME": str(tmp_path / "vidsmith")}) == expected

    def test_an_explicit_path_that_does_not_exist_is_not_used(self, tmp_path):
        # Falling through to a sibling checkout would silently build with an
        # interpreter the caller did not ask for.
        found = find_vidsmith({"VIDSMITH_PYTHON": str(tmp_path / "nope")})
        assert found != tmp_path / "nope"

    def test_nothing_configured_and_nothing_beside_us_finds_nothing(self, monkeypatch):
        monkeypatch.setattr("recut.build._venv_python", lambda home: None)
        assert find_vidsmith({}) is None


class TestGuards:
    def test_a_directory_without_a_script_is_refused(self, tmp_path):
        video, problem = build(tmp_path)
        assert video is None
        assert "not a vidsmith project" in problem

    def test_a_missing_vidsmith_names_the_way_to_configure_it(self, project, monkeypatch):
        monkeypatch.setattr("recut.build.find_vidsmith", lambda: None)
        video, problem = build(project)
        assert video is None
        assert "VIDSMITH_PYTHON" in problem and "VIDSMITH_HOME" in problem

    def test_the_guard_runs_before_the_interpreter_is_looked_for(self, tmp_path):
        # Order matters: an empty directory should be reported as an empty
        # directory, not as a missing vidsmith install.
        _, problem = build(tmp_path / "absent")
        assert "not a vidsmith project" in problem


class TestDelivery:
    def test_no_out_directory_delivers_nothing(self, project):
        assert delivered(project) is None

    def test_the_newest_mp4_is_the_delivered_one(self, project):
        out = project / "out"
        out.mkdir()
        for name, when in (("old-9x16.mp4", 1_000_000), ("new-9x16.mp4", 2_000_000)):
            path = out / name
            path.write_bytes(b"")
            import os

            os.utime(path, (when, when))
        assert delivered(project).name == "new-9x16.mp4"

    def test_a_zero_exit_with_no_file_is_an_error_not_a_success(self, project):
        exe = _fake_venv(project.parent / "vs")
        # An interpreter that succeeds and renders nothing, which is the case
        # that would otherwise be reported as a build that worked.
        video, problem = build(project, python=exe, echo=lambda line: None)
        assert video is None
        assert problem is not None
