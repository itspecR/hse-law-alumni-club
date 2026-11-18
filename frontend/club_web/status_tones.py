OK = "ok"
NEUTRAL = "neutral"
WARN = "warn"
ALERT = "alert"

OFFICE_GROUPS = {
    OK: ("published", "verified", "confirmed", "succeeded", "active", "ok", "actual"),
    WARN: ("new", "in_progress", "pending", "awaiting", "waiting_for_capture", "review", "open", "unsaved"),
    ALERT: ("error", "failed"),
}
SECTION_TONES = {"cohorts": {"open": OK, "closed": NEUTRAL}}
CABINET_ORDER_TONES = {"confirmed": OK}
PAYMENT_CHIPS = {"succeeded": ("Оплачено", OK), "pending": ("Ожидает оплаты", WARN)}
UNPAID_CHIP = ("Не оплачено", NEUTRAL)


def tone_lookup(groups):
    return {value: tone for tone, values in groups.items() for value in values}


OFFICE_TONES = tone_lookup(OFFICE_GROUPS)


def office_tone(value, section=None):
    return SECTION_TONES.get(section, {}).get(value) or OFFICE_TONES.get(value, NEUTRAL)


def order_tone(status):
    return CABINET_ORDER_TONES.get(status, NEUTRAL)


def payment_chip(status):
    return PAYMENT_CHIPS.get(status, UNPAID_CHIP)
