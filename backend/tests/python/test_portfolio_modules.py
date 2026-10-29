import pytest
from pydantic import ValidationError

from club_api.modules.portfolio.admin import MAX_MODULES, PortfolioBody

PROGRAM = {"title": "Конфликт как ресурс", "format": "blended", "duration": "26 ак. ч.", "price": 0}
MODULE = {"title": "День 1", "points": ["Лекция", "Практикум"], "hours": "6 ак. ч.", "format": "Аудиторная работа"}


def test_program_keeps_course_modules_in_order():
    body = PortfolioBody.model_validate({**PROGRAM, "modules": [MODULE, {"title": "Консультация"}]})
    assert body.model_dump()["modules"] == [
        MODULE,
        {"title": "Консультация", "points": [], "hours": None, "format": None},
    ]


@pytest.mark.parametrize(
    "modules",
    [
        [{"points": ["Без темы"]}],
        [{"title": ""}],
        [MODULE] * (MAX_MODULES + 1),
        [{**MODULE, "hours": "ч" * 101}],
    ],
)
def test_invalid_course_modules_are_rejected(modules):
    with pytest.raises(ValidationError):
        PortfolioBody.model_validate({**PROGRAM, "modules": modules})
