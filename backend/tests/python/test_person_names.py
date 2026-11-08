import pytest
from pydantic import ValidationError

from club_api.core.names import PERSON_NAME_MAX, optional_person_name, person_name
from club_api.modules.auth.routes import RegisterBody
from club_api.modules.checkout.orders import OrderBody
from club_api.modules.donations.routes import DonationBody
from club_api.modules.members.profile import ProfileBody
from club_api.modules.portfolio.applications import ApplicationBody

NAME_FIELDS = [
    (RegisterBody, "fio"),
    (ProfileBody, "fio"),
    (OrderBody, "contact_fio"),
    (ApplicationBody, "contact_fio"),
    (DonationBody, "name"),
]
REJECTED = [
    "Иван 2",
    "evil.com",
    "Перейдите на https://evil.test",
    "<b>Анна</b>",
    "Анна!",
    "Я",
    "Анна_Иванова",
    "Анна--Мария",
    "А" * (PERSON_NAME_MAX + 1),
]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Анна Вымышленная", "Анна Вымышленная"),
        ("  Анна   Вымышленная ", "Анна Вымышленная"),
        ("Пушкин А.С.", "Пушкин А.С."),
        ("А. С. Пушкин", "А. С. Пушкин"),
        ("Анна-Мария О'Коннор", "Анна-Мария О'Коннор"),
        ("Жанна д’Арк", "Жанна д’Арк"),
        ("John Smith", "John Smith"),
        ("Йоко", "Йоко"),
        ("Ёлкина Ия", "Ёлкина Ия"),
        ("А" * PERSON_NAME_MAX, "А" * PERSON_NAME_MAX),
    ],
)
def test_person_name_accepts_real_names(value, expected):
    assert person_name(value) == expected


def test_person_name_composes_letters_with_marks():
    assert person_name("\u0418\u0438\u0306\u043a\u0430") == "\u0418\u0439\u043a\u0430"


@pytest.mark.parametrize("value", REJECTED)
def test_person_name_rejects_text_that_is_not_a_name(value):
    with pytest.raises(ValueError):
        person_name(value)


def test_optional_name_may_stay_empty():
    assert optional_person_name("   ") == "" and optional_person_name(" Анна ") == "Анна"


def name_errors(model, field, value):
    try:
        model.model_validate({field: value})
    except ValidationError as error:
        return [item for item in error.errors() if item["loc"] == (field,)]
    return []


@pytest.mark.parametrize(("model", "field"), NAME_FIELDS)
@pytest.mark.parametrize("value", REJECTED)
def test_every_form_with_a_name_rejects_the_same_text(model, field, value):
    assert name_errors(model, field, value)


@pytest.mark.parametrize(("model", "field"), NAME_FIELDS)
def test_every_form_with_a_name_accepts_a_real_name(model, field):
    assert name_errors(model, field, "Анна Вымышленная") == []
