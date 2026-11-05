from club_api.core.config import Settings
from club_api.modules.notifications.mail import Notifications, club_subject
from club_ops.bootstrap import OperatorError, email

ERROR_LENGTH = 200


def check_letter(public_url):
    return (
        "Здравствуйте!\n\nЭто проверочное письмо сайта клуба. Если оно пришло, отправка писем настроена.\n\n"
        + public_url
    )


async def send_test_mail(values):
    address = email(values.get("TEST_MAIL_TO", ""), "TEST_MAIL_TO")
    settings = Settings(**{key: value for key, value in values.items() if key in Settings.model_fields})
    if not settings.SMTP_HOST:
        raise OperatorError("SMTP_HOST не задан: отправка писем не настроена")
    notifications = Notifications(settings, None, None)
    letter = notifications.letter(address, club_subject("Проверка почты"), check_letter(settings.PUBLIC_URL))
    try:
        await notifications.deliver(letter)
    except Exception as error:
        raise OperatorError(f"Письмо не отправлено: {type(error).__name__}: {error}"[:ERROR_LENGTH]) from None
    return f"Проверочное письмо отправлено на {address} через {settings.SMTP_HOST}:{settings.SMTP_PORT}"
