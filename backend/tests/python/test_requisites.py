import pytest
from pydantic import ValidationError

from club_api.core import requisites
from club_api.core.config import Settings, load_settings
from club_api.resources import shared_data

OPERATOR = shared_data("legal-data.json")["operator"]
BANK_BIC = "044525225"
BANK_CORRESPONDENT = "30101810400000000225"
ACCOUNT = "40703810000000000006"
PUBLIC_CORRESPONDENTS = [
    ("044525225", "30101810400000000225"),
    ("044525974", "30101810145250000974"),
    ("044525593", "30101810200000000593"),
]
PAYEE = {
    "PAYEE_NAME": OPERATOR["short_name"],
    "PAYEE_INN": OPERATOR["inn"],
    "PAYEE_KPP": OPERATOR["kpp"],
    "PAYEE_BANK": "ПАО Сбербанк",
    "PAYEE_BIC": BANK_BIC,
    "PAYEE_ACCOUNT": ACCOUNT,
    "PAYEE_CORRESPONDENT_ACCOUNT": BANK_CORRESPONDENT,
}


def settings(**values):
    return Settings(AUTH_SECRET="synthetic-session-secret-for-tests-only", **values)


def changed(value, index):
    return value[:index] + str((int(value[index]) + 1) % 10) + value[index + 1 :]


def test_operator_inn_and_kpp_pass_checks():
    assert requisites.valid_inn(OPERATOR["inn"]) and requisites.valid_kpp(OPERATOR["kpp"])


@pytest.mark.parametrize("index", range(10))
def test_inn_with_any_wrong_digit_is_rejected(index):
    assert not requisites.valid_inn(changed(OPERATOR["inn"], index))


@pytest.mark.parametrize("value", ["", "970704186", "97070418650", "97070418A5", " 9707041865"])
def test_inn_of_wrong_form_is_rejected(value):
    assert not requisites.valid_inn(value)


@pytest.mark.parametrize(("value", "valid"), [("7718AB001", True), ("77180100", False), ("77180100A", False)])
def test_kpp_form(value, valid):
    assert requisites.valid_kpp(value) is valid


@pytest.mark.parametrize(("bic", "account"), PUBLIC_CORRESPONDENTS)
def test_public_bank_correspondent_accounts_pass_control_key(bic, account):
    assert requisites.valid_correspondent_account(bic, account)


@pytest.mark.parametrize("index", range(5, 20))
def test_correspondent_account_with_wrong_digit_is_rejected(index):
    assert not requisites.valid_correspondent_account(BANK_BIC, changed(BANK_CORRESPONDENT, index))


def test_correspondent_account_needs_correspondent_prefix():
    assert not requisites.valid_correspondent_account(BANK_BIC, ACCOUNT)


def test_settlement_account_is_checked_against_bic():
    assert requisites.valid_account(BANK_BIC, ACCOUNT)
    assert not requisites.valid_account(BANK_BIC, changed(ACCOUNT, 19))
    assert not requisites.valid_account("044525974", ACCOUNT)
    assert not requisites.valid_account("04452522", ACCOUNT)


@pytest.mark.parametrize("value", ["", "a|b", "Банк\x00", "я" * (requisites.TEXT_MAX + 1)])
def test_payee_text_rejects_empty_separator_control_and_long_values(value):
    assert not requisites.valid_text(value)


def test_complete_payee_settings_are_loaded_and_normalized():
    loaded = settings(**{**PAYEE, "PAYEE_BANK": "  ПАО   Сбербанк ", "PAYEE_ACCOUNT": f" {ACCOUNT} "})
    assert loaded.payee() == PAYEE


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PAYEE_INN", changed(OPERATOR["inn"], 9)),
        ("PAYEE_KPP", "7718"),
        ("PAYEE_BIC", "04452522"),
        ("PAYEE_ACCOUNT", changed(ACCOUNT, 19)),
        ("PAYEE_CORRESPONDENT_ACCOUNT", changed(BANK_CORRESPONDENT, 19)),
        ("PAYEE_NAME", "АНО | Клуб"),
        ("PAYEE_BANK", "Б" * (requisites.TEXT_MAX + 1)),
    ],
)
def test_invalid_payee_setting_is_named_in_configuration_error(name, value, monkeypatch):
    with pytest.raises(ValidationError):
        settings(**{**PAYEE, name: value})
    monkeypatch.setenv("AUTH_SECRET", "synthetic-session-secret-for-tests-only")
    for key, item in {**PAYEE, name: value}.items():
        monkeypatch.setenv(key, item)
    with pytest.raises(RuntimeError, match=name):
        load_settings()


def test_accounts_of_other_bank_are_rejected():
    with pytest.raises(ValidationError, match="PAYEE_ACCOUNT"):
        settings(**{**PAYEE, "PAYEE_BIC": "044525974", "PAYEE_CORRESPONDENT_ACCOUNT": "30101810145250000974"})


def test_production_requires_all_payee_settings_or_none():
    production = {
        "APP_ENV": "production",
        "ADMIN_AUTH_SECRET": "synthetic-independent-office-secret-for-tests",
        "CHECKOUT_DATABASE_URL": "mariadb://api:test@mariadb/club",
        "PUBLIC_URL": "https://club.test",
        "SMTP_HOST": "smtp.test",
        "SMTP_FROM": "office@club.test",
        "OFFICE_NOTIFY_CHANNEL": "email",
        "OFFICE_EMAIL": "office@club.test",
    }
    assert not settings(**production).production_errors()
    assert not settings(**production, **PAYEE).production_errors()
    partial = {**PAYEE, "PAYEE_ACCOUNT": ""}
    assert (
        "Реквизиты PAYEE_* задаются все вместе или все пустые" in settings(**production, **partial).production_errors()
    )
