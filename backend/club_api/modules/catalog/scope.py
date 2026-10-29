from club_api.domain import DPO_CATALOG, PORTFOLIO_CATALOG

PUBLISHED = {"status": {"_eq": "published"}}
DPO_PROGRAMS = {"catalog": {"_eq": DPO_CATALOG}}
LINKED = {"source_url": {"_starts_with": "https://"}}
PUBLISHED_DPO = {**PUBLISHED, **DPO_PROGRAMS, **LINKED}
PORTFOLIO_PROGRAMS = {"catalog": {"_eq": PORTFOLIO_CATALOG}}
PUBLISHED_PORTFOLIO = {**PUBLISHED, **PORTFOLIO_PROGRAMS}
