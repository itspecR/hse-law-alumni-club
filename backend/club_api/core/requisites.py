import re

TEXT_MAX = 160
INN = re.compile(r"[0-9]{10}")
KPP = re.compile(r"[0-9]{4}[0-9A-Z]{2}[0-9]{3}")
BIC = re.compile(r"[0-9]{9}")
ACCOUNT = re.compile(r"[0-9]{20}")
CORRESPONDENT_PREFIX = "30101"
INN_WEIGHTS = (2, 4, 10, 3, 5, 9, 4, 6, 8)
ACCOUNT_KEY_WEIGHTS = (7, 1, 3)
FORBIDDEN = "|"


def normalized_text(value):
    return " ".join(value.split())


def valid_text(value):
    return 0 < len(value) <= TEXT_MAX and FORBIDDEN not in value and value.isprintable()


def valid_inn(value):
    if not INN.fullmatch(value):
        return False
    total = sum(int(digit) * weight for digit, weight in zip(value[:-1], INN_WEIGHTS, strict=True))
    return total % 11 % 10 == int(value[-1])


def valid_kpp(value):
    return bool(KPP.fullmatch(value))


def valid_bic(value):
    return bool(BIC.fullmatch(value))


def account_key_valid(prefix, account):
    digits = prefix + account
    return sum(int(digit) * ACCOUNT_KEY_WEIGHTS[index % 3] for index, digit in enumerate(digits)) % 10 == 0


def valid_account(bic, account):
    return valid_bic(bic) and bool(ACCOUNT.fullmatch(account)) and account_key_valid(bic[-3:], account)


def valid_correspondent_account(bic, account):
    return (
        valid_bic(bic)
        and bool(ACCOUNT.fullmatch(account))
        and account.startswith(CORRESPONDENT_PREFIX)
        and account_key_valid("0" + bic[4:6], account)
    )
