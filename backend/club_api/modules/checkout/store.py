import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from club_api.core.errors import ApiError
from club_api.db.queries import Query, acquire_lock
from club_api.domain import effective_discount
from club_api.modules.checkout.cart import locked_cart
from club_api.modules.portfolio.seats import require_free_seats

RESERVATION_BATCH_SIZE = 50


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


async def next_order_number(connection):
    await acquire_lock(connection, "order.sequence")
    year = datetime.now(ZoneInfo("Europe/Moscow")).year
    cursor = await connection.execute(
        Query(
            "SELECT COALESCE(MAX(CAST(split_part(number,'-',3) AS integer)),0)+1 AS seq FROM orders WHERE number ~ %s",
            "SELECT COALESCE(MAX(CAST(SUBSTRING_INDEX(number,'-',-1) AS integer)),0)+1 AS seq FROM orders WHERE number REGEXP %s",
        ),
        (f"^ALU-{year}-[0-9]+$",),
    )
    return f"ALU-{year}-{(await cursor.fetchone())['seq']:06d}"


class Checkout:
    def __init__(self, state):
        self.state = state

    async def replay(self, key, request_hash, connection=None):
        if connection is None:
            async with self.state.database.connection() as connection:
                return await self.replay(key, request_hash, connection)
        cursor = await connection.execute(
            "SELECT receipt,request_hash FROM club_checkout_commits WHERE key_hash=%s", (key,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        if row["request_hash"] != request_hash:
            raise ApiError(409, "Этот ключ уже использован для другой заявки. Проверьте сохранённую заявку в кабинете.")
        return row["receipt"]

    async def save_receipt(self, key, receipt):
        await self.state.database.execute(
            "UPDATE club_checkout_commits SET receipt=%s WHERE key_hash=%s", (Jsonb(receipt), key)
        )

    async def commit(self, *, session, key, request_hash, cart_id, cart_items, base):
        async with locked_cart(self.state, session) as connection:
            if previous := await self.replay(key, request_hash, connection):
                return {"replay": previous, "number": previous["number"]}
            cursor = await connection.execute(
                "SELECT items_json FROM carts WHERE id=%s AND session_token=%s FOR UPDATE", (cart_id, session)
            )
            cart = await cursor.fetchone()
            if not cart or cart["items_json"] != cart_items:
                raise ApiError(409, "Корзина изменилась. Обновите её перед оформлением.")
            if base["alumni_id"]:
                cursor = await connection.execute(
                    Query(
                        "SELECT verification_status,points_cached,personal_discount FROM alumni WHERE id=%s FOR SHARE",
                        "SELECT verification_status,points_cached,personal_discount FROM alumni WHERE id=%s LOCK IN SHARE MODE",
                    ),
                    (base["alumni_id"],),
                )
                alumni = await cursor.fetchone()
                if (
                    not alumni
                    or effective_discount(
                        alumni["verification_status"] == "verified",
                        alumni["points_cached"] or 0,
                        alumni["personal_discount"] or 0,
                    )
                    != base["member_discount"]
                ):
                    raise ApiError(409, "Статус или скидка изменились. Обновите корзину.")
            reservations = []
            for item in sorted(
                base["items_json"], key=lambda item: (item["type"], item["ref_id"], item.get("variant_sku") or "")
            ):
                query = (
                    "SELECT * FROM products WHERE slug=%s FOR UPDATE"
                    if item["type"] == "merch"
                    else "SELECT * FROM programs WHERE slug=%s FOR UPDATE"
                )
                product = await (await connection.execute(query, (item["ref_id"],))).fetchone()
                if (
                    not product
                    or product["status"] != "published"
                    or product["price"] != item["price"]
                    or (item["type"] == "dpo" and (product["source_url"] or product["enrollment"] == "nonactual"))
                ):
                    raise ApiError(409, "Цена или доступность позиции изменились. Обновите корзину.")
                if type(item["qty"]) is not int or not 1 <= item["qty"] <= 99:
                    raise ApiError(409, "Некорректное количество.")
                if item["type"] == "merch":
                    variants = product["variants_json"] or []
                    variant = next((value for value in variants if value["sku"] == item.get("variant_sku")), None)
                    if (variants and not variant) or (not variants and item.get("variant_sku")):
                        raise ApiError(409, "Вариант товара больше недоступен.")
                    stock = variant["stock"] if variant else product["stock"]
                    if type(stock) is not int or stock < item["qty"]:
                        raise ApiError(409, f"Недостаточно на складе: {item['title']}.")
                    if variant:
                        variant["stock"] -= item["qty"]
                        await connection.execute(
                            "UPDATE products SET variants_json=%s WHERE id=%s", (Jsonb(variants), product["id"])
                        )
                    else:
                        await connection.execute(
                            "UPDATE products SET stock=stock-%s WHERE id=%s", (item["qty"], product["id"])
                        )
                    reservations.append({"id": str(product["id"]), "sku": item.get("variant_sku"), "qty": item["qty"]})
            number = await self.record_order(connection, key, request_hash, base, reservations)
            await connection.execute(
                Query(
                    "UPDATE carts SET items_json='[]'::json,updated_at=now() WHERE id=%s",
                    "UPDATE carts SET items_json='[]',updated_at=now() WHERE id=%s",
                ),
                (cart_id,),
            )
            return {"number": number, "replay": None}

    async def record_order(self, connection, key, request_hash, base, reservations):
        number = await next_order_number(connection)
        order = await self.state.store.create(
            "orders",
            {"id": str(uuid4()), **base, "number": number, "created_at": datetime.now(UTC)},
            connection=connection,
        )
        receipt = {
            "number": number,
            "status": base["status"],
            "member_discount": base["member_discount"],
            "subtotal": base["subtotal"],
            "total_estimate": base["total_estimate"],
            "notified": {"channel": "none", "ok": False, "blocked": True},
        }
        await connection.execute(
            "INSERT INTO club_checkout_commits(key_hash,request_hash,order_id,reservations,receipt) VALUES(%s,%s,%s,%s,%s)",
            (key, request_hash, order["id"], Jsonb(reservations), Jsonb(receipt)),
        )
        return number

    async def commit_single(self, *, key, request_hash, base):
        async with self.state.database.transaction() as connection:
            await acquire_lock(connection, "checkout:" + key)
            if previous := await self.replay(key, request_hash, connection):
                return {"replay": previous, "number": previous["number"]}
            return {"replay": None, "number": await self.record_order(connection, key, request_hash, base, [])}

    async def release(self, connection, order_id):
        cursor = await connection.execute(
            "SELECT reservations,released FROM club_checkout_commits WHERE order_id=%s FOR UPDATE", (order_id,)
        )
        commit = await cursor.fetchone()
        if not commit or commit["released"]:
            return
        for reserve in commit["reservations"]:
            cursor = await connection.execute(
                "SELECT variants_json FROM products WHERE id=%s FOR UPDATE", (reserve["id"],)
            )
            product = await cursor.fetchone()
            if not product:
                raise ApiError(409, "Товар резерва удалён. Восстановите его перед отменой.")
            if reserve["sku"]:
                variants = product["variants_json"] or []
                variant = next((value for value in variants if value["sku"] == reserve["sku"]), None)
                if not variant:
                    raise ApiError(409, "Вариант резерва удалён. Восстановите его перед отменой.")
                variant["stock"] += reserve["qty"]
                await connection.execute(
                    "UPDATE products SET variants_json=%s WHERE id=%s", (Jsonb(variants), reserve["id"])
                )
            else:
                await connection.execute(
                    "UPDATE products SET stock=stock+%s WHERE id=%s", (reserve["qty"], reserve["id"])
                )
        await connection.execute("UPDATE club_checkout_commits SET released=true WHERE order_id=%s", (order_id,))

    async def change_status(self, id, status, *, expected_status=None):
        async with self.state.database.transaction() as connection:
            cursor = await connection.execute(
                "SELECT status,payment_status,program_id,participants FROM orders WHERE id=%s FOR UPDATE", (id,)
            )
            order = await cursor.fetchone()
            if not order:
                raise ApiError(404, "Заявка не найдена")
            if expected_status is not None and order["status"] != expected_status:
                return False
            if order["status"] == status:
                return False
            if order["status"] in ("done", "canceled", "expired"):
                raise ApiError(409, "Закрытую заявку нельзя открыть повторно. Создайте новую.")
            if status in ("canceled", "expired"):
                if order["payment_status"] in ("succeeded", "pending", "waiting_for_capture", "review"):
                    raise ApiError(409, "Сначала завершите сверку или возврат платежа. Резерв сохранён.")
                await self.release(connection, id)
            await require_free_seats(connection, order, status)
            await connection.execute("UPDATE orders SET status=%s WHERE id=%s", (status, id))
            return True

    async def expire_reservations(self):
        ttl = self.state.settings.RESERVE_TTL_HOURS
        if ttl <= 0 or not self.state.database.pool:
            return 0
        cutoff = datetime.now(UTC) - timedelta(hours=ttl)
        count, after = 0, None
        while True:
            rows = await self.state.database.rows(
                Query(
                    "SELECT o.id,o.number,o.created_at,o.payment_id,o.payment_status FROM orders o JOIN club_checkout_commits c ON c.order_id=o.id WHERE o.status='new' AND (o.payment_status IS NULL OR o.payment_status IN ('','none','canceled','pending','waiting_for_capture') OR (o.payment_status='review' AND o.payment_id IS NULL)) AND c.released=false AND jsonb_array_length(c.reservations)>0 AND o.created_at < %s AND (CAST(%s AS uuid) IS NULL OR o.id>CAST(%s AS uuid)) ORDER BY o.id LIMIT %s",
                    "SELECT o.id,o.number,o.created_at,o.payment_id,o.payment_status FROM orders o JOIN club_checkout_commits c ON c.order_id=o.id WHERE o.status='new' AND (o.payment_status IS NULL OR o.payment_status IN ('','none','canceled','pending','waiting_for_capture') OR (o.payment_status='review' AND o.payment_id IS NULL)) AND c.released=false AND JSON_LENGTH(c.reservations)>0 AND o.created_at < %s AND (%s IS NULL OR o.id>%s) ORDER BY o.id LIMIT %s",
                ),
                (cutoff, after, after, RESERVATION_BATCH_SIZE),
            )
            if not rows:
                break
            for row in rows:
                try:
                    if row["payment_status"] in ("pending", "waiting_for_capture", "review"):
                        payment = None
                        if self.state.payments.enabled:
                            payment = (
                                await self.state.payments.fetch(row["payment_id"])
                                if row["payment_id"]
                                else await self.state.payments.find_order_payment(row)
                            )
                        if payment:
                            result = await self.state.payments.record(row["number"], payment)
                            count += result["outcome"] == "canceled"
                        elif not row["payment_id"]:
                            await self.state.payments.flag_missing_payment(row["number"])
                        continue
                    count += bool(await self.change_status(str(row["id"]), "expired", expected_status="new"))
                except ApiError:
                    if not row["payment_id"]:
                        await self.state.payments.flag_missing_payment(row["number"])
            after = rows[-1]["id"]
        return count
