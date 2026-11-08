import segno

from club_api.core.errors import ApiError
from club_api.core.requisites import FORBIDDEN, normalized_text
from club_api.core.schemas import SAFE_INTEGER_MAX
from club_api.domain import rubles

HEADER = "ST00012"
ENCODING = "utf-8"
PURPOSE_MAX = 210
QR_ERROR_LEVEL = "m"
QR_BORDER = 4
QR_DARK = "#000"
QR_LIGHT = "#fff"
PNG_SCALE = 8
FIELDS = (
    ("Name", "PAYEE_NAME"),
    ("PersonalAcc", "PAYEE_ACCOUNT"),
    ("BankName", "PAYEE_BANK"),
    ("BIC", "PAYEE_BIC"),
    ("CorrespAcc", "PAYEE_CORRESPONDENT_ACCOUNT"),
    ("PayeeINN", "PAYEE_INN"),
    ("KPP", "PAYEE_KPP"),
)
PAYEE_LABELS = {
    "PAYEE_NAME": "Получатель",
    "PAYEE_INN": "ИНН",
    "PAYEE_KPP": "КПП",
    "PAYEE_BANK": "Банк",
    "PAYEE_BIC": "БИК",
    "PAYEE_ACCOUNT": "Расчетный счет",
    "PAYEE_CORRESPONDENT_ACCOUNT": "Корреспондентский счет",
}
UNAVAILABLE = "Оплата переводом по реквизитам не настроена."
INVALID_AMOUNT = "Некорректная сумма перевода."


def clean_purpose(text):
    purpose = normalized_text("".join(char if char.isprintable() else " " for char in text.replace(FORBIDDEN, " ")))
    if not purpose or len(purpose) > PURPOSE_MAX:
        raise ValueError(f"Назначение платежа должно быть от 1 до {PURPOSE_MAX} знаков")
    return purpose


def requisites_text(transfer):
    lines = [f"{item['label']}: {item['value']}" for item in transfer["payee"]]
    lines += [f"Сумма: {rubles(transfer['amount'])}", f"Назначение платежа: {transfer['purpose']}"]
    return "\n".join(lines)


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

    def payee(self):
        return [{"label": PAYEE_LABELS[name], "value": value} for name, value in self.settings.payee().items()]

    def details(self, amount, purpose):
        code = segno.make(
            self.payload(amount, purpose), error=QR_ERROR_LEVEL, encoding=ENCODING, micro=False, eci=False
        )
        drawing = {"border": QR_BORDER, "dark": QR_DARK, "light": QR_LIGHT}
        return {
            "amount": amount,
            "purpose": clean_purpose(purpose),
            "payee": self.payee(),
            "qr_svg": code.svg_data_uri(omitsize=True, **drawing),
            "qr_png": code.png_data_uri(scale=PNG_SCALE, **drawing),
        }
