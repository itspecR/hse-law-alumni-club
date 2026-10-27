from dataclasses import dataclass

from club_api.core.errors import ApiError


@dataclass(frozen=True)
class LinkTable:
    table: str
    owner: str
    target: str
    ordered: bool = False

    async def grouped(self, state, owner_ids, connection=None):
        if not owner_ids:
            return {}
        rows = await state.store.read(
            self.table,
            filters={self.owner: {"_in": owner_ids}},
            fields=(self.owner, self.target),
            sort=("sort",) if self.ordered else (),
            limit=-1,
            connection=connection,
        )
        result = {}
        for row in rows:
            result.setdefault(row[self.owner], []).append(row[self.target])
        return result

    async def replace(self, state, owner_id, target_ids, connection):
        await state.store.delete(self.table, filters={self.owner: {"_eq": owner_id}}, connection=connection)
        for sort, target_id in enumerate(target_ids):
            row = {self.owner: owner_id, self.target: target_id, **({"sort": sort} if self.ordered else {})}
            await state.store.create(self.table, row, connection=connection)


async def require_existing(state, table, ids, filters, message, connection):
    if not ids:
        return
    rows = await state.store.read(
        table, filters={"id": {"_in": ids}, **filters}, fields=("id",), limit=-1, connection=connection
    )
    if len(rows) != len(ids):
        raise ApiError(400, message)


PROGRAM_TRAINERS = LinkTable("program_trainers", "program_id", "trainer_id", ordered=True)
PROMO_PROGRAMS = LinkTable("promo_code_programs", "promo_id", "program_id")
