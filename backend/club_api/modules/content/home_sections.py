from typing import Literal

from pydantic import Field, model_validator

from club_api.core.schemas import Body

HOME_SLUG = "home"
SECTION_TITLE_LIMIT = 120
SECTION_LEDE_LIMIT = 600
SECTIONS = {
    "goals": {"title": True, "lede": False, "visible": True},
    "join": {"title": True, "lede": True, "visible": True},
    "campaign": {"title": False, "lede": False, "visible": True},
    "trainings": {"title": True, "lede": True, "visible": True},
    "dpo": {"title": True, "lede": False, "visible": True},
    "events": {"title": True, "lede": True, "visible": True},
    "people": {"title": True, "lede": False, "visible": True},
    "read": {"title": True, "lede": False, "visible": False},
    "give": {"title": True, "lede": True, "visible": True},
}
SectionKey = Literal[*SECTIONS]


class HomeSection(Body):
    key: SectionKey
    visible: bool = True
    title: str = Field(default=None, max_length=SECTION_TITLE_LIMIT)
    lede: str = Field(default=None, max_length=SECTION_LEDE_LIMIT)

    @model_validator(mode="after")
    def editable_texts_only(self):
        spec = SECTIONS[self.key]
        if self.title and not spec["title"]:
            raise ValueError("У этого раздела заголовок не меняется.")
        if self.lede and not spec["lede"]:
            raise ValueError("У этого раздела нет вводного текста.")
        return self


def unique_sections(sections):
    keys = [section.key for section in sections]
    if len(keys) != len(set(keys)):
        raise ValueError("Каждый раздел указывается один раз.")
    return sections


def default_section(key):
    return {"key": key, "visible": SECTIONS[key]["visible"], "title": None, "lede": None}


def stored_section(item):
    return {
        "key": item["key"],
        "visible": bool(item.get("visible", True)),
        "title": (item.get("title") or None) if SECTIONS[item["key"]]["title"] else None,
        "lede": (item.get("lede") or None) if SECTIONS[item["key"]]["lede"] else None,
    }


def resolve_sections(saved):
    known = [item for item in saved or [] if isinstance(item, dict) and item.get("key") in SECTIONS]
    chosen = list({item["key"]: stored_section(item) for item in known}.values())
    present = {item["key"] for item in chosen}
    return chosen + [default_section(key) for key in SECTIONS if key not in present]


def section_specs():
    return [{"key": key, "title": spec["title"], "lede": spec["lede"]} for key, spec in SECTIONS.items()]
