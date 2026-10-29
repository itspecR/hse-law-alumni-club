from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from django.http import HttpRequest
from pydantic import Field

from club_api.core.errors import ApiError
from club_api.core.views import api_view, parse_body
from club_api.db.queries import acquire_lock
from club_api.domain import MAX_CART_LINES, MAX_LINE_QTY, effective_discount, order_totals, same_line, summarize_cart
from club_api.modules.auth.routes import Body
from club_api.modules.catalog.lookup import lookup_catalog

ROUTE_LIMITS = {("POST", "/cart"): 40}


class CartItem(Body):
    type: Literal["merch"]
    ref_id: str = Field(min_length=1)
    variant_sku: str | None = None
    qty: int = Field(default=1, ge=1, le=MAX_LINE_QTY)


class ChangeQty(Body):
    type: Literal["dpo", "merch"] | None = None
    ref_id: str
    variant_sku: str | None = None
    qty: int = Field(ge=0, le=MAX_LINE_QTY)


def cart_session(request):
    value = request.headers.get("x-cart-session", "")
    try:
        if len(value) != 36 or str(UUID(value)) != value.lower():
            raise ValueError
    except ValueError as error:
        raise ApiError(400, "Корзина недоступна. Обновите страницу.") from error
    return value


@asynccontextmanager
async def locked_cart(state, token):
    async with state.database.transaction() as connection:
        await acquire_lock(connection, "cart:" + token)
        yield connection


async def load_cart(store, token, connection=None):
    rows = await store.read(
        "carts", filters={"session_token": {"_eq": token}}, fields=("id", "items_json"), limit=1, connection=connection
    )
    return {"id": rows[0]["id"], "items": rows[0]["items_json"] or []} if rows else None


async def save_cart(store, token, items, connection):
    existing = await load_cart(store, token, connection)
    data = {"items_json": items, "updated_at": datetime.now(UTC)}
    if existing:
        await store.update("carts", data, id=existing["id"], connection=connection)
    else:
        await store.create("carts", {"session_token": token, **data}, connection=connection)


def exceeds_stock(info, sku, qty):
    available = (
        next((value.get("stock") for value in info["variants"] if value.get("sku") == sku), None)
        if info["variants"]
        else info["stock"]
    )
    return isinstance(available, int | float) and qty > available


@api_view
async def cart(request: HttpRequest):
    state = request.services
    existing = await load_cart(state.store, cart_session(request))
    items = existing["items"] if existing else []
    alumni = await state.auth.resolve_alumni(request)
    discount = effective_discount(
        bool(alumni and alumni["verification_status"] == "verified"),
        alumni["points_cached"] or 0 if alumni else 0,
        alumni["personal_discount"] or 0 if alumni else 0,
    )
    catalog = await lookup_catalog(state.store, items) if items else {}
    priced = [
        {**item, "price": catalog.get(item["type"] + ":" + item["ref_id"], {}).get("price", item["price"])}
        for item in items
    ]
    return {
        **summarize_cart(priced),
        "estimated_total": order_totals(priced, discount)["total"],
        "member_discount": discount,
    }


@api_view(body=CartItem)
async def add_cart(request: HttpRequest):
    body = parse_body(request, CartItem)
    state, token = (request.services, cart_session(request))
    info = (await lookup_catalog(state.store, [body.model_dump()])).get(body.type + ":" + body.ref_id)
    if not info:
        raise ApiError(404, "Позиция не найдена.")
    if info["variants"]:
        if not body.variant_sku:
            raise ApiError(400, "Выберите вариант товара.")
        if not any(value["sku"] == body.variant_sku for value in info["variants"]):
            raise ApiError(400, "Такого варианта товара нет.")
    elif body.variant_sku:
        raise ApiError(400, "У этого товара нет вариантов.")
    async with locked_cart(state, token) as connection:
        existing = await load_cart(state.store, token, connection)
        items = existing["items"] if existing else []
        match = next((item for item in items if same_line(item, body.type, body.ref_id, body.variant_sku)), None)
        if not match and len(items) >= MAX_CART_LINES:
            raise ApiError(
                409, f"В корзине может быть не больше {MAX_CART_LINES} позиций. Оформите заявку или удалите лишнее."
            )
        if match:
            match["qty"] = min(MAX_LINE_QTY, match["qty"] + body.qty)
        else:
            match = {**body.model_dump(), "price": info["price"], "title": info["title"]}
            items.append(match)
        if exceeds_stock(info, body.variant_sku, match["qty"]):
            raise ApiError(409, "Недостаточно товара в наличии.")
        await save_cart(state.store, token, items, connection)
        return summarize_cart(items)


@api_view(body=ChangeQty)
async def change_qty(request: HttpRequest):
    body = parse_body(request, ChangeQty)
    state, token = (request.services, cart_session(request))
    async with locked_cart(state, token) as connection:
        existing = await load_cart(state.store, token, connection)
        items = existing["items"] if existing else []
        types = {
            item["type"]
            for item in items
            if item["ref_id"] == body.ref_id and item.get("variant_sku") == body.variant_sku
        }
        if body.type is None and len(types) > 1:
            raise ApiError(409, "Укажите тип позиции корзины.")
        kind = body.type or next(iter(types), None)
        match = next((item for item in items if same_line(item, kind, body.ref_id, body.variant_sku)), None)
        if match and match["type"] == "merch" and (body.qty > match["qty"]):
            info = (await lookup_catalog(state.store, [match], connection)).get("merch:" + body.ref_id)
            if not info:
                raise ApiError(404, "Позиция не найдена.")
            if info["variants"] and (not any(value["sku"] == body.variant_sku for value in info["variants"])):
                raise ApiError(400, "Такого варианта товара нет.")
            if exceeds_stock(info, body.variant_sku, body.qty):
                raise ApiError(409, "Недостаточно товара в наличии.")
        for item in items:
            if same_line(item, kind, body.ref_id, body.variant_sku):
                item["qty"] = 1 if item["type"] == "dpo" and body.qty else body.qty
        items = [item for item in items if item["qty"] > 0]
        await save_cart(state.store, token, items, connection)
        return summarize_cart(items)


@api_view
async def clear_cart(request: HttpRequest):
    state, token = (request.services, cart_session(request))
    async with locked_cart(state, token) as connection:
        await save_cart(state.store, token, [], connection)
    return summarize_cart([])
