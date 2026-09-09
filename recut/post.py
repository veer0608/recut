"""Publish approved drafts to X, and only ones a human approved.

Posting is the one action in this product that cannot be taken back. Everything
else here is reversible: a bad draft is rejected, a bad render is deleted, a bad
number is withdrawn. A published post has been seen. So this module is built to
refuse rather than to try:

- Only a draft in the `approved` state is postable. The queue already enforces
  that `posted` is reachable only from `approved` and is terminal, so a draft
  cannot be posted twice through it.
- Nothing posts without `--confirm`. The default prints exactly what would go
  out, in order, and stops.
- The 280-character limit is checked again here. The generator checks it at
  write time, but the body may have been edited in the queue since, and a
  rejected post halfway through a thread leaves a broken thread behind.

Credentials come from the environment and are never logged. They are the four
OAuth 1.0a values from the X developer portal, which post as one account with no
interactive flow:

    X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass

import httpx

TWEETS_URL = "https://api.x.com/2/tweets"
POST_LIMIT = 280

# Only the thread target. A linkedin body is long-form prose and a vidsmith draft
# is a directory; neither becomes a post by being sent to a post endpoint. Naming
# the supported targets is better than discovering the limit at 280 characters.
POSTABLE = {"thread"}

ENV_KEYS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")


class PostError(RuntimeError):
    """Anything that should stop a post from going out."""


@dataclass(frozen=True)
class Credentials:
    api_key: str
    api_secret: str
    access_token: str
    access_secret: str

    def __repr__(self) -> str:  # pragma: no cover - defensive
        # Never let a traceback or a log line carry these.
        return "Credentials(<redacted>)"


def credentials_from_env(env: dict[str, str] | None = None) -> Credentials:
    source = os.environ if env is None else env
    missing = [name for name in ENV_KEYS if not source.get(name)]
    if missing:
        raise PostError(
            "missing X credentials: " + ", ".join(missing) + ". These are the four "
            "OAuth 1.0a values from the X developer portal, and they belong in .env, "
            "not on the command line."
        )
    return Credentials(*(source[name] for name in ENV_KEYS))


def posts_from(draft: dict) -> list[str]:
    """The individual posts inside a draft body.

    thread.py joins posts with a blank line and keeps the body as the thing a
    human reads and verifies, so that join is the split.
    """
    body = (draft.get("body") or "").strip()
    return [chunk.strip() for chunk in body.split("\n\n") if chunk.strip()]


def refuse_reason(draft: dict) -> str | None:
    """Why this draft must not be posted, or None if it may be."""
    if draft.get("state") != "approved":
        return f"state is {draft.get('state')!r}, not approved"
    if draft.get("target") not in POSTABLE:
        return f"target {draft.get('target')!r} is not postable to X; have {sorted(POSTABLE)}"
    posts = posts_from(draft)
    if not posts:
        return "body is empty"
    over = [i + 1 for i, post in enumerate(posts) if len(post) > POST_LIMIT]
    if over:
        # Checked again at post time: a thread that fails partway through has
        # already published the posts before the failure.
        return f"post(s) {over} exceed {POST_LIMIT} characters"
    return None


# --------------------------------------------------------------------- signing


def _quote(value: str) -> str:
    return urllib.parse.quote(str(value), safe="~")


def _signature(method: str, url: str, params: dict[str, str], creds: Credentials) -> str:
    """OAuth 1.0a HMAC-SHA1 over the oauth_* parameters.

    The request body is JSON, not form-encoded, so it is not part of the
    signature base string. Only the oauth_* parameters are.
    """
    joined = "&".join(f"{_quote(k)}={_quote(params[k])}" for k in sorted(params))
    base = "&".join([method.upper(), _quote(url), _quote(joined)])
    key = f"{_quote(creds.api_secret)}&{_quote(creds.access_secret)}".encode()
    digest = hmac.new(key, base.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def auth_header(
    method: str,
    url: str,
    creds: Credentials,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> str:
    params = {
        "oauth_consumer_key": creds.api_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": creds.access_token,
        "oauth_version": "1.0",
    }
    params["oauth_signature"] = _signature(method, url, params, creds)
    inner = ", ".join(f'{_quote(k)}="{_quote(v)}"' for k, v in sorted(params.items()))
    return f"OAuth {inner}"


# --------------------------------------------------------------------- posting


def send_post(text: str, creds: Credentials, reply_to: str | None = None) -> str:
    """Publish one post and return its id. Raises PostError on anything else."""
    payload: dict = {"text": text}
    if reply_to:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to}
    headers = {
        "Authorization": auth_header("POST", TWEETS_URL, creds),
        "Content-Type": "application/json",
    }
    try:
        response = httpx.post(TWEETS_URL, headers=headers, json=payload, timeout=30)
    except httpx.HTTPError as exc:
        raise PostError(f"network error posting to X: {exc}") from exc
    if response.status_code >= 400:
        # Truncated: an X error body can be long and the useful part is the front.
        raise PostError(f"X returned {response.status_code}: {response.text[:300]}")
    try:
        return response.json()["data"]["id"]
    except (KeyError, ValueError, TypeError) as exc:
        raise PostError(f"unexpected response from X: {response.text[:300]}") from exc


def post_thread(posts: list[str], creds: Credentials) -> list[str]:
    """Publish posts as a chain, each replying to the one before it.

    Returns the ids published so far even when a later post fails, because those
    are already public and the caller has to record them: a thread that stops
    halfway is the failure mode this is most likely to hit, and losing the ids
    would leave posts nobody can find or delete.
    """
    ids: list[str] = []
    for text in posts:
        try:
            ids.append(send_post(text, creds, reply_to=ids[-1] if ids else None))
        except PostError as exc:
            raise PartialThread(ids, str(exc)) from exc
    return ids


class PartialThread(PostError):
    """Some posts went out and then one failed. The published ids are not lost."""

    def __init__(self, published: list[str], reason: str) -> None:
        self.published = published
        self.reason = reason
        super().__init__(
            f"{len(published)} post(s) published, then: {reason}. "
            f"Published ids: {', '.join(published) or 'none'}"
        )
