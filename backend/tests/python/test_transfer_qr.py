import base64
from urllib.parse import unquote

import pytest
from test_requisites import PAYEE, settings

from club_api.core.errors import ApiError
from club_api.core.schemas import SAFE_INTEGER_MAX
from club_api.modules.transfer.qr import PURPOSE_MAX, Transfer

EXPECTED = (
    "ST00012"
    f"|Name={PAYEE['PAYEE_NAME']}"
    f"|PersonalAcc={PAYEE['PAYEE_ACCOUNT']}"
    f"|BankName={PAYEE['PAYEE_BANK']}"
    f"|BIC={PAYEE['PAYEE_BIC']}"
    f"|CorrespAcc={PAYEE['PAYEE_CORRESPONDENT_ACCOUNT']}"
    f"|PayeeINN={PAYEE['PAYEE_INN']}"
    f"|KPP={PAYEE['PAYEE_KPP']}"
    "|Sum=150000"
    "|Purpose=Пожертвование D-2026-000001"
)


def transfer(**values):
    return Transfer(settings(**{**PAYEE, **values}))


def test_disabled_without_complete_requisites():
    assert not Transfer(settings()).enabled
    assert not transfer(PAYEE_KPP="").enabled
    with pytest.raises(ApiError) as error:
        Transfer(settings()).payload(150000, "Пожертвование")
    assert error.value.status == 503


def test_payload_follows_gost_field_order_with_sum_in_kopecks():
    assert transfer().enabled
    assert transfer().payload(150000, "Пожертвование D-2026-000001") == EXPECTED


def test_purpose_is_cleaned_of_separators_line_breaks_and_control_characters():
    assert transfer().payload(150000, " Пожертвование |\n\x00 D-2026-000001\t") == EXPECTED


@pytest.mark.parametrize("purpose", ["", " | \n", "я" * (PURPOSE_MAX + 1)])
def test_empty_or_long_purpose_is_a_programming_error(purpose):
    with pytest.raises(ValueError):
        transfer().payload(150000, purpose)


@pytest.mark.parametrize("amount", [0, -1, True, 1.5, "150000", SAFE_INTEGER_MAX + 1])
def test_invalid_amount_is_rejected(amount):
    with pytest.raises(ApiError) as error:
        transfer().payload(amount, "Пожертвование")
    assert error.value.status == 400


def test_details_carry_payee_purpose_and_both_images():
    details = transfer().details(150000, " Пожертвование |\nD-2026-000001")
    assert (details["amount"], details["purpose"]) == (150000, "Пожертвование D-2026-000001")
    assert details["payee"][0] == {"label": "Получатель", "value": PAYEE["PAYEE_NAME"]}
    assert [item["value"] for item in details["payee"]] == list(PAYEE.values())
    svg = unquote(details["qr_svg"].removeprefix("data:image/svg+xml;charset=utf-8,"))
    assert svg.startswith("<svg") and "#fff" in svg and "#000" in svg
    assert "width=" not in svg.split(">", 1)[0]
    assert base64.b64decode(details["qr_png"].removeprefix("data:image/png;base64,")).startswith(b"\x89PNG")
