import logging

from club_api.db.queries import Query

logger = logging.getLogger("club.social")
SOCIAL_COUNTS = ("telegram_subscribed", "telegram_reactions")


async def social_progress(state, alumni_id, telegram_id=None):
    stats = {
        "telegram_subscribed": 0,
        "telegram_reactions": 0,
        "subscription": "unavailable" if telegram_id else "not_linked",
        "reactions_available": bool(state.settings.TELEGRAM_REACTIONS_CHAT_ID),
    }
    if not telegram_id or not state.settings.secret("TELEGRAM_BOT_TOKEN") or not state.database.pool:
        return stats
    try:
        rows = await state.database.rows(
            Query(
                "SELECT subscribed FROM club_social_membership WHERE alumni_id=%s AND telegram_id=%s AND checked_at > now() - interval '5 minutes'",
                "SELECT subscribed FROM club_social_membership WHERE alumni_id=%s AND telegram_id=%s AND checked_at > now() - INTERVAL 5 MINUTE",
            ),
            (alumni_id, telegram_id),
        )
        checked = bool(rows)
        if rows:
            stats["telegram_subscribed"] = int(rows[0]["subscribed"])
        else:
            try:
                response = await state.client.post(
                    f"https://api.telegram.org/bot{state.settings.secret('TELEGRAM_BOT_TOKEN')}/getChatMember",
                    json={"chat_id": "@AlumniLawHSE", "user_id": int(telegram_id)},
                    timeout=5,
                )
                data = response.json()
                if response.is_success and data.get("ok") and data.get("result"):
                    member = data["result"]
                    subscribed = member.get("status") in ("creator", "administrator", "member") or (
                        member.get("status") == "restricted" and member.get("is_member") is True
                    )
                    checked = True
                    stats["telegram_subscribed"] = int(subscribed)
                    await state.database.execute(
                        Query(
                            "INSERT INTO club_social_membership(alumni_id,telegram_id,subscribed) VALUES(%s,%s,%s) ON CONFLICT(alumni_id) DO UPDATE SET telegram_id=EXCLUDED.telegram_id,subscribed=EXCLUDED.subscribed,checked_at=now()",
                            "INSERT INTO club_social_membership(alumni_id,telegram_id,subscribed) VALUES(%s,%s,%s) ON DUPLICATE KEY UPDATE telegram_id=VALUES(telegram_id),subscribed=VALUES(subscribed),checked_at=now()",
                        ),
                        (alumni_id, telegram_id, subscribed),
                    )
            except Exception:
                logger.warning("Не удалось проверить подписку Telegram")
        if state.settings.TELEGRAM_REACTIONS_CHAT_ID:
            rows = await state.database.rows(
                Query(
                    "SELECT count(*)::int AS count FROM club_social_reactions WHERE alumni_id=%s AND chat_id=%s AND active=true",
                    "SELECT count(*) AS count FROM club_social_reactions WHERE alumni_id=%s AND chat_id=%s AND active=true",
                ),
                (alumni_id, state.settings.TELEGRAM_REACTIONS_CHAT_ID),
            )
            stats["telegram_reactions"] = rows[0]["count"]
        if checked:
            stats["subscription"] = "subscribed" if stats["telegram_subscribed"] else "not_subscribed"
    except Exception:
        stats.update(telegram_subscribed=0, telegram_reactions=0, subscription="unavailable", reactions_available=False)
    return stats


async def record_reaction(state, update, update_id=0):
    user, chat = update.get("user", {}), str(update.get("chat", {}).get("id", ""))
    if chat != state.settings.TELEGRAM_REACTIONS_CHAT_ID or not user.get("id") or user.get("is_bot"):
        return
    if state.database.vendor == "mysql":
        from club_api.db.models import Alumnus, SocialReactions

        async with state.database.transaction() as connection:
            await connection.lock(f"reaction:{chat}:{update['message_id']}:{user['id']}")

            def record():
                owner = (
                    Alumnus.objects.filter(telegram_id=str(user["id"]), verification_status="verified")
                    .select_for_update()
                    .first()
                )
                if not owner:
                    return
                reaction = SocialReactions.objects.filter(
                    alumni=owner, chat_id=chat, message_id=update["message_id"]
                ).first()
                values = {"active": bool(update["new_reaction"]), "event_at": update["date"], "update_id": update_id}
                if not reaction:
                    SocialReactions.objects.create(
                        alumni=owner, chat_id=chat, message_id=update["message_id"], **values
                    )
                elif (reaction.event_at, reaction.update_id) < (update["date"], update_id):
                    SocialReactions.objects.filter(pk=reaction.pk).update(**values)

            await connection.run(record)
        return
    await state.database.execute(
        "INSERT INTO club_social_reactions(alumni_id,chat_id,message_id,active,event_at,update_id) "
        "SELECT id,%s,%s,%s,%s,%s FROM alumni WHERE telegram_id=%s AND verification_status='verified' "
        "ON CONFLICT(alumni_id,chat_id,message_id) DO UPDATE SET active=EXCLUDED.active,event_at=EXCLUDED.event_at,update_id=EXCLUDED.update_id "
        "WHERE (club_social_reactions.event_at,club_social_reactions.update_id) < (EXCLUDED.event_at,EXCLUDED.update_id)",
        (chat, update["message_id"], bool(update["new_reaction"]), update["date"], update_id, str(user["id"])),
    )
