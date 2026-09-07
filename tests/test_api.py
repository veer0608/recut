"""Alignment, the job store, and the HTTP surface. No network, no model calls."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from recut.align import MIN_SCORE, align, coverage, score, sentences_of, tokens
from recut.ingest.markdown import ingest_text
from recut.jobs import JobStore, run_in_thread
from recut.models import Artifact, Claim, ClaimSet

SOURCE = """# Why Your Bank Statement Lies

The merchant name is typed by the payment processor, not the shop.

Settlement and payment dates can be three days apart on a weekend.
"""


@pytest.fixture
def document():
    return ingest_text(SOURCE, source_ref="test")


@pytest.fixture
def claims():
    return ClaimSet(
        document_id="d",
        claims=[
            Claim(id="c0", text="The merchant name is typed by the payment processor.", segment_ids=["s1"]),
            Claim(id="c1", text="Settlement and payment dates can be three days apart.", segment_ids=["s2"], kind="stat"),
        ],
    )


class TestSentenceSplitting:
    def test_offsets_index_the_body(self):
        body = "First claim here. Second claim there.\n\nA third one."
        for start, end, text in sentences_of(body):
            assert body[start:end] == text

    def test_blank_lines_do_not_become_sentences(self):
        assert len(sentences_of("One here.\n\n\n\nTwo there.")) == 2

    def test_an_empty_body_yields_nothing(self):
        assert sentences_of("") == []


class TestScoring:
    def test_stopwords_do_not_count_as_overlap(self):
        assert "the" not in tokens("the merchant and the shop")

    def test_a_matching_sentence_scores(self, claims):
        assert score("Merchant names are typed by the payment processor.", claims.claims[0]) > MIN_SCORE

    def test_an_unrelated_sentence_scores_zero(self, claims):
        assert score("Rome fell slowly over centuries.", claims.claims[0]) == 0.0

    def test_one_shared_word_is_not_a_match(self, claims):
        # "payment" alone should not anchor a sentence to a claim.
        assert score("Payment was late.", claims.claims[0]) == 0.0

    def test_a_shared_number_strengthens_the_match(self, claims):
        with_number = score("Dates can be three days apart.", claims.claims[1])
        assert with_number > 0.5


class TestAlignment:
    def _artifact(self, body, ids=("c0", "c1")):
        return Artifact(target="linkedin", body=body, claim_ids=list(ids))

    def test_each_sentence_gets_its_own_claim(self, claims, document):
        body = (
            "The merchant name is typed by the payment processor. "
            "Settlement and payment dates can be three days apart."
        )
        aligned = align(self._artifact(body), claims, document)
        assert [e["claim_id"] for e in aligned] == ["c0", "c1"]

    def test_a_matched_sentence_carries_its_source_segment(self, claims, document):
        aligned = align(self._artifact("The merchant name is typed by the payment processor."), claims, document)
        assert aligned[0]["segments"][0]["id"] == "s1"
        assert "merchant name" in aligned[0]["segments"][0]["text"]

    def test_an_unmatched_sentence_shows_nothing_rather_than_guessing(self, claims, document):
        # Showing a reader a span that does not support the line they are reading
        # is worse than showing none, because they would believe it.
        aligned = align(self._artifact("Rome fell slowly over many centuries."), claims, document)
        assert aligned[0]["claim_id"] is None
        assert aligned[0]["segments"] == []

    def test_only_cited_claims_are_candidates(self, claims, document):
        # c1 fits this sentence, but the artifact never cited it.
        body = "Settlement and payment dates can be three days apart."
        aligned = align(self._artifact(body, ids=("c0",)), claims, document)
        assert aligned[0]["claim_id"] is None

    def test_coverage_reports_what_the_page_can_show(self, claims, document):
        body = "The merchant name is typed by the payment processor. Rome fell slowly."
        assert coverage(align(self._artifact(body), claims, document)) == 0.5

    def test_coverage_of_nothing_is_zero_not_a_crash(self):
        assert coverage([]) == 0.0


class TestJobStore:
    def test_a_new_job_starts_pending(self, tmp_path):
        store = JobStore(tmp_path / "j.db")
        job = store.get(store.create("post.md", ["linkedin"]))
        assert job["state"] == "pending"
        assert job["targets"] == ["linkedin"]

    def test_an_unknown_job_is_none(self, tmp_path):
        assert JobStore(tmp_path / "j.db").get("nope") is None

    def test_progress_moves_it_to_running(self, tmp_path):
        store = JobStore(tmp_path / "j.db")
        job_id = store.create("x", [])
        store.progress(job_id, "extracting")
        job = store.get(job_id)
        assert job["state"] == "running" and job["progress"] == "extracting"

    def test_a_finished_job_carries_its_result(self, tmp_path):
        store = JobStore(tmp_path / "j.db")
        job_id = store.create("x", [])
        store.finish(job_id, {"artifacts": []})
        job = store.get(job_id)
        assert job["state"] == "done" and job["result"] == {"artifacts": []}

    def test_a_thrown_error_marks_the_job_failed_not_stuck(self, tmp_path):
        # A job stuck at "running" forever is the worst outcome for someone
        # watching a progress line.
        store = JobStore(tmp_path / "j.db")
        job_id = store.create("x", [])

        def boom(report):
            raise ValueError("no transcript")

        run_in_thread(store, job_id, boom).join(timeout=5)
        job = store.get(job_id)
        assert job["state"] == "failed"
        assert "no transcript" in job["error"]

    def test_a_worker_can_report_progress(self, tmp_path):
        store = JobStore(tmp_path / "j.db")
        job_id = store.create("x", [])

        def work(report):
            report("halfway")
            return {"ok": True}

        run_in_thread(store, job_id, work).join(timeout=5)
        assert store.get(job_id)["result"] == {"ok": True}

    def test_recent_lists_newest_first(self, tmp_path):
        store = JobStore(tmp_path / "j.db")
        first = store.create("a", [])
        time.sleep(1.05)  # created_at has second resolution
        second = store.create("b", [])
        assert [j["id"] for j in store.recent()][:2] == [second, first]


class TestHttp:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RECUT_DB", str(tmp_path / "api.db"))
        import importlib

        from recut import api

        importlib.reload(api)
        return TestClient(api.app)

    def test_targets_are_listed(self, client):
        assert "linkedin" in client.get("/api/targets").json()["targets"]

    def test_an_unknown_target_is_rejected_before_a_job_exists(self, client):
        response = client.post("/api/jobs", json={"source": "x.md", "targets": ["tiktok"]})
        assert response.status_code == 400
        assert "tiktok" in response.json()["detail"]
        assert client.get("/api/jobs").json()["jobs"] == []

    def test_an_unknown_job_is_404(self, client):
        assert client.get("/api/jobs/nope").status_code == 404

    def test_a_bad_source_fails_the_job_rather_than_the_request(self, client):
        # The request succeeds because the work happens off-thread. The failure
        # has to surface on the job, which is the thing the page is polling.
        job_id = client.post("/api/jobs", json={"source": "/nope/missing.md"}).json()["id"]
        for _ in range(50):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["state"] in ("done", "failed"):
                break
            time.sleep(0.1)
        assert job["state"] == "failed"
        assert job["error"]

    def test_an_empty_source_is_refused_by_validation(self, client):
        assert client.post("/api/jobs", json={"source": ""}).status_code == 422

    def test_a_key_in_the_request_is_never_written_to_the_database(self, client, tmp_path):
        client.post("/api/jobs", json={"source": "/nope.md", "gemini_key": "SECRET-KEY-123"})
        time.sleep(0.3)
        blob = (tmp_path / "api.db").read_bytes()
        assert b"SECRET-KEY-123" not in blob

    def test_the_page_is_served(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "recut" in response.text.lower()
