import logging
import re
from datetime import UTC, datetime

from django.http import HttpRequest
from pydantic import Field

from club_api.core.views import api_view, parse_body
from club_api.db.queries import Query
from club_api.modules.auth.routes import Body

logger = logging.getLogger("club.analytics")
ROUTE_LIMITS = {("POST", "/analytics/pageview"): 60}
ALLOWED = [
    "/",
    "/(?:dpo|merch|news|events)(?:/[\\w.-]+)?",
    "/(?:podcasts|cart|join|forgot|reset|confirm|privacy|confidential|requisites)",
    "/lk(?:/profile)?",
    "/support(?:/consent)?",
]


def normalize_page_path(value):
    if not isinstance(value, str):
        return None
    path = re.split("[?#]", value.strip())[0]
    if not path.startswith("/") or len(path) > 120:
        return None
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    for prefix in ("/legacy", "/v2"):
        if path.startswith(prefix):
            path = path[len(prefix) :] or "/"
    return path if any(re.fullmatch(pattern, path, re.ASCII) for pattern in ALLOWED) else None


class PageViewBody(Body):
    path: str = Field(min_length=1, max_length=200)


@api_view(body=PageViewBody)
async def pageview(request: HttpRequest):
    body = parse_body(request, PageViewBody)
    state, path = (request.services, normalize_page_path(body.path))
    if path and state.database.pool:
        try:
            await state.database.execute(
                Query(
                    "INSERT INTO club_page_views(day,path,hits) VALUES(%s,%s,1) ON CONFLICT(day,path) DO UPDATE SET hits=club_page_views.hits+1",
                    "INSERT INTO club_page_views(day,path,hits) VALUES(%s,%s,1) ON DUPLICATE KEY UPDATE hits=club_page_views.hits+1",
                ),
                (datetime.now(UTC).date(), path),
            )
        except Exception:
            logger.warning("Не удалось записать счётчик страницы")
    return {"ok": True}


async def pageview_stats(state, since):
    result = {"hits": None, "by_day": [], "paths_top": []}
    if not state.database.pool:
        return result
    day = since.date()
    try:
        rows = await state.database.rows(
            Query(
                "SELECT COALESCE(sum(hits),0)::int AS hits FROM club_page_views WHERE day>=%s",
                "SELECT CAST(COALESCE(sum(hits),0) AS SIGNED) AS hits FROM club_page_views WHERE day>=%s",
            ),
            (day,),
        )
        result["hits"] = rows[0]["hits"]
        result["by_day"] = await state.database.rows(
            Query(
                "SELECT day::text AS day,sum(hits)::int AS count FROM club_page_views WHERE day>=%s GROUP BY day ORDER BY day",
                "SELECT CAST(day AS CHAR(36)) AS day,CAST(sum(hits) AS SIGNED) AS count FROM club_page_views WHERE day>=%s GROUP BY day ORDER BY day",
            ),
            (day,),
        )
        result["paths_top"] = await state.database.rows(
            Query(
                "SELECT path,sum(hits)::int AS count FROM club_page_views WHERE day>=%s GROUP BY path ORDER BY count DESC LIMIT 12",
                "SELECT path,CAST(sum(hits) AS SIGNED) AS count FROM club_page_views WHERE day>=%s GROUP BY path ORDER BY count DESC LIMIT 12",
            ),
            (day,),
        )
    except Exception:
        logger.warning("Не удалось получить счётчики страниц")
    return result
