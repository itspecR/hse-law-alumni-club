import asyncio
from email.utils import parseaddr
from unittest.mock import AsyncMock

import aiosmtplib
import pytest
from club_ops.bootstrap import OperatorError
from club_ops.mail_check import send_test_mail

from club_api.core.config import Settings
from club_api.modules.notifications.mail import CLUB_NAME, Notifications


async def test_smtp_downgrade_refuses_authentication_and_message():
    commands = []

    async def relay(reader, writer):
        writer.write(b"220 synthetic SMTP\r\n")
        await writer.drain()
        try:
            while line := await reader.readline():
                commands.append(line)
                if line.upper().startswith(b"EHLO"):
                    writer.write(b"250-synthetic\r\n250-AUTH PLAIN LOGIN\r\n250 SIZE 100000\r\n")
                elif line.upper().startswith(b"QUIT"):
                    writer.write(b"221 goodbye\r\n")
                    await writer.drain()
                    break
                elif line.upper().startswith(b"AUTH"):
                    writer.write(b"235 authenticated\r\n")
                else:
                    writer.write(b"250 accepted\r\n")
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(relay, "127.0.0.1", 0)
    async with server:
        settings = Settings(
            AUTH_SECRET="synthetic-session-secret-for-tests-only",
            SMTP_HOST="127.0.0.1",
            SMTP_PORT=server.sockets[0].getsockname()[1],
            SMTP_USER="synthetic-user@example.test",
            SMTP_PASS="synthetic-password",
            SMTP_FROM="sender@example.test",
        )
        sender = Notifications(settings, None, None)
        assert not await sender.send_email("recipient@example.test", "Тест", "synthetic-recovery-token")
    assert any(command.upper().startswith(b"EHLO") for command in commands)
    assert not any(command.upper().startswith((b"AUTH", b"MAIL", b"RCPT", b"DATA")) for command in commands)


@pytest.mark.parametrize(
    ("host", "port", "user", "password", "public_url", "implicit", "start_tls"),
    [
        ("smtp.example.test", 465, "user", "synthetic", "https://club.example.test", True, False),
        ("smtp.example.test", 587, "user", "synthetic", "https://club.example.test", False, True),
        ("mailpit", 1025, "", "", "https://localhost:9543", False, False),
        ("mailpit", 1025, "user", "synthetic", "https://localhost:9543", False, True),
        ("mailpit", 1025, "", "", "https://club.example.test", False, True),
    ],
)
async def test_mailpit_exception_is_limited_to_local_no_credentials(
    monkeypatch, host, port, user, password, public_url, implicit, start_tls
):
    send = AsyncMock()
    monkeypatch.setattr("club_api.modules.notifications.mail.aiosmtplib.send", send)
    settings = Settings(
        AUTH_SECRET="synthetic-session-secret-for-tests-only",
        SMTP_HOST=host,
        SMTP_PORT=port,
        SMTP_USER=user,
        SMTP_PASS=password,
        SMTP_FROM="sender@example.test",
        PUBLIC_URL=public_url,
    )
    assert await Notifications(settings, None, None).send_email("recipient@example.test", "Тест", "Вымышленное письмо")
    options = send.await_args.kwargs
    assert options["use_tls"] is implicit and options["start_tls"] is start_tls
    assert options["validate_certs"] is True


SETTINGS = {"AUTH_SECRET": "synthetic-session-secret-for-tests-only", "SMTP_HOST": "smtp.example.test"}


@pytest.mark.parametrize(
    ("sender", "reply", "expected_from", "expected_reply"),
    [
        ("noreply@example.test", "club@example.test", "noreply@example.test", "club@example.test"),
        ("Офис клуба <noreply@example.test>", "", "Офис клуба <noreply@example.test>", None),
    ],
)
async def test_letter_has_club_name_and_reply_address(monkeypatch, sender, reply, expected_from, expected_reply):
    send = AsyncMock()
    monkeypatch.setattr("club_api.modules.notifications.mail.aiosmtplib.send", send)
    settings = Settings(**SETTINGS, SMTP_FROM=sender, MAIL_REPLY_TO=reply)
    assert await Notifications(settings, None, None).send_email("recipient@example.test", "Тест", "Вымышленное письмо")
    message = send.await_args.args[0]
    name, address = parseaddr(str(message["From"]))
    assert address == parseaddr(expected_from)[1]
    assert name == (parseaddr(expected_from)[0] or CLUB_NAME)
    assert message["Reply-To"] == expected_reply


def test_reply_address_is_validated():
    with pytest.raises(ValueError, match="MAIL_REPLY_TO"):
        Settings(**SETTINGS, MAIL_REPLY_TO="not an address")


async def test_operator_test_mail_reports_delivery(monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr("club_api.modules.notifications.mail.aiosmtplib.send", send)
    values = {**SETTINGS, "SMTP_FROM": "noreply@example.test", "TEST_MAIL_TO": " Owner@Example.Test "}
    result = await send_test_mail(values)
    message = send.await_args.args[0]
    assert result == "Проверочное письмо отправлено на owner@example.test через smtp.example.test:587"
    assert message["To"] == "owner@example.test" and message["Subject"].startswith("Проверка почты")
    assert "отправка писем настроена" in message.get_content()


async def test_operator_test_mail_names_failure_without_secrets(monkeypatch):
    send = AsyncMock(side_effect=aiosmtplib.SMTPAuthenticationError(535, "authentication failed"))
    monkeypatch.setattr("club_api.modules.notifications.mail.aiosmtplib.send", send)
    values = {**SETTINGS, "SMTP_PASS": "synthetic-secret-password", "TEST_MAIL_TO": "owner@example.test"}
    with pytest.raises(OperatorError) as error:
        await send_test_mail(values)
    assert str(error.value).startswith("Письмо не отправлено: SMTPAuthenticationError")
    assert "synthetic-secret-password" not in str(error.value)
    with pytest.raises(OperatorError, match="SMTP_HOST"):
        await send_test_mail({**values, "SMTP_HOST": ""})
