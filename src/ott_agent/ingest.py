"""Step 1: load events.jsonl into SQLite, once, safely."""
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

SKEW_LIMIT_S = 300      # phone clock >5 min off  => clock_skewed
BUFFER_LIMIT_S = 300    # sent >5 min after it happened => buffered

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  event TEXT NOT NULL,
  occurred_at TEXT, sent_at TEXT, received_at TEXT NOT NULL,
  best_time TEXT NOT NULL,
  clock_offset_s REAL, buffered_s REAL,
  clock_skewed INTEGER DEFAULT 0, buffered INTEGER DEFAULT 0,
  app_dialect TEXT, app_language TEXT,
  session_id TEXT, properties TEXT
);
CREATE INDEX IF NOT EXISTS ix_user_time ON events(user_id, best_time);
"""


def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ingest(events_path, db_path):
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    stats, seen = Counter(), {}

    with open(events_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                stats["bad_json"] += 1
                continue
            stats["lines"] += 1

            eid = e["event_id"]
            digest = hashlib.md5(line.encode()).hexdigest()
            if eid in seen:  # duplicate delivery: keep the first copy
                stats["dup_identical" if seen[eid] == digest else "dup_different"] += 1
                continue
            seen[eid] = digest

            received = parse(e["received_at"])  # trusted server clock
            best, offset, buffered_s = received, None, None
            skewed = buffered = 0
            try:
                occurred, sent = parse(e["occurred_at"]), parse(e["sent_at"])
                offset = (sent - received).total_seconds()
                buffered_s = (sent - occurred).total_seconds()
                skewed = int(abs(offset) > SKEW_LIMIT_S)
                buffered = int(buffered_s > BUFFER_LIMIT_S)
                best = occurred - timedelta(seconds=offset) if skewed else occurred
                if best > received:  # cannot happen after it was received
                    best = received
                    stats["clamped_future"] += 1
            except (KeyError, ValueError):
                stats["bad_time_used_received_at"] += 1

            db.execute(
                "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, e["user_id"], e["event"], e.get("occurred_at"), e.get("sent_at"),
                 e["received_at"], fmt(best), offset, buffered_s, skewed, buffered,
                 e.get("app_dialect"), e.get("app_language"), e.get("session_id"),
                 json.dumps(e.get("properties", {}))))
    db.commit()
    return db, stats


def report(db, stats):
    q = lambda sql: db.execute(sql).fetchall()
    print("== INGEST STATS ==", dict(stats))
    print("events stored:", q("SELECT COUNT(*) FROM events")[0][0])
    print("distinct users:", q("SELECT COUNT(DISTINCT user_id) FROM events")[0][0])
    print("== EVENTS BY TYPE ==")
    for name, n in q("SELECT event, COUNT(*) FROM events GROUP BY event ORDER BY 2 DESC"):
        print(f"  {name:25s}{n}")
    print("clock_skewed:", q("SELECT COUNT(*) FROM events WHERE clock_skewed=1")[0][0],
          "| max |offset| hours:",
          round(q("SELECT MAX(ABS(clock_offset_s)) FROM events")[0][0] / 3600, 1))
    print("buffered:", q("SELECT COUNT(*) FROM events WHERE buffered=1")[0][0],
          "| max buffered hours:",
          round(q("SELECT MAX(buffered_s) FROM events")[0][0] / 3600, 1))
    print("best_time range:", q("SELECT MIN(best_time), MAX(best_time) FROM events")[0])


if __name__ == "__main__":
    # usage: python src/ott_agent/ingest.py <events.jsonl> <out.db>
    database, st = ingest(sys.argv[1], sys.argv[2])
    report(database, st)