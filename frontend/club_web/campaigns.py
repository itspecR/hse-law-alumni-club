import math

WAVE_BARS = 56
WAVE_FLOOR = 8
WAVE_SWING = 144
WAVE_SHAPE = 1.4
RIPPLE_BASE = 0.55
RIPPLE_SWING = 0.45
RIPPLE_FAST = 21
RIPPLE_SLOW = 7.5
RIPPLE_SHIFT = 0.6
FACT_LABELS = (("date_text", "Когда"), ("time_text", "Время"), ("place_text", "Где"), ("duration_text", "Формат"))
PERSON_ROLES = (("speaker", "Спикер", "Спикеры"), ("trainer", "Тренер", "Тренеры"), ("host", "Ведущий", "Ведущие"))
DEFAULT_ROLE = PERSON_ROLES[0][0]


def bar_height(index):
    position = index / (WAVE_BARS - 1)
    envelope = math.sin(math.pi * position) ** WAVE_SHAPE
    ripple = RIPPLE_BASE + RIPPLE_SWING * math.sin(position * RIPPLE_FAST + RIPPLE_SHIFT) * math.cos(
        position * RIPPLE_SLOW
    )
    return round(WAVE_FLOOR + WAVE_SWING * envelope * abs(ripple), 1)


WAVE = tuple(bar_height(index) for index in range(WAVE_BARS))


def campaign_facts(campaign):
    return [(label, campaign[key]) for key, label in FACT_LABELS if campaign.get(key)]


def people_groups(people):
    groups = [
        (role, title, [person for person in people if (person.get("role") or DEFAULT_ROLE) == role])
        for role, _, title in PERSON_ROLES
    ]
    return [group for group in groups if group[2]]


def form_amounts(campaign, defaults, default_chosen):
    amounts = tuple(campaign.get("amounts") or defaults)
    preferred = (campaign.get("invite_threshold"), default_chosen)
    chosen = next((amount for amount in preferred if amount in amounts), amounts[0])
    return amounts, chosen


def is_pause(block):
    return not (block.get("time") or block.get("note") or block.get("points"))
