import pytest

from club_web.status_tones import ALERT, NEUTRAL, OK, WARN, office_tone, order_tone, payment_chip


@pytest.mark.parametrize(
    "value,section,tone",
    [
        ("published", None, OK),
        ("verified", None, OK),
        ("actual", None, OK),
        ("open", "cohorts", OK),
        ("closed", "cohorts", NEUTRAL),
        ("open", "support", WARN),
        ("new", "orders", WARN),
        ("awaiting", None, WARN),
        ("review", None, WARN),
        ("pending", None, WARN),
        ("unsaved", None, WARN),
        ("canceled", None, NEUTRAL),
        ("rejected", None, NEUTRAL),
        ("expired", None, NEUTRAL),
        ("done", None, NEUTRAL),
        ("answered", None, NEUTRAL),
        ("nonactual", None, NEUTRAL),
        ("past", None, NEUTRAL),
        ("draft", None, NEUTRAL),
        ("error", None, ALERT),
        ("failed", None, ALERT),
        ("unknown-status", None, NEUTRAL),
    ],
)
def test_office_tone_follows_whose_action_is_needed(value, section, tone):
    assert office_tone(value, section) == tone


def test_cabinet_tones_wait_on_the_member_only_for_payment():
    assert [order_tone(status) for status in ("new", "in_progress", "confirmed", "done", "canceled")] == [
        NEUTRAL,
        NEUTRAL,
        OK,
        NEUTRAL,
        NEUTRAL,
    ]
    assert payment_chip("succeeded") == ("Оплачено", OK)
    assert payment_chip("pending") == ("Ожидает оплаты", WARN)
    assert payment_chip(None) == ("Не оплачено", NEUTRAL)
    assert payment_chip("canceled") == ("Не оплачено", NEUTRAL)
