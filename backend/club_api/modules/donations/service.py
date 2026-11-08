from datetime import UTC, datetime
from uuid import uuid4

from club_api.core.errors import ApiError
from club_api.db.store import normalize
from club_api.domain import rubles
from club_api.modules.checkout.payments import DONATION_KEY, paid_kopecks
from club_api.modules.checkout.store import next_number
from club_api.modules.notifications.mail import CLUB_NAME, club_subject
from club_api.modules.transfer.qr import requisites_text
from club_api.resources import shared_data

DONATION_TABLE = "club_donations"
DONATION_PURPOSE = "на развитие факультета права НИУ ВШЭ"
DONATION_DESCRIPTION = "Пожертвование " + DONATION_PURPOSE
DONATION_CONSENTS = shared_data("legal-data.json")["donation_consents"]
THANKS_PATH = "/give/thanks"
ONCE = "once"
CARD = "card"
TRANSFER = "transfer"
NEW = "new"
AWAITING = "awaiting"
SUCCEEDED = "succeeded"
NOT_FOUND = "Пожертвование не найдено."
NOT_AWAITING = "Это пожертвование не ждет перевода."
LOCKED_SQL = "SELECT * FROM club_donations WHERE number=%s FOR UPDATE"


def donation_outcome(donation, payment):
    if donation["payment_id"] and donation["payment_id"] != payment["id"]:
        return "ignored"
    if donation["status"] == "succeeded":
        return "duplicate"
    if payment["status"] == "succeeded":
        return "succeeded" if paid_kopecks(payment) == donation["amount"] else "review"
    if donation["status"] == "review":
        return "ignored"
    if payment["status"] == "canceled":
        return "canceled"
    return "pending"


def greeting(donation):
    return f"Здравствуйте, {donation['name']}!" if donation["name"] else "Здравствуйте!"


def thanks_text(donation):
    return (
        f"{greeting(donation)}\n\nМы получили Ваше пожертвование {rubles(donation['amount'])} {DONATION_PURPOSE}. "
        f"Номер пожертвования {donation['number']}.\n\nСпасибо за поддержку.\n\n{CLUB_NAME}"
    )


def transfer_purpose(number):
    return f"Пожертвование {number} {DONATION_PURPOSE}"


def transfer_text(donation, transfer):
    return (
        f"{greeting(donation)}\n\nВы оформили пожертвование {rubles(donation['amount'])} {DONATION_PURPOSE}, "
        f"номер {donation['number']}. Чтобы его завершить, переведите деньги по реквизитам.\n\n"
        f"{requisites_text(transfer)}\n\n"
        f"Когда перевод поступит на счет клуба, мы пришлем письмо с благодарностью.\n\n{CLUB_NAME}"
    )


def awaiting_notice(donation):
    return (
        f"Пожертвование {donation['number']} на сумму {rubles(donation['amount'])} ждет перевода по реквизитам. "
        "Проверьте поступление по выписке банка."
    )


class Donations:
    def __init__(self, state):
        self.state = state

    async def create(self, *, amount, email, name, alumni_id, method=CARD):
        async with self.state.database.transaction() as connection:
            number = await next_number(connection, DONATION_TABLE)
            await self.state.store.create(
                DONATION_TABLE,
                {
                    "id": str(uuid4()),
                    "number": number,
                    "amount": amount,
                    "period": ONCE,
                    "email": email,
                    "name": name or None,
                    "alumni_id": alumni_id,
                    "status": AWAITING if method == TRANSFER else NEW,
                    "method": method,
                    "consents": DONATION_CONSENTS,
                    "created_at": datetime.now(UTC),
                },
                connection=connection,
            )
        return {"number": number, "amount": amount, "email": email, "name": name or None}

    async def start_payment(self, donation):
        return await self.state.payments.create_donation(
            donation["number"], donation["amount"], DONATION_DESCRIPTION, THANKS_PATH
        )

    async def start_transfer(self, donation):
        transfer = self.state.transfer.details(donation["amount"], transfer_purpose(donation["number"]))
        await self.state.notifications.mail_applicant(
            donation["email"], club_subject("Реквизиты для пожертвования"), transfer_text(donation, transfer)
        )
        await self.state.notifications.office_text(awaiting_notice(donation))
        return transfer

    async def mark_failed(self, number):
        await self.state.database.execute(
            "UPDATE club_donations SET status='failed' WHERE number=%s AND status='new'", (number,)
        )

    async def apply(self, payment):
        number = payment.get("metadata", {}).get(DONATION_KEY)
        async with self.state.database.transaction() as connection:
            donation = normalize(await (await connection.execute(LOCKED_SQL, (number,))).fetchone())
            if not donation:
                return {"outcome": "missing"}
            outcome = donation_outcome(donation, payment)
            if outcome in ("succeeded", "review", "canceled", "pending"):
                await connection.execute(
                    "UPDATE club_donations SET payment_id=%s,status=%s,paid_at=CASE WHEN %s THEN now() ELSE paid_at END WHERE id=%s",
                    (payment["id"], outcome, outcome == "succeeded", donation["id"]),
                )
            return {"outcome": outcome, "donation": donation}

    async def settle(self, payment):
        result = await self.apply(payment)
        if result["outcome"] == "succeeded":
            await self.thank(result["donation"])
        return result

    async def receive_transfer(self, number):
        async with self.state.database.transaction() as connection:
            donation = normalize(await (await connection.execute(LOCKED_SQL, (number,))).fetchone())
            if not donation:
                raise ApiError(404, NOT_FOUND)
            if donation["status"] != AWAITING:
                raise ApiError(409, NOT_AWAITING)
            await connection.execute(
                "UPDATE club_donations SET status=%s,paid_at=now() WHERE id=%s", (SUCCEEDED, donation["id"])
            )
        await self.thank(donation)
        return donation

    async def thank(self, donation):
        await self.state.notifications.mail_applicant(
            donation["email"], club_subject("Спасибо за пожертвование"), thanks_text(donation)
        )
        await self.state.notifications.office_text(
            f"Пожертвование {donation['number']} на сумму {rubles(donation['amount'])} получено."
        )
