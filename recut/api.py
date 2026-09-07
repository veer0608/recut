"""The HTTP surface. Submit a source, poll a job, read the result with provenance."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .align import align, coverage
from .extract import extract
from .ingest import ingest, source_kind
from .jobs import JobStore, run_in_thread
from .llm import LLM
from .models import Document
from .pipeline import GENERATORS, applicable, repurpose, unsupported

WEB = Path(__file__).resolve().parent.parent / "web"

load_dotenv(".env", override=False)
app = FastAPI(title="recut", version="0.1.0")
store = JobStore(os.getenv("RECUT_DB", "recut.db"))


class JobRequest(BaseModel):
    source: str = Field(min_length=1, max_length=4000)
    targets: list[str] = Field(default_factory=lambda: ["linkedin", "thread"])
    # Bring your own key. Held for the life of the request and never written to the
    # database, because the job row outlives the run and a key in it would outlive
    # the user's intent.
    gemini_key: str | None = None
    groq_key: str | None = None


def _serialise(document: Document, claims, artifacts) -> dict:
    return {
        "source": {
            "title": document.title,
            "kind": document.source_type,
            "ref": document.source_ref,
            "segments": len(document.segments),
            "chars": len(document.raw),
            "timed": document.is_timed,
            "raw": document.raw,
        },
        "claims": {
            "thesis": claims.thesis,
            "count": len(claims.claims),
            "dropped_unanchored": claims.__dict__.get("_dropped", 0),
        },
        "artifacts": [
            {
                "target": a.target,
                "body": a.body,
                "meta": a.meta,
                "clean": a.clean,
                "warnings": [w.model_dump() for w in a.warnings],
                "files": a.files,
                "sentences": (aligned := align(a, claims, document)),
                "coverage": round(coverage(aligned), 3),
            }
            for a in artifacts
        ],
    }


@app.get("/api/targets")
def list_targets() -> dict:
    return {"targets": list(GENERATORS)}


@app.post("/api/jobs")
def create_job(request: JobRequest) -> dict:
    targets = request.targets or []
    unknown = [t for t in targets if t not in GENERATORS]
    if unknown:
        raise HTTPException(400, f"unknown target(s): {', '.join(unknown)}")

    job_id = store.create(request.source, targets)

    def work(report) -> dict:
        report(f"reading {source_kind(request.source)}")
        document = ingest(request.source)

        wanted = targets or applicable(document)
        skipped = {t: why for t in wanted if (why := unsupported(t, document))}
        runnable = [t for t in wanted if t not in skipped]
        if not runnable:
            raise ValueError("no target can run on this source")

        # If the caller brought a key, the server's own keys are off the table
        # entirely. Falling back to them would spend the host's quota while the
        # page told the user they were spending their own.
        byo = bool(request.gemini_key or request.groq_key)
        llm = LLM(
            gemini_key=request.gemini_key,
            groq_key=request.groq_key,
            allow_env=not byo,
        )
        report(f"extracting claims from {len(document.segments)} segments")
        claims = extract(document, llm)

        report(f"writing {len(runnable)} target(s) from {len(claims.claims)} claims")
        _, artifacts = repurpose(document, runnable, llm, claims=claims)

        payload = _serialise(document, claims, artifacts)
        payload["skipped"] = skipped
        payload["model_calls"] = llm.budget.calls
        payload["byo_key"] = byo
        return payload

    run_in_thread(store, job_id, work)
    return {"id": job_id, "state": "pending"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return job


@app.get("/api/jobs")
def list_jobs() -> dict:
    return {"jobs": store.recent()}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


if WEB.exists():
    app.mount("/static", StaticFiles(directory=WEB), name="static")
