import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

from club_api.modules.telegram.faq import FAQ, answer_faq, duration_text, program_to_bot, site_reply

QUALIFICATION = "Удостоверение о повышении квалификации"
RETRAINING = "Диплом о профессиональной переподготовке"


def program(slug, title, document, duration):
    return {"id": slug, "slug": slug, "title": title, "document": document, "duration": duration, "price": 3000000}


def test_faq_answers_have_questions_and_follow_style():
    for answer in FAQ["answers"]:
        assert answer["question"].endswith("?"), answer["id"]
        assert re.findall("[ёЁ–—]", answer["question"] + answer["text"]) == [], answer["id"]


def test_duration_ranges_use_hyphens():
    programs = [
        program_to_bot(program("a", "Налоги", QUALIFICATION, "2 месяца")),
        program_to_bot(program("b", "Споры", QUALIFICATION, "6 месяцев")),
        program_to_bot(program("c", "Комплаенс", RETRAINING, "3 месяца")),
        program_to_bot(program("d", "Корпоративное право", RETRAINING, "1 год")),
    ]
    assert duration_text(programs).splitlines() == [
        "Повышение квалификации: длительность обычно 2-6 месяцев.",
        "Профессиональная переподготовка: длительность обычно 3 месяца - 1 год.",
    ]


def bot_state(rows):
    return SimpleNamespace(
        settings=SimpleNamespace(PUBLIC_URL="https://club.example.test"),
        store=SimpleNamespace(read=AsyncMock(return_value=rows)),
        database=SimpleNamespace(pool=None),
    )


async def test_bot_lists_programs_without_gendered_intro():
    rows = [program("talks", "Переговоры для юристов", QUALIFICATION, "2 месяца")]
    reply = await answer_faq(bot_state(rows), "переговоры")
    assert reply.startswith("Подходит одна программа:")
    assert "30 000 ₽" in reply


async def test_price_range_keeps_sentence_commas():
    rows = [
        program("talks", "Переговоры", QUALIFICATION, "2 месяца"),
        {**program("corporate", "Корпоративное право", RETRAINING, "1 год"), "price": 15000000},
    ]
    reply = await site_reply(bot_state(rows), "сколько стоят программы")
    assert reply["text"] == (
        "Программы стоят от 30 000 до 150 000 ₽. Чтобы отобрать по цене, напишите, например, «до 30000» или «от 50000»."
    )
