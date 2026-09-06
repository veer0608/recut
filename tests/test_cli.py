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
