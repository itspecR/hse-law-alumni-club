import re

from club_api.core.errors import ApiError
from club_api.db.operator import is_operator
from club_api.modules.auth.passwords import hash_password
from club_api.modules.auth.service import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    VERIFIED,
    alumni_profile,
    insert_alumni,
)
from club_ops.bootstrap import OperatorError, email

DEFAULT_FIO = "Тестовый Выпускник"
COHORT = "2020"
EDU_LEVEL = "магистратура"
EDU_PROGRAM = "Юриспруденция"
FIO_LENGTH = range(2, 201)


def validate_input(address, fio, password):
    address = email(address, "ALUMNI_EMAIL")
    fio = " ".join((fio or DEFAULT_FIO).split())
    if len(fio) not in FIO_LENGTH:
        raise OperatorError("ALUMNI_FIO должен содержать от 2 до 200 символов")
    if not PASSWORD_MIN_LENGTH <= len(password or "") <= PASSWORD_MAX_LENGTH or re.search(r"[\r\n]", password):
        raise OperatorError(
            f"Пароль выпускника должен содержать {PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} символов без переноса строки"
        )
    return address, fio, password


async def create_alumni(connection, *, address, fio, password):
    address, fio, password = validate_input(address, fio, password)
    hashed = await hash_password(password)
    profile = alumni_profile(fio=fio, cohort=COHORT, edu_level=EDU_LEVEL, edu_program=EDU_PROGRAM)
    async with connection.transaction():
        if not await is_operator(connection):
            raise OperatorError("Команда доступна только владельцу базы данных")
        try:
            await insert_alumni(
                connection, email=address, hashed=hashed, status="active", verification=VERIFIED, profile=profile
            )
        except ApiError:
            raise OperatorError("Аккаунт с этой почтой уже есть; команда ничего не изменила") from None
    return address
