from club_api.modules.events.routes import EventBody


def test_event_time_without_zone_is_moscow_time():
    assert EventBody(title="Встреча клуба", starts_at="2026-11-20T19:00").starts_at == "2026-11-20T19:00:00+03:00"


def test_event_time_with_zone_is_kept():
    assert EventBody(title="Встреча клуба", starts_at="2026-11-20T16:00:00Z").starts_at == "2026-11-20T16:00:00Z"
