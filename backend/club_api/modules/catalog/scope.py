from club_api.domain import DPO_CATALOG, PORTFOLIO_CATALOG

PUBLISHED = {"status": {"_eq": "published"}}
DPO_PROGRAMS = {"catalog": {"_eq": DPO_CATALOG}}
PUBLISHED_DPO = {**PUBLISHED, **DPO_PROGRAMS}
PUBLISHED_PORTFOLIO = {**PUBLISHED, "catalog": {"_eq": PORTFOLIO_CATALOG}}
