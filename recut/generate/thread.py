from __future__ import annotations

from pydantic import BaseModel, Field

from ..extract import render_claims
from ..llm import LLM, load_prompt
from ..models import Artifact, ClaimSet, Document

POST_LIMIT = 280
MIN_POSTS = 4
MAX_POSTS = 10


class _Response(BaseModel):
    posts: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


def build(document: Document, claims: ClaimSet, llm: LLM, note: str = "") -> Artifact:
    prompt = load_prompt("thread", title=document.title, claims=render_claims(claims, document))
    if note:
        prompt = f"{prompt}\n\n---\n{note}"
    response = llm.structured(prompt, _Response)

    posts = [post.strip() for post in response.posts if post.strip()]
    over = [i + 1 for i, post in enumerate(posts) if len(post) > POST_LIMIT]

    meta: dict = {"posts": len(posts), "longest": max((len(p) for p in posts), default=0)}
    if over:
        meta["format_violation"] = f"posts over {POST_LIMIT} chars: {over}"
    elif not MIN_POSTS <= len(posts) <= MAX_POSTS:
        meta["format_violation"] = f"{len(posts)} posts, wanted {MIN_POSTS} to {MAX_POSTS}"

    # The body is what gets verified and what the user copies, so keep it as the
    # plain text a person would actually paste, one post per block.
    body = "\n\n".join(posts)
    return Artifact(target="thread", body=body, claim_ids=response.claim_ids, meta=meta)
