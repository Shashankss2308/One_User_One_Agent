import json
import sqlite3

from ott_agent.goals import GoalBook
from ott_agent.ingest import SCHEMA
from ott_agent.outcomes import g09_success_events
from ott_agent.providers import MockProvider
from ott_agent.planner import resolve_dialect
from ott_agent.runtime import ingest_one


def event(event_id="evt-1"):
    return {
        "event_id": event_id,
        "user_id": "U-test",
        "event": "app_open",
        "occurred_at": "2026-10-01T10:00:00Z",
        "sent_at": "2026-10-01T10:00:01Z",
        "received_at": "2026-10-01T10:00:02Z",
        "app_dialect": "d3",
        "app_language": "hin",
        "properties": {},
    }


def test_duplicate_delivery_is_idempotent():
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    assert ingest_one(db, event()) is True
    assert ingest_one(db, event()) is False
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_app_dialect_wins_but_conflicts_remain_visible():
    trigger = {"app_dialect": "d3", "properties": {"deeplink": "ott://c/camp/d4/title/T1"}}
    facts = {"dialect": {"value": "d2"}}
    profile = {"preferred_dialect": "d1"}
    result = resolve_dialect(trigger, facts, profile)
    assert result["used"] == "d3"
    assert result["conflicts"] == ["deeplink:d4", "last_history_setting:d2", "profile:d1"]


def test_model_timeout_is_available_for_runtime_fallback(tmp_path):
    fault_file = tmp_path / "faults.jsonl"
    fault_file.write_text(json.dumps({"trigger_event_id": "evt-1", "fault": "timeout"}) + "\n")
    provider = MockProvider(str(fault_file))
    try:
        provider.advise("evt-1", ["G01"], "G01")
    except TimeoutError:
        pass
    else:
        raise AssertionError("configured provider timeout was not raised")


def test_goal_rule_correction_appends_version(tmp_path):
    config = tmp_path / "goals.json"
    config.write_text(json.dumps({"goals": [{"goal_id": "G01", "success": {"event": "play_started"}}]}))
    book = GoalBook(str(config))
    book.apply_correction("G01", {"event": "episode_completed"}, "2026-10-01T12:00:00Z")
    assert book.current_success("G01") == {"event": "episode_completed"}
    assert book.history["G01"][0]["success"] == {"event": "play_started"}
    assert book.history["G01"][1]["rule_version"] == 2


def test_weekly_habit_requires_three_distinct_ist_days():
    plays = [
        {"event": "play_started", "received_at": "2026-10-01T10:00:00Z"},
        {"event": "play_started", "received_at": "2026-10-01T12:00:00Z"},
        {"event": "play_started", "received_at": "2026-10-02T10:00:00Z"},
    ]
    assert g09_success_events(plays) == []
    plays.append({"event": "play_started", "received_at": "2026-10-03T10:00:00Z"})
    assert len(g09_success_events(plays)) == 1
