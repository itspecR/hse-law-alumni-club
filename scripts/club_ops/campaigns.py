import json
from importlib.resources import files
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "data/campaign-templates.json"
OPENING_TEMPLATE = "intensive"
LINK_FIELDS = ("trainer_ids",)
PUBLISHED = "published"


def read_templates():
    resource = files("club_ops").joinpath("data/campaign-templates.json")
    return json.loads(
        resource.read_text(encoding="utf-8") if resource.is_file() else TEMPLATES.read_text(encoding="utf-8")
    )


def opening_campaign():
    record = read_templates()[OPENING_TEMPLATE]["record"]
    return {**{name: value for name, value in record.items() if name not in LINK_FIELDS}, "status": PUBLISHED}
