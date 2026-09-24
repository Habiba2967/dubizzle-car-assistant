"""
memory.py — Long-term memory store (SQLite).

Design decision (explain this in the interview):
We use SQLite instead of a vector DB for long-term memory because what we're
storing is a handful of structured facts per user (name, price range, liked
cars) — not free-text documents that need semantic search. A plain relational
table is simpler, queryable, inspectable with any SQLite viewer, and needs
zero extra infra. This is the "Persistent Storage Module" required by the brief.

Short-term (within-session) memory is handled separately in app/main.py as an
in-memory dict of conversation turns per session_id — it does not need to
survive a server restart, so it doesn't belong in this file.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "memory.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                name TEXT,
                price_min INTEGER,
                price_max INTEGER,
                preferences TEXT,          -- free text, e.g. "white SUV, low mileage"
                created_at TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS liked_cars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                listing_id INTEGER,
                note TEXT,
                created_at TEXT
            )
        """)


def get_user(user_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def upsert_user(user_id: str, name: str | None = None, price_min: int | None = None,
                 price_max: int | None = None, preferences: str | None = None) -> dict:
    now = datetime.utcnow().isoformat()
    existing = get_user(user_id)
    with get_conn() as conn:
        if existing:
            conn.execute("""
                UPDATE users SET
                    name = COALESCE(?, name),
                    price_min = COALESCE(?, price_min),
                    price_max = COALESCE(?, price_max),
                    preferences = COALESCE(?, preferences),
                    updated_at = ?
                WHERE user_id = ?
            """, (name, price_min, price_max, preferences, now, user_id))
        else:
            conn.execute("""
                INSERT INTO users (user_id, name, price_min, price_max, preferences, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, name, price_min, price_max, preferences, now, now))
    return get_user(user_id)


def add_liked_car(user_id: str, listing_id: int, note: str = "") -> None:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO liked_cars (user_id, listing_id, note, created_at) VALUES (?, ?, ?, ?)",
            (user_id, listing_id, note, now),
        )


def get_liked_cars(user_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM liked_cars WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def user_memory_summary(user_id: str) -> str:
    """Turns everything we know about a returning user into a short text
    block that gets injected into the LLM's system prompt (this IS the
    'long-term memory recall' the assessment asks for)."""
    user = get_user(user_id)
    if not user:
        return ""
    liked = get_liked_cars(user_id)
    lines = [f"Returning user profile for user_id={user_id}:"]
    if user["name"]:
        lines.append(f"- Name: {user['name']}")
    if user["price_min"] or user["price_max"]:
        lines.append(f"- Previously stated budget: {user['price_min']}–{user['price_max']} AED")
    if user["preferences"]:
        lines.append(f"- Stated preferences: {user['preferences']}")
    if liked:
        ids = ", ".join(str(c["listing_id"]) for c in liked[:5])
        lines.append(f"- Previously showed interest in listing_id(s): {ids}")
    return "\n".join(lines)
