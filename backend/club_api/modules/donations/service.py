from datetime import UTC, datetime
from uuid import uuid4

from club_api.core.errors import ApiError
from club_api.db.store import normalize
from club_api.domain import rubles
from club_api.modules.campaigns.store import payment_purpose
from club_api.modules.checkout.payments import DONATION_KEY, paid_kopecks
from club_api.modules.checkout.store import next_number
from club_api.modules.notifications.mail import CLUB_NAME, club_subject
from club_api.modules.transfer.qr import requisites_text
from club_api.resources import shared_data

DONATION_TABLE = "club_donations"
DONATION_PURPOSE = "на развитие факультета права НИУ ВШЭ"
DONATION_CONSENTS = shared_data("legal-data.json")["donation_consents"]
CAMPAIGN_CONSENTS = {**DONATION_CONSENTS, **shared_data("legal-data.json")["campaign_consents"]}
THANKS_PATH = "/give/thanks"
ONCE = "once"
CARD = "card"
TRANSFER = "transfer"
NEW = "new"
AWAITING = "awaiting"
SUCCEEDED = "succeeded"
NOT_FOUND = "Пожертвование не найдено."
NOT_AWAITING = "Это пожертвование не ждет перевода."
NOT_INVITABLE = "Приглашение отправляют дарителю оплаченного пожертвования от порога сбора."
ALREADY_INVITED = "Приглашение этому дарителю уже отправлено."
EMAIL_ERASED = "Почта дарителя удалена по сроку хранения, отправить письмо некуда."
ERASED_EMAIL = "-"
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


def payment_description(purpose):
    return "Пожертвование " + purpose


def thanks_text(donation, purpose):
    return (
        f"{greeting(donation)}\n\nМы получили Ваше пожертвование {rubles(donation['amount'])} {purpose}. "
        f"Номер пожертвования {donation['number']}.\n\nСпасибо за поддержку.\n\n{CLUB_NAME}"
    )


def transfer_purpose(number, purpose):
    return f"Пожертвование {number} {purpose}"


def transfer_text(donation, transfer, purpose):
    return (
        f"{greeting(donation)}\n\nВы оформили пожертвование {rubles(donation['amount'])} {purpose}, "
        f"номер {donation['number']}. Чтобы его завершить, переведите деньги по реквизитам.\n\n"
        f"{requisites_text(transfer)}\n\n"
        f"Когда перевод поступит на счет клуба, мы пришлем письмо с благодарностью.\n\n{CLUB_NAME}"
    )


def invitation_text(donation, body):
    return f"{greeting(donation)}\n\n{body.strip()}\n\n{CLUB_NAME}"


def require_invitable(donation, campaign):
    if not donation or donation["campaign_id"] != campaign["id"]:
        raise ApiError(404, NOT_FOUND)
    if donation["status"] != SUCCEEDED or donation["amount"] < campaign["invite_threshold"]:
        raise ApiError(409, NOT_INVITABLE)
    if donation["invited_at"]:
        raise ApiError(409, ALREADY_INVITED)
    if donation["email"] == ERASED_EMAIL:
        raise ApiError(409, EMAIL_ERASED)


def awaiting_notice(donation):
    return (
        f"Пожертвование {donation['number']} на сумму {rubles(donation['amount'])} ждет перевода по реквизитам. "
        "Проверьте поступление по выписке банка."
    )


class Donations:
    def __init__(self, state):
        self.state = state

    async def purpose(self, donation):
        return await payment_purpose(self.state, donation.get("campaign_id")) or DONATION_PURPOSE

    async def create(self, *, amount, email, name, alumni_id, method=CARD, campaign_id=None):
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
                    "consents": CAMPAIGN_CONSENTS if campaign_id else DONATION_CONSENTS,
                    "created_at": datetime.now(UTC),
                    "campaign_id": campaign_id,
                },
                connection=connection,
            )
        return {"number": number, "amount": amount, "email": email, "name": name or None, "campaign_id": campaign_id}

    async def start_payment(self, donation):
        description = payment_description(await self.purpose(donation))
        return await self.state.payments.create_donation(
            donation["number"], donation["amount"], description, THANKS_PATH
        )

    async def start_transfer(self, donation):
        purpose = await self.purpose(donation)
        transfer = self.state.transfer.details(donation["amount"], transfer_purpose(donation["number"], purpose))
        await self.state.notifications.mail_applicant(
            donation["email"], club_subject("Реквизиты для пожертвования"), transfer_text(donation, transfer, purpose)
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

    async def invite(self, number, campaign):
        async with self.state.database.transaction() as connection:
            donation = normalize(await (await connection.execute(LOCKED_SQL, (number,))).fetchone())
            require_invitable(donation, campaign)
            await connection.execute("UPDATE club_donations SET invited_at=now() WHERE id=%s", (donation["id"],))
        await self.state.notifications.mail_applicant(
            donation["email"],
            club_subject(campaign["invite_subject"].strip()),
            invitation_text(donation, campaign["invite_body"]),
        )
        return donation

    async def thank(self, donation):
        await self.state.notifications.mail_applicant(
            donation["email"],
            club_subject("Спасибо за пожертвование"),
            thanks_text(donation, await self.purpose(donation)),
        )
        await self.state.notifications.office_text(
            f"Пожертвование {donation['number']} на сумму {rubles(donation['amount'])} получено."
        )
