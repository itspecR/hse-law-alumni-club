from club_api.modules.campaigns.store import published, published_by_slug
from club_api.modules.donations.service import SUCCEEDED
from club_api.modules.portfolio.links import published_trainers
from club_api.modules.portfolio.relations import LinkTable

CAMPAIGN_TRAINERS = LinkTable("club_campaign_trainers", "campaign_id", "trainer_id", ordered=True)
DONATED_SQL = "SELECT COALESCE(SUM(amount),0) AS donated FROM club_donations WHERE campaign_id=%s AND status=%s"
FULL_PERCENT = 100
INTERNAL_FIELDS = ("id", "extra_amount")
TEASER_FIELDS = (
    *INTERNAL_FIELDS,
    "slug",
    "title",
    "subtitle",
    "lede",
    "date_text",
    "place_text",
    "goal_title",
    "goal_amount",
    "cover",
)
DETAIL_FIELDS = (
    *TEASER_FIELDS,
    "time_text",
    "duration_text",
    "goal_text",
    "invite_threshold",
    "amounts",
    "about_title",
    "about",
    "highlights",
    "program_title",
    "modules",
    "feature_title",
    "feature_note",
    "feature_text",
    "results_title",
    "results",
    "steps",
    "faq",
    "note",
)


def progress(campaign, donated):
    raised = donated + campaign["extra_amount"]
    goal = campaign["goal_amount"]
    return {"raised": raised, "percent": min(FULL_PERCENT, raised * FULL_PERCENT // goal) if goal > 0 else 0}


async def donated(state, campaign_id):
    rows = await state.database.rows(DONATED_SQL, (campaign_id, SUCCEEDED))
    return int(rows[0]["donated"])


async def shown(state, campaign):
    visible = {name: value for name, value in campaign.items() if name not in INTERNAL_FIELDS}
    return {**visible, **progress(campaign, await donated(state, campaign["id"]))}


async def current_campaign(state):
    campaign = await published(state, TEASER_FIELDS)
    return await shown(state, campaign) if campaign else None


async def published_campaign(state, slug):
    campaign = await published_by_slug(state, slug, DETAIL_FIELDS)
    if not campaign:
        return None
    speakers = await published_trainers(state, campaign["id"], CAMPAIGN_TRAINERS)
    return {**await shown(state, campaign), "speakers": speakers}
