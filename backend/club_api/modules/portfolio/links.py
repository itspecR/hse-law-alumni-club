from club_api.modules.catalog.scope import PUBLISHED
from club_api.modules.portfolio.relations import PROGRAM_TRAINERS

PUBLIC_TRAINER_FIELDS = ("id", "fio", "photo", "bio")


async def published_trainers(state, owner_id, links=PROGRAM_TRAINERS):
    ids = (await links.grouped(state, [owner_id])).get(owner_id, [])
    if not ids:
        return []
    rows = await state.store.read(
        "trainers", filters={"id": {"_in": ids}, **PUBLISHED}, fields=PUBLIC_TRAINER_FIELDS, limit=-1
    )
    found = {row["id"]: row for row in rows}
    return [found[id] for id in ids if id in found]
