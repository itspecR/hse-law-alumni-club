import re
import unicodedata
from typing import Annotated

from pydantic import AfterValidator

from club_api.domain import DOMAIN

PERSON_NAME_MIN = 2
PERSON_NAME_MAX = DOMAIN["limits"]["person_name_max"]
LETTER = r"[^\W\d_]"
WORD = rf"{LETTER}+(?:[-'’]{LETTER}+)*"
INITIALS = rf"(?:{LETTER}\.)+"
PERSON = re.compile(rf"(?:{WORD}|{INITIALS})(?: (?:{WORD}|{INITIALS}))*")
INVALID_NAME = "Имя пишется буквами; можно пробел, дефис, апостроф и точку после инициала."


def person_name(value):
    name = " ".join(unicodedata.normalize("NFC", value).split())
    if not PERSON_NAME_MIN <= len(name) <= PERSON_NAME_MAX or not PERSON.fullmatch(name):
        raise ValueError(INVALID_NAME)
    return name


def optional_person_name(value):
    return person_name(value) if value.strip() else ""


PersonName = Annotated[str, AfterValidator(person_name)]
OptionalPersonName = Annotated[str, AfterValidator(optional_person_name)]
