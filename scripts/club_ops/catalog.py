import json
from importlib.resources import files
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "data/dpo-catalog.json"


def read_catalog():
    resource = files("club_ops").joinpath("data/catalog.json")
    return json.loads(
        resource.read_text(encoding="utf-8") if resource.is_file() else CATALOG.read_text(encoding="utf-8")
    )
