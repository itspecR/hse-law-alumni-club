from collections import Counter
from datetime import UTC, datetime, timedelta

from club_api.core.models import count, group_count
from club_api.db.queries import Query
from club_api.db.store import normalize
from club_api.domain import DOMAIN
from club_api.modules.analytics.pageviews import pageview_stats
from club_api.modules.catalog.scope import PUBLISHED_DPO

ACHIEVEMENT_TITLES = {item["key"]: item["title"] for item in DOMAIN["achievements"]}


def achievement_title(row, fallback):
    return ACHIEVEMENT_TITLES.get(row.get("key"), row.get("title", fallback))


async def sum_field(store, table, field, filters=None):
    rows = await store.aggregate(table, filters=filters, sum_field=field)
    return int(rows[0]["sum"][field] or 0) if rows else 0


async def overview(state):
    store, now = state.store, normalize(datetime.now(UTC))
    orders = await group_count(store, "orders", ("status", "payment_status"))
    alumni = await group_count(store, "alumni", ("verification_status",))
    programs = await group_count(store, "programs", ("enrollment",), PUBLISHED_DPO)
    friends = await group_count(store, "alumni_friends", ("status",), {"status": {"_in": ["accepted", "pending"]}})

    def total(rows, field=None, value=None):
        return sum(row["count"] for row in rows if field is None or row[field] == value)

    events = await store.read(
        "events",
        filters={"status": {"_eq": "published"}, "starts_at": {"_gte": now}},
        fields=("id", "title", "starts_at"),
        sort=("starts_at",),
        limit=1,
    )
    result = {
        "new_orders": total(orders, "status", "new"),
        "orders_count": total(orders),
        "orders_paid": total(orders, "payment_status", "succeeded"),
        "pending_verifications": total(alumni, "verification_status", "pending"),
        "alumni_count": total(alumni),
        "alumni_verified": total(alumni, "verification_status", "verified"),
        "points_total": await sum_field(store, "alumni", "points_cached"),
        "programs_total": total(programs),
        "programs_actual": total(programs) - total(programs, "enrollment", "nonactual"),
        "friendships": total(friends, "status", "accepted"),
        "friend_requests": total(friends, "status", "pending"),
        "podcast_subscribers": await count(store, "alumni", {"podcast_sub_until": {"_gte": now}}),
        "push_subs_count": await count(store, "push_subs"),
        "next_event": {**events[0], "rsvps": await count(store, "event_rsvps", {"event_id": {"_eq": events[0]["id"]}})}
        if events
        else None,
    }
    for key, table in (("products_count", "products"), ("news_count", "news"), ("podcasts_count", "podcasts")):
        result[key] = await count(store, table, {"status": {"_eq": "published"}})
    return result


def days_in_range(days, now):
    return [(now.date() - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def bucket_by_day(values, days):
    counts = Counter(str(value)[:10] for value in values if value)
    return [{"day": day, "count": counts[day]} for day in days]


def top_programs(orders):
    result = {}
    for order in orders:
        seen = set()
        for line in order.get("items_json") or []:
            if not isinstance(line, dict) or line.get("type") != "dpo" or not line.get("ref_id"):
                continue
            ref = str(line["ref_id"]).strip()
            title = str(line.get("title") or ref).strip()
            current = result.setdefault(ref, {"ref_id": ref, "title": title, "qty": 0, "orders": 0})
            qty = line.get("qty")
            current["qty"] += int(qty) if isinstance(qty, int | float) and qty > 0 else 1
            if len(title) >= len(current["title"]):
                current["title"] = title
            if ref not in seen:
                current["orders"] += 1
                seen.add(ref)
    return sorted(result.values(), key=lambda row: (-row["qty"], -row["orders"], row["title"]))[:12]


async def support_stats(state, since):
    open_rows = await state.database.rows(
        Query(
            "SELECT count(*)::int AS count FROM club_support_tickets WHERE expires_at>now() AND status='open'",
            "SELECT count(*) AS count FROM club_support_tickets WHERE expires_at>now() AND status='open'",
        )
    )
    created = await state.database.rows(
        Query(
            "SELECT count(*)::int AS count FROM club_support_tickets WHERE expires_at>now() AND created_at>=%s",
            "SELECT count(*) AS count FROM club_support_tickets WHERE expires_at>now() AND created_at>=%s",
        ),
        (since,),
    )
    return {
        "open": open_rows[0]["count"],
        "created_in_range": created[0]["count"],
        "by_status": await state.database.rows(
            Query(
                "SELECT status,count(*)::int AS count FROM club_support_tickets WHERE expires_at>now() AND created_at>=%s GROUP BY status ORDER BY count DESC",
                "SELECT status,count(*) AS count FROM club_support_tickets WHERE expires_at>now() AND created_at>=%s GROUP BY status ORDER BY count DESC",
            ),
            (since,),
        ),
        "by_topic": await state.database.rows(
            Query(
                "SELECT COALESCE(topic,'(без темы)') AS topic,count(*)::int AS count FROM club_support_tickets WHERE expires_at>now() AND created_at>=%s GROUP BY 1 ORDER BY count DESC LIMIT 12",
                "SELECT COALESCE(topic,'(без темы)') AS topic,count(*) AS count FROM club_support_tickets WHERE expires_at>now() AND created_at>=%s GROUP BY 1 ORDER BY count DESC LIMIT 12",
            ),
            (since,),
        ),
    }


async def analytics(state, range="30d", now=None):
    now = now or datetime.now(UTC)
    days = days_in_range(int(range[:-1]), now)
    since = now - timedelta(days=int(range[:-1]))
    cutoff, store = normalize(since), state.store
    pulse = {}
    specs = {
        "joins": ("alumni", "joined_at", {}),
        "verified_in_range": ("alumni", "verified_at", {"verification_status": {"_eq": "verified"}}),
        "orders_created": ("orders", "created_at", {}),
        "orders_new": ("orders", "created_at", {"status": {"_eq": "new"}}),
        "orders_paid": ("orders", "created_at", {"payment_status": {"_eq": "succeeded"}}),
        "rsvps": ("event_rsvps", "created_at", {}),
        "podcast_plays": ("podcast_plays", "created_at", {}),
        "login_ok": ("audit_log", "created_at", {"event": {"_eq": "login.ok"}}),
        "login_fail": ("audit_log", "created_at", {"event": {"_eq": "login.fail"}}),
        "login_locked": ("audit_log", "created_at", {"event": {"_eq": "login.locked"}}),
        "registers": ("audit_log", "created_at", {"event": {"_eq": "register"}}),
        "referrals_ledger": ("points_ledger", "created_at", {"reason": {"_eq": "referral"}}),
        "referrals_alumni": ("alumni", "joined_at", {"referred_by": {"_nnull": True}}),
        "achievements_granted": ("alumni_achievements", "earned_at", {}),
        "friendships_new": ("alumni_friends", "created_at", {"status": {"_eq": "accepted"}}),
        "push_subs_new": ("push_subs", "created_at", {}),
    }
    for key, (table, field, filters) in specs.items():
        pulse[key] = await count(store, table, {**filters, field: {"_gte": cutoff}})
    support = await support_stats(state, since)
    pulse.update(support_open=support["open"], support_created=support["created_in_range"])
    alumni_count = await count(store, "alumni")
    verified = await count(store, "alumni", {"verification_status": {"_eq": "verified"}})
    order_filters = {"created_at": {"_gte": cutoff}}
    order_rows = await store.read("orders", filters=order_filters, fields=("created_at", "items_json"), limit=-1)
    joins = await store.read("alumni", filters={"joined_at": {"_gte": cutoff}}, fields=("joined_at",), limit=-1)
    pages = await pageview_stats(state, since)
    page_days = {row["day"]: row["count"] for row in pages["by_day"]}

    async def grouped(table, field, time_field="created_at"):
        groups = await group_count(store, table, (field,), {time_field: {"_gte": cutoff}})
        return sorted(
            [
                {"key": str(row[field]) if row[field] is not None else "(пусто)", "count": row["count"]}
                for row in groups
            ],
            key=lambda row: -row["count"],
        )

    achievement_rows = await store.read(
        "alumni_achievements", filters={"earned_at": {"_gte": cutoff}}, fields=("achievement_id",), limit=-1
    )
    ach_counts = Counter(row["achievement_id"] for row in achievement_rows)
    definitions = (
        await store.read(
            "achievements", filters={"id": {"_in": list(ach_counts)}}, fields=("id", "key", "title"), limit=-1
        )
        if ach_counts
        else []
    )
    defs = {row["id"]: row for row in definitions}
    achievements = [
        {
            "achievement_id": id,
            "key": defs.get(id, {}).get("key", id),
            "title": achievement_title(defs.get(id, {}), id),
            "count": amount,
        }
        for id, amount in ach_counts.most_common(12)
    ]
    events = {row["id"]: row["title"] for row in await store.read("events", fields=("id", "title"), limit=-1)}
    rsvps = await store.read(
        "event_rsvps", filters={"created_at": {"_gte": cutoff}}, fields=("event_id", "attended"), limit=-1
    )
    event_counts, attendance = (
        Counter(row["event_id"] for row in rsvps),
        Counter(row["event_id"] for row in rsvps if row["attended"]),
    )
    events_top = [
        {"event_id": id, "title": events.get(id, id), "rsvps": amount, "attended": attendance[id]}
        for id, amount in event_counts.most_common(10)
    ]
    episodes = {row["id"]: row["title"] for row in await store.read("podcasts", fields=("id", "title"), limit=-1)}
    plays = await store.read(
        "podcast_plays", filters={"created_at": {"_gte": cutoff}}, fields=("podcast_id", "alumni_id"), limit=-1
    )
    play_counts = Counter(row["podcast_id"] for row in plays)
    podcasts_top = [
        {
            "podcast_id": id,
            "title": episodes.get(id, id),
            "plays": amount,
            "listeners": len({row["alumni_id"] for row in plays if row["podcast_id"] == id}),
        }
        for id, amount in play_counts.most_common(10)
    ]
    return {
        "range": range,
        "since": cutoff,
        "generated_at": normalize(now),
        "pulse": pulse,
        "snapshot": {
            "alumni_count": alumni_count,
            "alumni_verified": verified,
            "verified_ratio": ((verified * 2000 + alumni_count) // (2 * alumni_count)) / 10 if alumni_count else 0,
        },
        "orders": {
            "by_type": await grouped("orders", "type"),
            "by_status": await grouped("orders", "status"),
            "paid_sum_kop": await sum_field(
                store, "orders", "total_estimate", {**order_filters, "payment_status": {"_eq": "succeeded"}}
            ),
            "programs_top": top_programs(order_rows),
        },
        "community": {"points_by_reason": await grouped("points_ledger", "reason"), "achievements_top": achievements},
        "engagement": {"events_top": events_top, "podcasts_top": podcasts_top},
        "pageviews": {"hits": pages["hits"], "paths_top": pages["paths_top"]},
        "support": support,
        "series": {
            "joins_by_day": bucket_by_day([row["joined_at"] for row in joins], days),
            "orders_by_day": bucket_by_day([row["created_at"] for row in order_rows], days),
            "pageviews_by_day": [{"day": day, "count": page_days.get(day, 0)} for day in days],
        },
    }


def analytics_rows(data):
    rows = [["section", "key", "value"]]
    for key in ("range", "since", "generated_at"):
        rows.append(["meta", key, data[key]])
    for section in ("pulse", "snapshot"):
        rows.extend([section, key, value] for key, value in data[section].items())
    rows.append(["orders", "paid_sum_kop", data["orders"]["paid_sum_kop"]])
    for section, source, key in (
        ("orders_by_type", data["orders"]["by_type"], "key"),
        ("orders_by_status", data["orders"]["by_status"], "key"),
        ("points_by_reason", data["community"]["points_by_reason"], "key"),
        ("achievements", data["community"]["achievements_top"], "title"),
        ("pageviews_paths", data["pageviews"]["paths_top"], "path"),
        ("support_by_status", data["support"]["by_status"], "status"),
        ("support_by_topic", data["support"]["by_topic"], "topic"),
    ):
        rows.extend([section, row[key], row["count"]] for row in source)
    for section, source, first, second in (
        ("programs_top", data["orders"]["programs_top"], "qty", "orders"),
        ("events", data["engagement"]["events_top"], "rsvps", "attended"),
        ("podcasts", data["engagement"]["podcasts_top"], "plays", "listeners"),
    ):
        rows.extend([section, row["title"], f"{row[first]}/{row[second]}"] for row in source)
    rows.append(["pageviews", "hits", data["pageviews"]["hits"]])
    for key in ("open", "created_in_range"):
        rows.append(["support", key, data["support"][key]])
    for key, source in data["series"].items():
        rows.extend([key, row["day"], row["count"]] for row in source)
    return rows
