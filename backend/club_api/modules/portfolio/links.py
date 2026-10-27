from club_api.modules.catalog.scope import PUBLISHED

LINKS = "program_trainers"
PUBLIC_TRAINER_FIELDS = ("id", "fio", "photo", "bio")


async def program_links(state, program_ids):
    if not program_ids:
        return {}
    rows = await state.store.read(
        LINKS,
        filters={"program_id": {"_in": program_ids}},
        fields=("program_id", "trainer_id"),
        sort=("sort",),
        limit=-1,
    )
    result = {}
    for row in rows:
        result.setdefault(row["program_id"], []).append(row["trainer_id"])
    return result


async def published_trainers(state, program_id):
    ids = (await program_links(state, [program_id])).get(program_id, [])
    if not ids:
        return []
    rows = await state.store.read(
        "trainers", filters={"id": {"_in": ids}, **PUBLISHED}, fields=PUBLIC_TRAINER_FIELDS, limit=-1
    )
    found = {row["id"]: row for row in rows}
    return [found[id] for id in ids if id in found]
