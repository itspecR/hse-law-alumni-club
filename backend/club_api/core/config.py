import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", env_file=None)

    API_HOST: str = "0.0.0.0"
    API_PORT: int = Field(default=3000, ge=1, le=65535)
    APP_ENV: Literal["development", "production"] = "development"
    CHECKOUT_DATABASE_URL: SecretStr = SecretStr("")
    UPLOADS_PATH: Path = Path("/data/uploads")
    SUPPORT_ENABLED: Literal["true", "false"] = "false"
    SUPPORT_OPERATOR_NAME: str = "Автономная некоммерческая организация «Клуб выпускников факультета права НИУ ВШЭ»"
    SUPPORT_OPERATOR_ADDRESS: str = "107061, г. Москва, ул. Большая Черкизовская, д. 3, к. 2, помещ. 17/1"
    SUPPORT_OPERATOR_CONTACT: str = "Письменное обращение по юридическому адресу оператора"
    SUPPORT_RETENTION_DAYS: int = Field(default=30, ge=1, le=365)
    OFFICE_EMAIL: str = ""
    POINTS_SERVICE_TOKEN: SecretStr = SecretStr("")
    AUTH_SECRET: SecretStr = Field(min_length=32)
    ADMIN_AUTH_SECRET: SecretStr = SecretStr("")
    NEWS_SYNC_ENABLED: Literal["true", "false"] = "false"
    JOBS_ENABLED: Literal["true", "false"] = "true"
    DPO_SYNC_ENABLED: Literal["true", "false"] = "true"
    HSE_DPO_URL: str = ""
    HSE_DPO_ALL_URL: str = ""
    TELEGRAM_REACTIONS_CHAT_ID: str = Field(default="", pattern=r"^(-[0-9]+)?$")
    TELEGRAM_BOT_TOKEN: SecretStr = SecretStr("")
    TELEGRAM_WEBHOOK_SECRET: SecretStr = SecretStr("")
    TELEGRAM_POLLING: str = ""
    TELEGRAM_BOT_USERNAME: str = "pravohse_alumni_bot"
    CORS_ORIGINS: str = ""
    OFFICE_NOTIFY_CHANNEL: Literal["telegram", "email", "both"] = "telegram"
    OFFICE_TG_BOT_TOKEN: SecretStr = SecretStr("")
    OFFICE_TG_CHAT_ID: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_USER: str = ""
    SMTP_PASS: SecretStr = SecretStr("")
    SMTP_FROM: str = ""
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: SecretStr = SecretStr("")
    PUBLIC_URL: str = "http://localhost"
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: SecretStr = SecretStr("")
    SENTRY_DSN: SecretStr = SecretStr("")
    SEED_DEMO: str = ""
    ORDER_RETENTION_DAYS: int = Field(default=1095, gt=0)
    AUDIT_RETENTION_DAYS: int = Field(default=365, gt=0)
    RESERVE_TTL_HOURS: int = Field(default=72, ge=0, le=720)
    MAIL_OUTBOX_MAX_ATTEMPTS: int = Field(default=8, ge=1, le=50)
    RATE_LIMIT_MAX: int = Field(default=1000, gt=0)

    @field_validator("OFFICE_EMAIL")
    @classmethod
    def office_address(cls, value):
        value = value.strip()
        if value and (len(value) > 128 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)):
            raise ValueError("Некорректный OFFICE_EMAIL")
        return value

    def secret(self, name: str) -> str:
        return getattr(self, name).get_secret_value()

    def production_errors(self) -> list[str]:
        if self.APP_ENV != "production":
            return []
        errors = []
        placeholder = re.compile(r"replace_with|сгенерируйте|changeme|your[_-]?secret|example", re.I)
        database = self.secret("CHECKOUT_DATABASE_URL")
        if not database or placeholder.search(database):
            errors.append("CHECKOUT_DATABASE_URL должен содержать рабочее подключение")
        auth = self.secret("AUTH_SECRET")
        admin = self.secret("ADMIN_AUTH_SECRET")
        if placeholder.search(auth):
            errors.append("AUTH_SECRET содержит шаблонное значение")
        if len(admin) < 32 or placeholder.search(admin) or admin == auth:
            errors.append("ADMIN_AUTH_SECRET должен быть отдельным случайным секретом длиной не менее 32 символов")
        service = self.secret("POINTS_SERVICE_TOKEN")
        if service and (len(service) < 32 or placeholder.search(service)):
            errors.append("POINTS_SERVICE_TOKEN должен быть случайным секретом длиной не менее 32 символов")
        try:
            url = urlsplit(self.PUBLIC_URL)
            valid = url.scheme == "https" and bool(url.hostname) and not url.username and not url.password
            valid = valid and not url.query and not url.fragment and url.path in ("", "/")
        except ValueError:
            valid = False
        if not valid:
            errors.append("PUBLIC_URL должен быть https://<домен>")
        if (
            self.secret("TELEGRAM_BOT_TOKEN")
            and self.TELEGRAM_POLLING != "true"
            and not self.secret("TELEGRAM_WEBHOOK_SECRET")
        ):
            errors.append("Для webhook Telegram нужен TELEGRAM_WEBHOOK_SECRET")
        if not self.SMTP_HOST:
            errors.append("SMTP_HOST обязателен для подтверждения почты и восстановления доступа")
        elif not self.SMTP_FROM and not self.SMTP_USER:
            errors.append("Для SMTP нужен SMTP_FROM или SMTP_USER")
        if self.OFFICE_NOTIFY_CHANNEL in ("email", "both") and not self.OFFICE_EMAIL:
            errors.append("OFFICE_EMAIL обязателен для выбранного канала")
        if self.OFFICE_NOTIFY_CHANNEL in ("telegram", "both") and not (
            self.secret("OFFICE_TG_BOT_TOKEN") and self.OFFICE_TG_CHAT_ID
        ):
            errors.append("Для уведомлений сотрудникам нужны OFFICE_TG_BOT_TOKEN и OFFICE_TG_CHAT_ID")
        if bool(self.YOOKASSA_SHOP_ID) != bool(self.secret("YOOKASSA_SECRET_KEY")):
            errors.append("Оба ключа YOOKASSA должны быть заданы вместе")
        if bool(self.VAPID_PUBLIC_KEY) != bool(self.secret("VAPID_PRIVATE_KEY")):
            errors.append("Оба ключа VAPID должны быть заданы вместе")
        if self.SEED_DEMO == "true":
            errors.append("SEED_DEMO=true запрещён в production")
        return errors


def load_settings():
    try:
        return Settings()
    except ValidationError as error:
        fields = sorted({str(item["loc"][0]) for item in error.errors(include_input=False, include_url=False)})
        raise RuntimeError("Некорректная конфигурация: " + ", ".join(fields)) from None
