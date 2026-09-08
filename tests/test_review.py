"""The review queue. Nothing here posts anything; `posted` is a human saying so."""

import pytest

from recut.review import APPROVED, PENDING, POSTED, REJECTED, ReviewQueue, TransitionError

SOURCE = {
    "title": "Why Your Bank Statement Lies",
    "ref": "https://example.com/post",
    "raw": "The merchant name is typed by the payment processor, not the shop.",
}

ARTIFACT = {
    "target": "linkedin",
    "body": "Merchant names come from the payment processor.",
    "clean": True,
    "coverage": 1.0,
    "warnings": [{"rule": "intensity", "severity": "notice", "span": "ensures", "detail": "x"}],
    "sentences": [
        {
            "start": 0,
            "end": 46,
            "text": "Merchant names come from the payment processor.",
            "claim_id": "c0",
            "claim": "The merchant name is typed by the processor.",
            "confidence": 0.8,
            "segments": [{"id": "s0", "text": "The merchant name is typed by the payment processor,", "timecode": None, "char_start": 0, "char_end": 52}],
        }
    ],
}


@pytest.fixture
def queue(tmp_path):
    return ReviewQueue(tmp_path / "q.db")


@pytest.fixture
def draft_id(queue):
    return queue.add("job1", SOURCE, ARTIFACT)


class TestAdding:
    def test_a_new_draft_is_pending(self, queue, draft_id):
        assert queue.get(draft_id)["state"] == PENDING

    def test_provenance_survives_into_the_queue(self, queue, draft_id):
        # Without this the review is just reading prose, which is the thing the
        # whole product exists to stop.
        draft = queue.get(draft_id)
        assert draft["sentences"][0]["claim_id"] == "c0"
        assert draft["sentences"][0]["segments"][0]["id"] == "s0"

    def test_the_source_is_stored_with_the_draft(self, queue, draft_id):
        # A URL can change under you. Reviewing against today's version of a page
        # is not a review of what was actually generated.
        assert "merchant name" in queue.get(draft_id)["source_raw"]

    def test_warnings_survive(self, queue, draft_id):
        assert queue.get(draft_id)["warnings"][0]["span"] == "ensures"

    def test_an_unknown_draft_is_none(self, queue):
        assert queue.get("nope") is None


class TestTransitions:
    def test_pending_can_be_approved(self, queue, draft_id):
        assert queue.set_state(draft_id, APPROVED)["state"] == APPROVED

    def test_pending_can_be_rejected(self, queue, draft_id):
        assert queue.set_state(draft_id, REJECTED)["state"] == REJECTED

    def test_approved_can_be_marked_posted(self, queue, draft_id):
        queue.set_state(draft_id, APPROVED)
        assert queue.set_state(draft_id, POSTED)["state"] == POSTED

    def test_pending_cannot_jump_straight_to_posted(self, queue, draft_id):
        # This is the whole point of the queue. Allowing it would let the review
        # be skipped by the one action that means it went public.
        with pytest.raises(TransitionError, match="Approve it first"):
            queue.set_state(draft_id, POSTED)

    def test_rejected_cannot_jump_straight_to_posted(self, queue, draft_id):
        queue.set_state(draft_id, REJECTED)
        with pytest.raises(TransitionError):
            queue.set_state(draft_id, POSTED)

    def test_a_rejection_can_be_reconsidered(self, queue, draft_id):
        queue.set_state(draft_id, REJECTED)
        assert queue.set_state(draft_id, APPROVED)["state"] == APPROVED

    def test_posted_is_final(self, queue, draft_id):
        queue.set_state(draft_id, APPROVED)
        queue.set_state(draft_id, POSTED)
        with pytest.raises(TransitionError):
            queue.set_state(draft_id, PENDING)

    def test_an_unknown_state_is_refused(self, queue, draft_id):
        with pytest.raises(TransitionError, match="unknown state"):
            queue.set_state(draft_id, "published")

    def test_a_note_is_kept(self, queue, draft_id):
        queue.set_state(draft_id, REJECTED, note="hook is weak")
        assert queue.get(draft_id)["note"] == "hook is weak"

    def test_a_later_change_without_a_note_keeps_the_old_one(self, queue, draft_id):
        queue.set_state(draft_id, REJECTED, note="hook is weak")
        queue.set_state(draft_id, APPROVED)
        assert queue.get(draft_id)["note"] == "hook is weak"

    def test_an_unknown_draft_raises_key_error(self, queue):
        with pytest.raises(KeyError):
            queue.set_state("nope", APPROVED)


class TestListing:
    def test_listing_filters_by_state(self, queue):
        a = queue.add("j", SOURCE, ARTIFACT)
        queue.add("j", SOURCE, ARTIFACT)
        queue.set_state(a, APPROVED)
        assert [d["id"] for d in queue.list(APPROVED)] == [a]
        assert len(queue.list(PENDING)) == 1

    def test_the_list_omits_the_heavy_columns(self, queue, draft_id):
        # sentences and source_raw are by far the largest columns and the list
        # view has no use for them.
        row = queue.list()[0]
        assert "sentences" not in row and "source_raw" not in row
        assert row["target"] == "linkedin"

    def test_counts_cover_every_state_even_at_zero(self, queue, draft_id):
        counts = queue.counts()
        assert counts[PENDING] == 1
        assert counts[POSTED] == 0 and counts[REJECTED] == 0


class TestHttpQueue:
    def test_queueing_an_unfinished_job_is_refused(self, client):
        job_id = client.post("/api/jobs", json={"source": "/nope.md"}).json()["id"]
        response = client.post(f"/api/jobs/{job_id}/queue")
        assert response.status_code in (409, 200)
        if response.status_code == 409:
            assert "nothing to queue" in response.json()["detail"]

    def test_queueing_an_unknown_job_is_404(self, client):
        assert client.post("/api/jobs/nope/queue").status_code == 404

    def test_an_empty_queue_still_reports_counts(self, client):
        body = client.get("/api/queue").json()
        assert body["drafts"] == []
        assert body["counts"]["pending"] == 0

    def test_an_unknown_draft_is_404(self, client):
        assert client.get("/api/queue/nope").status_code == 404

    def test_a_bad_transition_is_409_not_500(self, client):
        assert client.post("/api/queue/nope/state", json={"state": "posted"}).status_code == 404


class TestEmittedFiles:
    """A target that emits a project has to survive the queue to be built.

    The vidsmith target's output is a directory, not prose. Before the drafts
    table carried it, an approved draft had nothing to render and the only way
    to get a video was to render every draft at generation time, including the
    ones a reviewer would go on to reject.
    """

    def _artifact(self, files=None):
        return {
            "target": "vidsmith",
            "body": "Narration that a human can read and judge.",
            "sentences": [],
            "warnings": [],
            "coverage": 1.0,
            "clean": True,
            "files": files if files is not None else {
                "vidsmith/script.md": "# A script\n",
                "vidsmith/config.yaml": "title: A script\n",
            },
        }

    def test_an_emitted_project_survives_the_queue(self, tmp_path):
        queue = ReviewQueue(tmp_path / "q.db")
        draft_id = queue.add("job", SOURCE, self._artifact())
        draft = queue.get(draft_id)
        assert draft["files"]["vidsmith/script.md"] == "# A script\n"

    def test_a_prose_target_stores_no_files_rather_than_null(self, tmp_path):
        queue = ReviewQueue(tmp_path / "q.db")
        draft_id = queue.add("job", SOURCE, {**self._artifact(files={}), "target": "linkedin"})
        assert queue.get(draft_id)["files"] == {}

    def test_a_queue_created_before_the_column_existed_still_opens(self, tmp_path):
        # CREATE TABLE IF NOT EXISTS does nothing to a table already there, so
        # without the migration an older database would keep working and
        # silently drop every emitted project.
        import sqlite3

        path = tmp_path / "old.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE drafts (id TEXT PRIMARY KEY, job_id TEXT, target TEXT NOT NULL,"
            " state TEXT NOT NULL, body TEXT NOT NULL, source_title TEXT, source_ref TEXT,"
            " source_raw TEXT, sentences TEXT, warnings TEXT, coverage REAL, clean INTEGER,"
            " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, note TEXT);"
        )
        conn.commit()
        conn.close()

        queue = ReviewQueue(path)
        draft_id = queue.add("job", SOURCE, self._artifact())
        assert queue.get(draft_id)["files"]["vidsmith/script.md"] == "# A script\n"

    def test_a_row_written_before_the_migration_reads_as_no_files(self, tmp_path):
        import sqlite3

        queue = ReviewQueue(tmp_path / "q.db")
        draft_id = queue.add("job", SOURCE, self._artifact())
        conn = sqlite3.connect(tmp_path / "q.db")
        conn.execute("UPDATE drafts SET files = NULL WHERE id = ?", (draft_id,))
        conn.commit()
        conn.close()
        # Absent is empty, not a crash: an old draft is simply not buildable.
        assert queue.get(draft_id)["files"] == {}
