"""Who is calling the workspace API.

Two modes, chosen by configuration:

* RESEARCHER_TOKENS="Name=token;Other=token2" (tokens >= 16 chars): every request needs
  `Authorization: Bearer <token>`; each workspace belongs to the researcher whose token created it and is
  invisible (404) to everyone else.
* No tokens configured: LOCAL SINGLE-USER MODE. Requests are accepted only from the loopback interface
  (the machine itself) and all workspaces belong to "local-user". This is NOT private: anyone with access
  to the machine can read and change them. Requests from other hosts are refused.
"""
import hmac
import os

from fastapi import Header, HTTPException, Request

from app.auth import MIN_TOKEN_LENGTH

LOCAL_USER = "local-user"
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def configured_researchers() -> dict[str, str]:
    out = {}
    for item in os.getenv("RESEARCHER_TOKENS", "").split(";"):
        name, sep, token = item.partition("=")
        if sep and name.strip() and len(token.strip()) >= MIN_TOKEN_LENGTH:
            out[token.strip()] = name.strip()
    return out


def access_mode() -> str:
    return "token" if configured_researchers() else "local_single_user"


def current_researcher(request: Request, authorization: str | None = Header(None)) -> str:
    researchers = configured_researchers()
    if researchers:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "Researcher authentication required (Authorization: Bearer <token>).",
                                headers={"WWW-Authenticate": "Bearer"})
        supplied = authorization[7:].strip()
        for token, name in researchers.items():
            if hmac.compare_digest(supplied.encode(), token.encode()):
                return name
        raise HTTPException(401, "Invalid researcher token.", headers={"WWW-Authenticate": "Bearer"})
    host = request.client.host if request.client else None
    if host in LOOPBACK:
        return LOCAL_USER
    raise HTTPException(403, "Workspaces run in local single-user mode and accept only loopback clients. "
                             "Configure RESEARCHER_TOKENS to use them from other machines.")
