from datetime import datetime, timezone

import audit_screen


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
ROWS = [
    {
        "action": "user.updated", "actor_id": "admin-1", "actor_name": "Ahmed",
        "target_id": "member-1", "target_email": "member@example.com",
        "org_name": "WSS", "created_at": "2026-08-23T10:00:00+00:00",
    },
    {
        "action": "team.upserted", "actor_id": "admin-2", "actor_name": "Mona",
        "org_name": "Test Org", "created_at": "2026-08-20T10:00:00+00:00",
    },
]


def test_filters_by_event_actor_target_and_search():
    assert audit_screen._filter_rows(ROWS, {"event": "user.updated"}, NOW) == [ROWS[0]]
    assert audit_screen._filter_rows(ROWS, {"actor_id": "admin-2"}, NOW) == [ROWS[1]]
    assert audit_screen._filter_rows(ROWS, {"target_id": "member-1"}, NOW) == [ROWS[0]]
    assert audit_screen._filter_rows(ROWS, {"query": "wss"}, NOW) == [ROWS[0]]


def test_date_filter_and_all_time():
    assert audit_screen._filter_rows(ROWS, {"period": "24h"}, NOW) == [ROWS[0]]
    assert audit_screen._filter_rows(ROWS, {"period": "all"}, NOW) == ROWS
