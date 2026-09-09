"""Posting to X, offline. The one action here that cannot be undone.

These test refusal more than they test posting. A bug that fails to publish costs
a retry; a bug that publishes the wrong thing, or publishes twice, or publishes
something nobody approved, cannot be walked back.
"""

import pytest

from recut.post import (
    POST_LIMIT,
    Credentials,
    PartialThread,
    PostError,
    auth_header,
    credentials_from_env,
    posts_from,
    refuse_reason,
    send_post,
)
from recut.review import APPROVED, PENDING, POSTED, ReviewQueue, TransitionError

CREDS = Credentials("ck", "cs", "at", "ats")


def draft(**over):
    base = {
        "id": "d1",
        "state": APPROVED,
        "target": "thread",
        "body": "First post.\n\nSecond post.\n\nThird post.",
        "warnings": [],
    }
    return base | over


class TestRefusal:
    def test_an_approved_thread_is_postable(self):
        assert refuse_reason(draft()) is None

    def test_an_unapproved_draft_is_refused(self):
        # The queue enforces this too. Checked here as well because this module is
        # what actually talks to X, and a second lock on that door is cheap.
        assert "not approved" in refuse_reason(draft(state=PENDING))

    def test_a_rejected_draft_is_refused(self):
        assert "not approved" in refuse_reason(draft(state="rejected"))

    def test_a_non_thread_target_is_refused_by_name(self):
        reason = refuse_reason(draft(target="linkedin"))
        assert "linkedin" in reason and "not postable" in reason

    def test_a_vidsmith_draft_is_refused(self):
        assert refuse_reason(draft(target="vidsmith")) is not None

    def test_an_empty_body_is_refused(self):
        assert "empty" in refuse_reason(draft(body="   "))

    def test_an_over_length_post_is_refused_and_says_which(self):
        body = "Fine.\n\n" + ("x" * (POST_LIMIT + 1)) + "\n\nAlso fine."
        reason = refuse_reason(draft(body=body))
        assert "[2]" in reason and str(POST_LIMIT) in reason

    def test_the_limit_is_checked_again_here_not_trusted_from_generation(self):
        # thread.py checks it at write time, but a body can be edited in the queue
        # between then and now, and half a published thread cannot be recalled.
        assert refuse_reason(draft(body="x" * (POST_LIMIT + 1))) is not None


class TestSplitting:
    def test_posts_split_on_the_blank_line_thread_joined_them_with(self):
        assert posts_from(draft()) == ["First post.", "Second post.", "Third post."]

    def test_trailing_and_repeated_blank_lines_do_not_make_empty_posts(self):
        assert posts_from(draft(body="One.\n\n\n\nTwo.\n\n")) == ["One.", "Two."]

    def test_a_single_post_body_is_one_post(self):
        assert posts_from(draft(body="Just the one.")) == ["Just the one."]


class TestCredentials:
    def test_missing_credentials_name_every_one_that_is_missing(self):
        with pytest.raises(PostError) as exc:
            credentials_from_env({"X_API_KEY": "k"})
        message = str(exc.value)
        assert "X_API_SECRET" in message and "X_ACCESS_TOKEN" in message

    def test_an_empty_string_counts_as_missing(self):
        env = dict.fromkeys(
            ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"), "v"
        )
        env["X_ACCESS_TOKEN"] = ""
        with pytest.raises(PostError, match="X_ACCESS_TOKEN"):
            credentials_from_env(env)

    def test_credentials_never_render_themselves(self):
        # A traceback or a log line must not carry these to disk.
        assert "ck" not in repr(CREDS) and "redacted" in repr(CREDS)


class TestSigning:
    def test_the_header_carries_the_expected_oauth_fields(self):
        header = auth_header("POST", "https://api.x.com/2/tweets", CREDS, "abc", "1600000000")
        assert header.startswith("OAuth ")
        for field in (
            "oauth_consumer_key",
            "oauth_nonce",
            "oauth_signature",
            "oauth_signature_method",
            "oauth_timestamp",
            "oauth_token",
        ):
            assert field in header

    def test_the_signature_is_stable_for_the_same_inputs(self):
        a = auth_header("POST", "https://api.x.com/2/tweets", CREDS, "abc", "1600000000")
        b = auth_header("POST", "https://api.x.com/2/tweets", CREDS, "abc", "1600000000")
        assert a == b

    def test_a_different_nonce_changes_the_signature(self):
        a = auth_header("POST", "https://api.x.com/2/tweets", CREDS, "abc", "1600000000")
        b = auth_header("POST", "https://api.x.com/2/tweets", CREDS, "xyz", "1600000000")
        assert a != b

    def test_the_secret_is_not_in_the_header(self):
        header = auth_header("POST", "https://api.x.com/2/tweets", CREDS, "abc", "1600000000")
        assert "cs" not in header.replace("oauth_signature", "")


class TestSending:
    def test_an_http_error_becomes_a_post_error_carrying_the_status(self, monkeypatch):
        class Response:
            status_code = 403
            text = '{"detail":"forbidden"}'

        monkeypatch.setattr("recut.post.httpx.post", lambda *a, **k: Response())
        with pytest.raises(PostError, match="403"):
            send_post("hello", CREDS)

    def test_a_partial_thread_keeps_the_ids_that_did_publish(self, monkeypatch):
        """The failure this is most likely to hit, and the one that loses data.

        Two posts are public. Losing their ids leaves posts on the account that
        nothing here can point at.
        """
        from recut.post import post_thread

        sent = []

        def fake(text, creds, reply_to=None):
            if len(sent) == 2:
                raise PostError("rate limited")
            sent.append(text)
            return f"id{len(sent)}"

        monkeypatch.setattr("recut.post.send_post", fake)
        with pytest.raises(PartialThread) as exc:
            post_thread(["a", "b", "c"], CREDS)
        assert exc.value.published == ["id1", "id2"]
        assert "id1, id2" in str(exc.value)

    def test_each_post_replies_to_the_one_before_it(self, monkeypatch):
        from recut.post import post_thread

        seen = []

        def fake(text, creds, reply_to=None):
            seen.append(reply_to)
            return f"id{len(seen)}"

        monkeypatch.setattr("recut.post.send_post", fake)
        post_thread(["a", "b", "c"], CREDS)
        assert seen == [None, "id1", "id2"]


class TestQueueCannotBeSkippedOrRepeated:
    def test_posted_is_reachable_only_from_approved(self, tmp_path):
        queue = ReviewQueue(tmp_path / "q.db")
        draft_id = queue.add("j1", {"title": "t"}, {"target": "thread", "body": "One."})
        with pytest.raises(TransitionError, match="Approve it first"):
            queue.set_state(draft_id, POSTED)

    def test_a_posted_draft_cannot_be_posted_again(self, tmp_path):
        queue = ReviewQueue(tmp_path / "q.db")
        draft_id = queue.add("j1", {"title": "t"}, {"target": "thread", "body": "One."})
        queue.set_state(draft_id, APPROVED)
        queue.set_state(draft_id, POSTED)
        # posted is terminal: there is no undo for a published post, so the state
        # machine does not pretend there is one.
        with pytest.raises(TransitionError):
            queue.set_state(draft_id, APPROVED)
        assert refuse_reason(queue.get(draft_id)) is not None
