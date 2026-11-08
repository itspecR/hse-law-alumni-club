import segno

from club_api.core.errors import ApiError
from club_api.core.requisites import FORBIDDEN, normalized_text
from club_api.modules.checkout.payments import SAFE_INTEGER_MAX

HEADER = "ST00012"
ENCODING = "utf-8"
PURPOSE_MAX = 210
QR_ERROR_LEVEL = "m"
QR_BORDER = 4
QR_DARK = "#000"
QR_LIGHT = "#fff"
FIELDS = (
    ("Name", "PAYEE_NAME"),
    ("PersonalAcc", "PAYEE_ACCOUNT"),
    ("BankName", "PAYEE_BANK"),
    ("BIC", "PAYEE_BIC"),
    ("CorrespAcc", "PAYEE_CORRESPONDENT_ACCOUNT"),
    ("PayeeINN", "PAYEE_INN"),
    ("KPP", "PAYEE_KPP"),
)
UNAVAILABLE = "Оплата переводом по реквизитам не настроена."
INVALID_AMOUNT = "Некорректная сумма перевода."


def clean_purpose(text):
    purpose = normalized_text("".join(char if char.isprintable() else " " for char in text.replace(FORBIDDEN, " ")))
    if not purpose or len(purpose) > PURPOSE_MAX:
        raise ValueError(f"Назначение платежа должно быть от 1 до {PURPOSE_MAX} знаков")
    return purpose


class Transfer:
    def __init__(self, settings):
        self.settings = settings

    @property
    def enabled(self):
        return all(self.settings.payee().values())

    def payload(self, amount, purpose):
        if not self.enabled:
            raise ApiError(503, UNAVAILABLE)
        if type(amount) is not int or not 0 < amount <= SAFE_INTEGER_MAX:
            raise ApiError(400, INVALID_AMOUNT)
        payee = self.settings.payee()
        fields = [f"{key}={payee[name]}" for key, name in FIELDS]
        fields += [f"Sum={amount}", f"Purpose={clean_purpose(purpose)}"]
        return FORBIDDEN.join((HEADER, *fields))

    def svg(self, amount, purpose):
        code = segno.make(
            self.payload(amount, purpose), error=QR_ERROR_LEVEL, encoding=ENCODING, micro=False, eci=False
        )
        return code.svg_inline(border=QR_BORDER, dark=QR_DARK, light=QR_LIGHT, omitsize=True)
