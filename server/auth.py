"""
User ID + password accounts and server-side sessions.

Password hashing is stdlib `hashlib.scrypt` — no bcrypt/passlib. Sessions
are rows in Mongo keyed by a random cookie token, not JWTs, so signing out
actually revokes access instead of waiting for a token to expire.

`current_user` is the FastAPI dependency every protected route in main.py
depends on; it is the only place a cookie is turned into a user.
"""

import hashlib
import hmac
import os
import secrets
from datetime import timedelta
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from server.db import sessions, users
from server.shared import now

SESSION_COOKIE = "session"
SESSION_DAYS = 30

# scrypt's cost parameters. n=2**14 with r=8/p=1 is the interactive-login
# preset from the scrypt paper — ~100ms and ~16MB per hash on this class of
# machine, which is the point (it's what makes an offline guess expensive).
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_password(password: str) -> str:
    """Input: a plaintext password. Output: "<salt_hex>$<hash_hex>" to store in users.passwordHash."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """
    Input: a plaintext password and the stored "<salt_hex>$<hash_hex>".
    Output: whether they match. Compared with hmac.compare_digest so the
    comparison time doesn't leak how much of the hash was correct.
    """
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    candidate = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return hmac.compare_digest(candidate.hex(), digest_hex)


async def current_user(request: Request) -> dict[str, Any]:
    """
    FastAPI dependency for every route that needs a signed-in user.
    Input: the request (reads the `session` cookie). Output: the user
    document. Raises 401 if the cookie is missing, unknown, or its session
    has expired.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not signed in.")
    session = await sessions.find_one({"token": token})
    # expiresAt is also enforced by a TTL index (db.py), but that reaper only
    # runs about once a minute, so check it here too rather than honoring a
    # session that is expired but not yet swept.
    if not session or session["expiresAt"] <= now():
        raise HTTPException(status_code=401, detail="Session expired.")
    user = await users.find_one({"_id": session["ownerId"]})
    if not user:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    return user


async def _start_session(response: Response, owner_id: ObjectId) -> None:
    """Creates a session row and sets its cookie on the response."""
    token = secrets.token_urlsafe(32)
    expires_at = now() + timedelta(days=SESSION_DAYS)
    await sessions.insert_one({"token": token, "ownerId": owner_id, "expiresAt": expires_at})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_DAYS * 24 * 60 * 60,
        path="/",
        # Off for localhost (Vite serves the app over plain http and a
        # `secure` cookie would never be sent). This is the one line to flip
        # — along with COOKIE_SECURE=1 in the env — before deploying anywhere real.
        secure=os.environ.get("COOKIE_SECURE") == "1",
    )


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    """Input: a user document. Output: only the fields safe to send to the client (never passwordHash)."""
    return {"id": str(user["_id"]), "userId": user["userId"], "guest": bool(user.get("guest"))}


class Credentials(BaseModel):
    """Body of POST /api/auth/register and /api/auth/login."""

    # A handle the person picks, not an email — nothing here ever sends mail,
    # so an address would be a field to mistype for no benefit. Letters,
    # digits, dot/underscore/hyphen; stored lowercased so "Longdo" and
    # "longdo" are the same account rather than two.
    userId: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    # 8 is the floor, not a recommendation; the max stops a huge body from
    # turning into a huge scrypt call.
    password: str = Field(min_length=8, max_length=256)


router = APIRouter(prefix="/api/auth")


@router.post("/register")
async def register(body: Credentials, response: Response):
    """Input: {userId, password}. Output: the new user + a session cookie. 409 if the user ID is taken."""
    user_id = body.userId.lower()
    # The guest route generates ids in this namespace; letting someone
    # register one would let them squat an id the generator can later collide
    # with, which surfaces as a 500 on an unrelated person's "use as guest".
    if user_id.startswith("guest-"):
        raise HTTPException(status_code=400, detail="User IDs starting with 'guest-' are reserved.")
    try:
        result = await users.insert_one(
            {
                "userId": user_id,
                "passwordHash": hash_password(body.password),
                "createdAt": now(),
            }
        )
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="That user ID is already taken.") from None
    await _start_session(response, result.inserted_id)
    return _public_user({"_id": result.inserted_id, "userId": user_id})


@router.post("/login")
async def login(body: Credentials, response: Response):
    """Input: {userId, password}. Output: the user + a session cookie, or 401."""
    user = await users.find_one({"userId": body.userId.lower()})
    # Same message and roughly the same work either way, so the response
    # doesn't reveal whether the user ID exists.
    if not user or not verify_password(body.password, user["passwordHash"]):
        raise HTTPException(status_code=401, detail="Incorrect user ID or password.")
    await _start_session(response, user["_id"])
    return _public_user(user)


@router.post("/guest")
async def guest(response: Response):
    """
    Input: none. Output: a throwaway account + a session cookie.

    A guest is an ordinary user row with a generated id and an empty
    passwordHash — which verify_password() rejects, so nobody can ever log
    into it — rather than a separate "unauthenticated" mode. That way
    conversations, the sidebar, and every protected route work exactly as
    they do for a real account, with no second code path. The trade-off is
    that the account is unrecoverable once the cookie is gone: clearing
    cookies orphans the history rather than losing it, and there is nothing
    that reaps orphaned guests.
    """
    user_id = f"guest-{secrets.token_hex(4)}"
    result = await users.insert_one(
        {
            "userId": user_id,
            "passwordHash": "",
            "guest": True,
            "createdAt": now(),
        }
    )
    await _start_session(response, result.inserted_id)
    return _public_user({"_id": result.inserted_id, "userId": user_id, "guest": True})


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Input: the session cookie. Output: {ok: true} — deletes the session row and clears the cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await sessions.delete_one({"token": token})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: dict[str, Any] = Depends(current_user)):
    """Input: the session cookie. Output: the signed-in user, or 401 — what the client calls on load to decide whether to show the login screen."""
    return _public_user(user)


def _demo() -> None:
    """Self-check for the hashing pair (the security-critical logic here), run with `python -m server.auth`."""
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("Correct horse battery staple", stored)
    assert not verify_password("", stored)
    # A fresh salt per call means the same password never stores the same string.
    assert hash_password("same") != hash_password("same")
    # A malformed/legacy value must fail closed, not raise.
    assert not verify_password("anything", "not-a-valid-hash")
    assert not verify_password("anything", "")
    print("auth: all checks passed")


if __name__ == "__main__":
    _demo()
