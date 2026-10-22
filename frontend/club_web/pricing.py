PERCENT = 100
HALF_UP = PERCENT // 2


def discount_amount(price, percent):
    return (price * percent + HALF_UP) // PERCENT


def member_prices(price, levels):
    return [
        {
            "level": level["key"],
            "title": level["title"],
            "discount": level["discount_percent"],
            "price": price - discount_amount(price, level["discount_percent"]),
        }
        for level in sorted(levels, key=lambda level: level["sort"])
    ]
