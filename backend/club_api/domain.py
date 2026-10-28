import math

from club_api.resources import shared_data

DOMAIN = shared_data("domain-data.json")
LEVELS = DOMAIN["levels"]
ACHIEVEMENTS = [item for item in DOMAIN["achievements"] if not item.get("hidden")]
ACHIEVEMENT_FLAGS = ("star", "hidden")
PERSONAL_DISCOUNT_MAX = DOMAIN["limits"]["personal_discount_max"]
MEMBER_DISCOUNT_CAP = DOMAIN["limits"]["member_discount_cap"]
MAX_LINE_QTY = DOMAIN["limits"]["max_line_qty"]
MAX_CART_LINES = DOMAIN["limits"]["max_cart_lines"]
MAX_INTERESTS = DOMAIN["limits"]["max_interests"]
PROGRAM_CATALOGS = ("dpo", "portfolio")
DPO_CATALOG, PORTFOLIO_CATALOG = PROGRAM_CATALOGS
DISCOUNTED_TYPES = PROGRAM_CATALOGS


def rubles(kopecks):
    whole, cents = divmod(kopecks or 0, 100)
    return f"{whole:,}".replace(",", "\u00a0") + (f",{cents:02d}" if cents else "") + "\u00a0₽"


def applied_discount(order):
    return order["member_discount"] if order["office_discount"] is None else order["office_discount"]


def compute_level(points):
    return next((level for level in reversed(LEVELS) if points >= level["min_points"]), LEVELS[0])


def member_discount(points, personal=0):
    return min(
        MEMBER_DISCOUNT_CAP, compute_level(points)["discount_percent"] + max(0, min(PERSONAL_DISCOUNT_MAX, personal))
    )


def effective_discount(verified, points, personal):
    return member_discount(points, personal) if verified else 0


def level_info(points, personal=0):
    level = compute_level(points)
    next_level = LEVELS[LEVELS.index(level) + 1] if level is not LEVELS[-1] else None
    return {
        "points": points,
        "level": level["key"],
        "level_title": level["title"],
        "discount": member_discount(points, personal),
        "next_level": next_level["title"] if next_level else None,
        "to_next": max(0, next_level["min_points"] - points) if next_level else 0,
    }


def decay_delta(points):
    return -math.floor(max(0, points) * DOMAIN["limits"]["decay_rate"] + 0.5)


def achievement_progress(stats):
    result = []
    for item in ACHIEVEMENTS:
        current, target = stats.get(item["rule_json"]["type"], 0), item["rule_json"]["gte"]
        result.append(
            {
                **{key: item[key] for key in ("key", "title", "description", "icon", "kind")},
                "current": min(current, target),
                "target": target,
                "earned": current >= target,
                "star": item.get("star", False),
            }
        )
    return result


def order_totals(items, discount_percent):
    subtotal = sum(item["price"] * item["qty"] for item in items)
    base = sum(item["price"] * item["qty"] for item in items if item["type"] in DISCOUNTED_TYPES)
    discount = max(0, min(100, discount_percent))
    amount = (base * discount + 50) // 100
    return {"subtotal": subtotal, "discount": discount, "discountAmount": amount, "total": max(0, subtotal - amount)}


def same_line(item, type, ref, sku=None):
    return item["type"] == type and item["ref_id"] == ref and item.get("variant_sku") == sku


def summarize_cart(items):
    return {
        "items": items,
        "count": sum(item["qty"] for item in items),
        "subtotal": sum(item["price"] * item["qty"] for item in items),
    }
