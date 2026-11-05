from datetime import UTC, datetime

from club_api.resources import shared_data

MAILING_CONSENT_VERSION = shared_data("legal-data.json")["mailing_consent"]["version"]
NO_MAILING = {"mailing_consent_at": None, "mailing_consent_version": None}


def mailing_consent(given):
    if not given:
        return dict(NO_MAILING)
    return {"mailing_consent_at": datetime.now(UTC), "mailing_consent_version": MAILING_CONSENT_VERSION}
