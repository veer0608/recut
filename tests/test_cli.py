"""The CLI, actually invoked.

Written after `python -m recut run` shipped broken: a NameError on the first line
of main(), which every other test walked straight past because none of them called
main(). A module that imports cleanly is not a module that runs.
"""

import pytest

from recut import cli


@pytest.fixture
def post(tmp_path):
    path = tmp_path / "post.md"
    path.write_text(
        "# Why Your Bank Statement Lies\n\n"
        "The merchant name is typed by the payment processor, not the shop.\n",
        encoding="utf-8",
    )
    return path


class TestArgumentHandling:
    """Everything reachable before the first model call, which is all of it that
    can be tested without spending money."""

    def test_an_unknown_target_exits_two(self, post, capsys, tmp_path):
        code = cli.main(["run", str(post), "--targets", "tiktok", "--env", str(tmp_path / "x")])
        assert code == 2
        assert "tiktok" in capsys.readouterr().err

    def test_a_missing_source_exits_one(self, tmp_path, capsys):
        code = cli.main(["run", str(tmp_path / "nope.md"), "--env", str(tmp_path / "x")])
        assert code == 1
        assert "could not read" in capsys.readouterr().err

    def test_an_empty_source_exits_one(self, tmp_path, capsys):
        empty = tmp_path / "empty.md"
        empty.write_text("   \n\n", encoding="utf-8")
        assert cli.main(["run", str(empty), "--env", str(tmp_path / "x")]) == 1

    def test_a_target_the_source_cannot_support_is_skipped_not_crashed(
        self, post, capsys, tmp_path
    ):
        # article needs a timed source. Asking for it alone leaves nothing to run.
        code = cli.main(["run", str(post), "--targets", "article", "--env", str(tmp_path / "x")])
        assert code == 2
        assert "no target can run" in capsys.readouterr().err

    def test_no_subcommand_is_a_usage_error(self):
        with pytest.raises(SystemExit):
            cli.main([])


class TestConsoleEncoding:
    def test_utf8_stdout_survives_a_stream_that_refuses(self, monkeypatch):
        class Stubborn:
            def reconfigure(self, **kwargs):
                raise ValueError("nope")

        monkeypatch.setattr(cli.sys, "stdout", Stubborn())
        monkeypatch.setattr(cli.sys, "stderr", Stubborn())
        cli._utf8_stdout()  # must not raise

    def test_utf8_stdout_survives_a_stream_without_reconfigure(self, monkeypatch):
        monkeypatch.setattr(cli.sys, "stdout", object())
        monkeypatch.setattr(cli.sys, "stderr", object())
        cli._utf8_stdout()  # must not raise

    def test_a_title_a_windows_console_cannot_encode_does_not_crash_the_run(
        self, tmp_path, capsys, monkeypatch
    ):
        # The bug this guards: an arrow in a heading killed the run at the print,
        # after every model call had already been paid for.
        path = tmp_path / "arrow.md"
        path.write_text("# Statement → reconciled rows\n\nSome body text here.\n", encoding="utf-8")

        # Stop at the model step without going near the network. Whether a key
        # happens to be in the environment is not this test's business, and the
        # first version of this test spent thirteen seconds finding that out.
        def no_llm(*args, **kwargs):
            raise cli.LLMError("no key")

        monkeypatch.setattr(cli, "LLM", no_llm)
        assert cli.main(["run", str(path), "--env", str(tmp_path / "x")]) == 1
        assert "reconciled rows" in capsys.readouterr().out


class TestBuildApproved:
    """Rendering hangs off the approval, not off the run that made the draft.

    A render is a minute or two of compute plus the video tool's own model
    calls. Spending that at generation time spends it on drafts a reviewer may
    reject, in a project whose stance is that nothing reaches publication
    unreviewed.
    """

    def _queue(self, tmp_path, state=None, files=None):
        from recut.review import ReviewQueue

        queue = ReviewQueue(tmp_path / "q.db")
        draft_id = queue.add(
            "job",
            {"title": "T", "ref": "r", "raw": "x"},
            {
                "target": "vidsmith",
                "body": "Narration.",
                "sentences": [],
                "warnings": [],
                "coverage": 1.0,
                "clean": True,
                "files": files if files is not None else {"vidsmith/script.md": "# S\n"},
            },
        )
        if state:
            queue.set_state(draft_id, state)
        return queue, draft_id

    def test_a_pending_draft_is_not_built(self, tmp_path, monkeypatch, capsys):
        from recut import cli

        self._queue(tmp_path, state=None)
        called = []
        monkeypatch.setattr(cli, "build_video", lambda *a, **k: called.append(a) or (None, "x"))
        code = cli.main(["build", "--db", str(tmp_path / "q.db"), "--out", str(tmp_path / "o")])
        assert code == 0
        assert called == []

    def test_an_approved_draft_is_built(self, tmp_path, monkeypatch):
        from pathlib import Path

        from recut import cli

        self._queue(tmp_path, state="approved")
        seen = {}

        def fake_build(project, **kwargs):
            seen["project"] = Path(project)
            out = Path(project) / "out"
            out.mkdir(parents=True, exist_ok=True)
            mp4 = out / "v-9x16.mp4"
            mp4.write_bytes(b"0" * 10)
            return mp4, None

        monkeypatch.setattr(cli, "build_video", fake_build)
        code = cli.main(["build", "--db", str(tmp_path / "q.db"), "--out", str(tmp_path / "o")])
        assert code == 0
        # The emitted project is written back out before being handed over.
        assert (seen["project"] / "script.md").read_text(encoding="utf-8") == "# S\n"

    def test_two_drafts_do_not_overwrite_each_other(self, tmp_path, monkeypatch):
        from pathlib import Path

        from recut import cli

        queue, first = self._queue(tmp_path, state="approved")
        second = queue.add(
            "job",
            {"title": "T", "ref": "r", "raw": "x"},
            {"target": "vidsmith", "body": "b", "sentences": [], "warnings": [],
             "coverage": 1.0, "clean": True, "files": {"vidsmith/script.md": "# Other\n"}},
        )
        queue.set_state(second, "approved")

        roots = []
        monkeypatch.setattr(cli, "build_video", lambda p, **k: roots.append(Path(p)) or (None, "no"))
        cli.main(["build", "--db", str(tmp_path / "q.db"), "--out", str(tmp_path / "o")])
        assert len({str(r) for r in roots}) == 2

    def test_an_approved_prose_draft_is_skipped_not_failed(self, tmp_path, monkeypatch):
        from recut import cli

        self._queue(tmp_path, state="approved", files={})
        monkeypatch.setattr(cli, "build_video", lambda *a, **k: (None, "should not run"))
        assert cli.main(["build", "--db", str(tmp_path / "q.db"), "--out", str(tmp_path / "o")]) == 0

    def test_a_failed_render_gets_its_own_exit_code(self, tmp_path, monkeypatch):
        from recut import cli

        self._queue(tmp_path, state="approved")
        monkeypatch.setattr(cli, "build_video", lambda *a, **k: (None, "vidsmith exited 1"))
        assert cli.main(["build", "--db", str(tmp_path / "q.db"), "--out", str(tmp_path / "o")]) == 4


class TestPostCommand:
    """The dry run is the safety property, so it is the one tested hardest."""

    def _queued(self, tmp_path, state="approved", target="thread"):
        from recut.review import ReviewQueue

        db = tmp_path / "q.db"
        queue = ReviewQueue(db)
        body = chr(10).join(["One post.", "", "Two post."])
        draft_id = queue.add("j1", {"title": "t"}, {"target": target, "body": body})
        if state != "pending":
            queue.set_state(draft_id, state)
        return db, draft_id

    def test_the_default_posts_nothing_even_with_credentials_present(
        self, tmp_path, monkeypatch, capsys
    ):
        db, _ = self._queued(tmp_path)

        def explode(*a, **k):
            raise AssertionError("a dry run reached the network")

        monkeypatch.setattr("recut.post.httpx.post", explode)
        for name in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"):
            monkeypatch.setenv(name, "set")

        assert cli.main(["post", "--db", str(db)]) == 0
        out = capsys.readouterr().out
        assert "dry run" in out and "One post." in out

    def test_a_dry_run_leaves_the_draft_approved(self, tmp_path, capsys):
        from recut.review import ReviewQueue

        db, draft_id = self._queued(tmp_path)
        cli.main(["post", "--db", str(db)])
        assert ReviewQueue(db).get(draft_id)["state"] == "approved"

    def test_confirm_without_credentials_fails_before_any_request(
        self, tmp_path, monkeypatch, capsys
    ):
        db, _ = self._queued(tmp_path)

        def explode(*a, **k):
            raise AssertionError("tried to post without credentials")

        monkeypatch.setattr("recut.post.httpx.post", explode)
        for name in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"):
            monkeypatch.delenv(name, raising=False)

        assert cli.main(["post", "--db", str(db), "--confirm", "--env", str(tmp_path / "none")]) == 3

    def test_naming_an_unapproved_draft_is_refused_and_says_why(self, tmp_path, capsys):
        # Without --draft only approved drafts are listed at all, so this is the
        # path where a human points at one by id and the state check earns its keep.
        db, draft_id = self._queued(tmp_path, state="pending")
        assert cli.main(["post", "--db", str(db), "--draft", draft_id]) == 0
        assert "not approved" in capsys.readouterr().out

    def test_an_unapproved_draft_is_never_listed_for_posting(self, tmp_path, capsys):
        db, _ = self._queued(tmp_path, state="pending")
        assert cli.main(["post", "--db", str(db)]) == 0
        assert "nothing approved" in capsys.readouterr().out

    def test_a_linkedin_draft_is_skipped_by_name(self, tmp_path, capsys):
        db, _ = self._queued(tmp_path, target="linkedin")
        assert cli.main(["post", "--db", str(db)]) == 0
        assert "not postable" in capsys.readouterr().out

    def test_nothing_approved_is_not_an_error(self, tmp_path, capsys):
        from recut.review import ReviewQueue

        db = tmp_path / "empty.db"
        ReviewQueue(db)
        assert cli.main(["post", "--db", str(db)]) == 0
