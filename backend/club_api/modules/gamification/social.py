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
