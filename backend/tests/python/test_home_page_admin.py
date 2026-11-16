import pytest
from pydantic import ValidationError

from club_api.modules.content.admin import HERO_SUBTITLE_LIMIT, HeroBody


def test_hero_line_is_saved_as_typed():
    body = HeroBody.model_validate({"subtitle": "Встречи выпускников и новости факультета."})
    assert body.model_dump(exclude_unset=True) == {"subtitle": "Встречи выпускников и новости факультета."}


def test_hero_line_longer_than_the_limit_is_rejected():
    with pytest.raises(ValidationError):
        HeroBody.model_validate({"subtitle": "а" * (HERO_SUBTITLE_LIMIT + 1)})
