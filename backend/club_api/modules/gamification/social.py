async def record_reaction(state, update, update_id=0):
    user, chat = update.get("user", {}), str(update.get("chat", {}).get("id", ""))
    if chat != state.settings.TELEGRAM_REACTIONS_CHAT_ID or not user.get("id") or user.get("is_bot"):
        return
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
                SocialReactions.objects.create(alumni=owner, chat_id=chat, message_id=update["message_id"], **values)
            elif (reaction.event_at, reaction.update_id) < (update["date"], update_id):
                SocialReactions.objects.filter(pk=reaction.pk).update(**values)

        await connection.run(record)
