"""Reviewer authentication for review mutations.

Reviewers are configured in the environment, never in code:

    REVIEWER_TOKENS="Dr A. Karimova=long-random-token-1;Dr B. Lee=long-random-token-2"

A request must send `Authorization: Bearer <token>`. The reviewer identity stored
with a review comes from the matched token, never from the request body. With no
reviewers configured, every review mutation is refused (read-only demo mode).
"""
import hmac
import os

from fastapi import Header, HTTPException

MIN_TOKEN_LENGTH = 16


def configured_reviewers() -> dict[str, str]:
    """token -> reviewer name. Entries with short tokens are ignored."""
    out = {}
    for item in os.getenv("REVIEWER_TOKENS", "").split(";"):
        name, sep, token = item.partition("=")
        if sep and name.strip() and len(token.strip()) >= MIN_TOKEN_LENGTH:
            out[token.strip()] = name.strip()
    return out


def reviews_enabled() -> bool:
    return bool(configured_reviewers())


def require_reviewer(authorization: str | None = Header(None)) -> str:
    reviewers = configured_reviewers()
    if not reviewers:
        raise HTTPException(403, "Review mutations are disabled: no reviewers are configured (REVIEWER_TOKENS).")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Reviewer authentication required (Authorization: Bearer <token>).",
                            headers={"WWW-Authenticate": "Bearer"})
    supplied = authorization[7:].strip()
    for token, name in reviewers.items():
        if hmac.compare_digest(supplied.encode(), token.encode()):
            return name
    raise HTTPException(401, "Invalid reviewer token.", headers={"WWW-Authenticate": "Bearer"})
