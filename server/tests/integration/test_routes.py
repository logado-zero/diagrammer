"""
Boots the real FastAPI app in-process against a live MongoDB and hits routes
that don't touch a provider (no Anthropic/OpenAI calls anywhere here) — see
CI's `integration-test` job, which supplies MONGODB_URI and a mongo service
container. Run directly against a local MongoDB too:

    MONGODB_URI=mongodb://localhost:27017 python -m pytest server/tests/integration
"""

import asyncio
import secrets

from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from server.db import sessions, users
from server.main import app


async def _run() -> None:
    transport = ASGITransport(app=app)
    created_user_ids: list[ObjectId] = []
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/health")
            assert r.status_code == 200
            assert r.json()["ok"] is True

            r = await client.get("/api/models")
            assert r.status_code == 200
            body = r.json()
            assert "defaultModelId" in body
            assert any(m["id"] == body["defaultModelId"] for m in body["models"])

            # Guest: creates an account + session cookie, kept in the
            # client's cookie jar for the requests that follow.
            r = await client.post("/api/auth/guest")
            assert r.status_code == 200
            guest = r.json()
            assert guest["guest"] is True
            created_user_ids.append(ObjectId(guest["id"]))

            r = await client.get("/api/conversations")
            assert r.status_code == 200
            assert r.json() == [], "a fresh guest must have no saved conversations"

            r = await client.get("/api/conversations/000000000000000000000000")
            assert r.status_code == 404

            r = await client.delete("/api/conversations/000000000000000000000000")
            assert r.status_code == 404

            # Register + login roundtrip, on a fresh client so the guest
            # cookie above doesn't interfere.
            async with AsyncClient(transport=transport, base_url="http://test") as anon:
                user_id = f"itest-{secrets.token_hex(4)}"
                password = "correct horse battery staple"

                r = await anon.post("/api/auth/register", json={"userId": user_id, "password": password})
                assert r.status_code == 200
                created_user_ids.append(ObjectId(r.json()["id"]))

                r = await anon.post("/api/auth/register", json={"userId": user_id, "password": password})
                assert r.status_code == 409, "registering the same userId twice must 409"

            async with AsyncClient(transport=transport, base_url="http://test") as anon:
                r = await anon.post("/api/auth/login", json={"userId": user_id, "password": password})
                assert r.status_code == 200

                r = await anon.get("/api/auth/me")
                assert r.status_code == 200
                assert r.json()["userId"] == user_id

            async with AsyncClient(transport=transport, base_url="http://test") as anon:
                r = await anon.post("/api/auth/login", json={"userId": user_id, "password": "wrong password"})
                assert r.status_code == 401

            async with AsyncClient(transport=transport, base_url="http://test") as anon:
                r = await anon.get("/api/auth/me")
                assert r.status_code == 401, "no cookie must mean not signed in"
    finally:
        # Best-effort cleanup so a local run against a persistent MongoDB
        # doesn't accumulate throwaway accounts — a throwaway CI mongo
        # service container is discarded anyway.
        if created_user_ids:
            await users.delete_many({"_id": {"$in": created_user_ids}})
            await sessions.delete_many({"ownerId": {"$in": created_user_ids}})


def test_routes_smoke():
    asyncio.run(_run())
