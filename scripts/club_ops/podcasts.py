import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, TypeAdapter

from club_ops.bootstrap import OperatorError


class Episode(BaseModel):
    model_config = ConfigDict(extra="ignore")
    file: StrictStr = Field(min_length=1)
    title: StrictStr = Field(min_length=1, max_length=255)
    description: StrictStr = ""
    duration: StrictStr = Field(default="", max_length=100)
    is_free: StrictBool = False
    sort: StrictInt = 0
    status: StrictStr = Field(default="draft", pattern="^(draft|published)$")


async def import_podcasts(office, manifest):
    manifest = Path(manifest)
    episodes = TypeAdapter(list[Episode]).validate_python(json.loads(manifest.read_text(encoding="utf-8")))
    if not 1 <= len(episodes) <= 100:
        raise OperatorError("Манифест должен содержать 1–100 выпусков")
    existing = await office.request("podcasts")
    for episode in episodes:
        path = manifest.parent / episode.file
        if not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
            raise OperatorError("Аудиофайл отсутствует или превышает 128 MiB")
        with path.open("rb") as stream:
            media = await office.request(
                "media", "POST", files={"file": (path.name, stream, "application/octet-stream")}
            )
        found = next((row for row in existing if row["title"] == episode.title), None)
        try:
            saved = await office.request(
                "podcasts/" + found["id"] if found else "podcasts",
                "PATCH" if found else "POST",
                json={**episode.model_dump(exclude={"file"}), "audio_url": media["id"]},
            )
        except Exception:
            try:
                await office.request("media/" + media["id"], "DELETE")
            except Exception:
                logging.getLogger(__name__).warning(
                    "Загруженный файл остался в панели управления; удалите его после проверки"
                )
            raise
        if not found:
            existing.append({"id": saved["id"], "title": episode.title})
        print("Выпуск сохранён")
