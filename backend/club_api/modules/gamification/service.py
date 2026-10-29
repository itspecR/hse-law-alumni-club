import logging
from collections import Counter
from datetime import UTC, datetime, timedelta

from club_api.core.models import parse_date
from club_api.db.queries import Query, acquire_lock
from club_api.db.store import normalize
from club_api.domain import ACHIEVEMENTS, DOMAIN, LEVELS, compute_level, decay_delta

logger = logging.getLogger("club.points")
POINT_REASONS = ("program", "event", "referral", "mentorship", "order", "decay", "manual", "achievement")


def stats_from_ledger(alumni, ledger):
    counts = Counter(row["reason"] for row in ledger)
    points = alumni.get("points_cached") or 0
    return {
        "programs_completed": counts["program"],
        "events_attended": counts["event"],
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
            Query(
                "SELECT COALESCE(sum(delta),0)::int AS points FROM points_ledger WHERE alumni_id=%s",
                "SELECT CAST(COALESCE(sum(delta),0) AS SIGNED) AS points FROM points_ledger WHERE alumni_id=%s",
            ),
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
        if reason != "decay":
            await connection.execute("UPDATE alumni SET last_activity_at=now() WHERE id=%s", (alumni_id,))
        return await self.recompute(connection, alumni_id)

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

    async def decay(self, now=None):
        now = now or datetime.now(UTC)
        month, affected = now.strftime("%Y-%m"), 0
        rows = await self.state.store.read(
            "alumni",
            filters={
                "_or": [
                    {"last_activity_at": {"_lte": normalize(now - timedelta(days=30))}},
                    {"last_activity_at": {"_null": True}},
                ]
            },
            fields=("id",),
            limit=-1,
        )
        for row in rows:
            try:
                async with self.state.database.transaction() as connection:
                    cursor = await connection.execute(
                        "SELECT last_activity_at FROM alumni WHERE id=%s FOR UPDATE", (row["id"],)
                    )
                    alumni = await cursor.fetchone()
                    if not alumni or (
                        alumni["last_activity_at"] and parse_date(alumni["last_activity_at"]) > now - timedelta(days=30)
                    ):
                        continue
                    ledger = await self.state.store.read(
                        "points_ledger",
                        filters={"alumni_id": {"_eq": row["id"]}},
                        fields=("delta", "reason", "created_at"),
                        limit=-1,
                        connection=connection,
                    )
                    last = max(
                        (item["created_at"] for item in ledger if item["reason"] == "decay" and item["created_at"]),
                        default=None,
                    )
                    if last and now - parse_date(last) < timedelta(days=27):
                        continue
                    delta = decay_delta(sum(item["delta"] or 0 for item in ledger))
                    if delta >= 0:
                        continue
                    await self.add_in_transaction(
                        connection,
                        row["id"],
                        reason="decay",
                        delta=delta,
                        idempotency_key=f"decay-{row['id']}-{month}",
                        comment="Списание за месяц неактивности",
                    )
                await self.refresh_achievements(row["id"])
                affected += 1
            except Exception:
                logger.error("Не удалось обработать списание баллов")
        return {"month": month, "affected": affected}
