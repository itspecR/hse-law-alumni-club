import logging
from collections import Counter

from club_api.db.queries import acquire_lock
from club_api.domain import ACHIEVEMENTS, DOMAIN, LEVELS, compute_level

logger = logging.getLogger("club.points")
EVENT_UNDO = "event_undo"
REVOCABLE_TYPES = ("events_attended",)
EVENT_ENTRIES = (
    "SELECT CAST(COALESCE(sum(delta),0) AS SIGNED) AS net, CAST(COALESCE(sum(reason=%s),0) AS SIGNED) AS undos "
    "FROM points_ledger WHERE alumni_id=%s AND ref=%s AND reason IN (%s,%s)"
)


def stats_from_ledger(alumni, ledger):
    counts = Counter(row["reason"] for row in ledger)
    points = alumni.get("points_cached") or 0
    return {
        "programs_completed": counts["program"],
        "events_attended": counts["event"] - counts[EVENT_UNDO],
        "mentorship_count": counts["mentorship"],
        "referrals_count": counts["referral"],
        "orders_count": counts["order"],
        "points": points,
        "verified": int(alumni.get("verification_status") == "verified"),
        "status_level": LEVELS.index(compute_level(points)) + 1,
    }


class Gamification:
    def __init__(self, state):
        self.state = state

    async def stats(self, alumni, ledger, connection=None):
        if connection is None:
            async with self.state.database.connection() as connection:
                return await self.stats(alumni, ledger, connection)
        cursor = await connection.execute(
            "SELECT count(*) AS count FROM orders WHERE alumni_id=%s AND type IN ('merch','mixed')", (alumni["id"],)
        )
        result = stats_from_ledger(alumni, ledger)
        result["orders_count"] = max(result["orders_count"], (await cursor.fetchone())["count"])
        return result

    async def recompute(self, connection, alumni_id):
        cursor = await connection.execute(
            "SELECT CAST(COALESCE(sum(delta),0) AS SIGNED) AS points FROM points_ledger WHERE alumni_id=%s",
            (alumni_id,),
        )
        points = (await cursor.fetchone())["points"]
        level = compute_level(points)["key"]
        await connection.execute(
            "UPDATE alumni SET points_cached=%s,level_cached=%s WHERE id=%s", (points, level, alumni_id)
        )
        return {"points": points, "level": level}

    async def add(self, alumni_id, *, reason, delta=None, ref=None, comment=None, idempotency_key=None):
        async with self.state.database.transaction() as connection:
            result = await self.add_in_transaction(
                connection,
                alumni_id,
                reason=reason,
                delta=delta,
                ref=ref,
                comment=comment,
                idempotency_key=idempotency_key,
            )
        await self.refresh_achievements(alumni_id)
        return result

    async def add_in_transaction(
        self, connection, alumni_id, *, reason, delta=None, ref=None, comment=None, idempotency_key=None
    ):
        if delta is None:
            delta = next((rule["points"] for rule in DOMAIN["point_rules"] if rule["reason"] == reason), 0)
        await connection.execute("SELECT id FROM alumni WHERE id=%s FOR UPDATE", (alumni_id,))
        if idempotency_key:
            await acquire_lock(connection, "points:" + idempotency_key)
            cursor = await connection.execute(
                "SELECT id FROM points_ledger WHERE idempotency_key=%s LIMIT 1", (idempotency_key,)
            )
            if await cursor.fetchone():
                return await self.recompute(connection, alumni_id)
        await self.state.store.create(
            "points_ledger",
            {
                "alumni_id": alumni_id,
                "delta": delta,
                "reason": reason,
                "ref": ref,
                "comment": comment,
                "idempotency_key": idempotency_key,
            },
            connection=connection,
        )
        return await self.recompute(connection, alumni_id)

    async def event_entries(self, connection, alumni_id, event_id):
        cursor = await connection.execute(EVENT_ENTRIES, (EVENT_UNDO, alumni_id, event_id, "event", EVENT_UNDO))
        return await cursor.fetchone()

    async def revoke_achievements(self, alumni_id):
        async with self.state.database.transaction() as connection:
            ledger = await self.state.store.read(
                "points_ledger",
                filters={"alumni_id": {"_eq": alumni_id}},
                fields=("reason",),
                limit=-1,
                connection=connection,
            )
            stats = stats_from_ledger({}, ledger)
            lost = [
                item["key"]
                for item in ACHIEVEMENTS
                if item["rule_json"]["type"] in REVOCABLE_TYPES
                and stats[item["rule_json"]["type"]] < item["rule_json"]["gte"]
            ]
            if not lost:
                return
            defs = await self.state.store.read(
                "achievements", filters={"key": {"_in": lost}}, fields=("id",), limit=-1, connection=connection
            )
            for item in defs:
                await connection.execute(
                    "DELETE FROM alumni_achievements WHERE alumni_id=%s AND achievement_id=%s", (alumni_id, item["id"])
                )

    async def refresh_achievements(self, alumni_id):
        try:
            await self.grant_achievements(alumni_id)
        except Exception:
            logger.error("Не удалось обновить достижения")

    async def grant_achievements(self, alumni_id):
        async with self.state.database.transaction() as connection:
            cursor = await connection.execute(
                "SELECT id,points_cached,verification_status FROM alumni WHERE id=%s FOR UPDATE", (alumni_id,)
            )
            alumni = await cursor.fetchone()
            if not alumni:
                return
            ledger = await self.state.store.read(
                "points_ledger",
                filters={"alumni_id": {"_eq": alumni_id}},
                fields=("reason",),
                limit=-1,
                connection=connection,
            )
            stats = await self.stats(alumni, ledger, connection)
            keys = [
                item["key"]
                for item in ACHIEVEMENTS
                if stats.get(item["rule_json"]["type"], 0) >= item["rule_json"]["gte"]
            ]
            if not keys:
                return
            defs = await self.state.store.read(
                "achievements", filters={"key": {"_in": keys}}, fields=("id",), limit=-1, connection=connection
            )
            existing = await self.state.store.read(
                "alumni_achievements",
                filters={"alumni_id": {"_eq": alumni_id}},
                fields=("achievement_id",),
                limit=-1,
                connection=connection,
            )
            have = {item["achievement_id"] for item in existing}
            for item in defs:
                if item["id"] not in have:
                    await self.state.store.create(
                        "alumni_achievements",
                        {"alumni_id": alumni_id, "achievement_id": item["id"]},
                        connection=connection,
                    )
