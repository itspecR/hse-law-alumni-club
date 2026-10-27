from club_api.domain import DPO_CATALOG

PUBLISHED = {"status": {"_eq": "published"}}
DPO_PROGRAMS = {"catalog": {"_eq": DPO_CATALOG}}
PUBLISHED_DPO = {**PUBLISHED, **DPO_PROGRAMS}
