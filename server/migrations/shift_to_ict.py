"""
One-off: shift every stored timestamp from UTC to Vietnam local time (+7h).

Every timestamp this backend writes comes from `server/shared.now()`, which
used to return naive UTC and now returns naive Asia/Ho_Chi_Minh. Rows written
before that change are 7 hours behind the ones written after, so this walks
each collection once and re-labels the old instants.

A flat +7h, not each document's `_id.generation_time`: the goal is a uniform
re-labelling, and `updatedAt`/`endedAt`/`expiresAt` have no relationship to
the id's creation time anyway. Vietnam has had no DST since 1975, so one fixed
offset is exact for every row that can exist here.

Run once, with the server stopped:

    python -m server.migrations.shift_to_ict --yes

It records itself in the `migrations` collection and refuses to run twice —
a second run would push everything to +14h.
"""

import asyncio
import sys
from typing import Any

from server.db import agent_logs, conversations, db, memory_units, sessions, users
from server.shared import now

NAME = "shift_to_ict"
HOURS = 7

# collection -> (top-level date fields, [(array field, its date field), ...])
TARGETS: list[tuple[Any, list[str], list[tuple[str, str]]]] = [
    (users, ["createdAt"], []),
    (sessions, ["expiresAt"], []),
    (conversations, ["createdAt", "updatedAt"], [("messages", "at")]),
    (agent_logs, ["startedAt", "endedAt"], [("steps", "at")]),
    (memory_units, ["at"], []),
]


def _shift(expr: str) -> dict[str, Any]:
    """A `$dateAdd` that leaves non-dates (missing, null) exactly as they were."""
    return {
        "$cond": [
            {"$eq": [{"$type": expr}, "date"]},
            {"$dateAdd": {"startDate": expr, "unit": "hour", "amount": HOURS}},
            expr,
        ]
    }


async def main(confirmed: bool) -> int:
    marker = db["migrations"]
    if await marker.find_one({"_id": NAME}):
        print(f"{NAME} already applied — nothing to do.")
        return 0
    if not confirmed:
        print(__doc__)
        print("Refusing to run without --yes.")
        return 1

    for collection, fields, arrays in TARGETS:
        stage: dict[str, Any] = {f: _shift(f"${f}") for f in fields}
        for array, field in arrays:
            mapped = {
                "$map": {
                    "input": f"${array}",
                    "as": "el",
                    "in": {"$mergeObjects": ["$$el", {field: _shift(f"$$el.{field}")}]},
                }
            }
            # $map over a missing field yields null, which would replace an
            # absent array with an explicit null. Leave those documents alone.
            stage[array] = {"$cond": [{"$isArray": f"${array}"}, mapped, f"${array}"]}
        result = await collection.update_many({}, [{"$set": stage}])
        print(f"{collection.name:<15} {result.modified_count} document(s) shifted +{HOURS}h")

    await marker.insert_one({"_id": NAME, "appliedAt": now(), "hours": HOURS})
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--yes" in sys.argv)))
