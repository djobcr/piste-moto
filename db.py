"""Schéma SQLite + helpers upsert."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(__file__).parent / "piste.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organizer       TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    circuit         TEXT NOT NULL DEFAULT 'Alès',
    date            TEXT NOT NULL,
    title           TEXT NOT NULL,
    price_cents     INTEGER,
    currency        TEXT NOT NULL DEFAULT 'EUR',
    available       INTEGER NOT NULL,
    booking_url     TEXT NOT NULL,
    levels          TEXT,                  -- JSON array: [{raw, canonical, remaining?, max?}]
    raw_data        TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    UNIQUE(organizer, source_id)
);

CREATE INDEX IF NOT EXISTS idx_events_date     ON events(date);
CREATE INDEX IF NOT EXISTS idx_events_circuit  ON events(circuit);
CREATE INDEX IF NOT EXISTS idx_events_avail    ON events(available);

-- Vue : événements futurs uniquement (utile pour le frontend / queries client)
CREATE VIEW IF NOT EXISTS events_active AS
SELECT * FROM events WHERE date >= date('now');
"""

# Migration légère : si une vieille DB n'a pas la colonne `levels`, on l'ajoute.
def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if "levels" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN levels TEXT")
        # La vue events_active dépend du select * — on la recrée.
        conn.execute("DROP VIEW IF EXISTS events_active")
        conn.execute("CREATE VIEW events_active AS SELECT * FROM events WHERE date >= date('now')")


@dataclass
class Level:
    """Niveau de pilotage proposé pour un event.

    `canonical` est le slug normalisé (debutant, intermediaire, confirme,
    expert, open) — utilisé pour le filtrage côté UI.
    `raw` est le label affiché (ex: "Débutant", "Pilote", "Tous niveaux").
    `remaining` et `max` peuvent être None si la source ne les expose pas.
    """
    raw: str
    canonical: str
    remaining: int | None = None
    max: int | None = None


@dataclass
class Event:
    organizer: str
    source_id: str
    date: str           # ISO YYYY-MM-DD
    title: str
    booking_url: str
    available: bool
    price_cents: int | None = None
    currency: str = "EUR"
    circuit: str = "Alès"
    levels: list[Level] = field(default_factory=list)
    raw_data: dict[str, Any] = field(default_factory=dict)


@contextmanager
def connect(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def upsert_events(events: list[Event], db_path: Path = DB_PATH) -> tuple[int, int]:
    """Insert new or update existing events. Returns (inserted, updated)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    inserted = 0
    updated = 0

    with connect(db_path) as conn:
        for ev in events:
            existing = conn.execute(
                "SELECT id FROM events WHERE organizer = ? AND source_id = ?",
                (ev.organizer, ev.source_id),
            ).fetchone()

            levels_json = json.dumps(
                [
                    {k: v for k, v in {
                        "raw": lv.raw,
                        "canonical": lv.canonical,
                        "remaining": lv.remaining,
                        "max": lv.max,
                    }.items() if v is not None}
                    for lv in (ev.levels or [])
                ],
                ensure_ascii=False,
            )
            payload = {
                "organizer":   ev.organizer,
                "source_id":   ev.source_id,
                "circuit":     ev.circuit,
                "date":        ev.date,
                "title":       ev.title,
                "price_cents": ev.price_cents,
                "currency":    ev.currency,
                "available":   1 if ev.available else 0,
                "booking_url": ev.booking_url,
                "levels":      levels_json,
                "raw_data":    json.dumps(ev.raw_data, ensure_ascii=False),
                "last_seen_at": now,
            }

            if existing is None:
                payload["first_seen_at"] = now
                cols = ",".join(payload.keys())
                placeholders = ",".join(f":{k}" for k in payload.keys())
                conn.execute(f"INSERT INTO events ({cols}) VALUES ({placeholders})", payload)
                inserted += 1
            else:
                set_clause = ",".join(f"{k}=:{k}" for k in payload.keys())
                payload["id"] = existing["id"]
                conn.execute(f"UPDATE events SET {set_clause} WHERE id=:id", payload)
                updated += 1

    return inserted, updated
