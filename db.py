import os
import sqlite3
from datetime import datetime
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "bot.db")


class Database:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DB_PATH
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id     INTEGER PRIMARY KEY,
                    username    TEXT,
                    first_name  TEXT,
                    joined_at   TEXT,
                    paid        INTEGER DEFAULT 0,
                    paid_at     TEXT,
                    pay_method  TEXT,
                    source      TEXT DEFAULT 'direct'
                )
            """)
            # Add columns if upgrading from old db
            for col, definition in [
                ("source", "TEXT DEFAULT 'direct'"),
                ("offer_sent", "INTEGER DEFAULT 0"),
                ("stars_charge_id", "TEXT"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                except Exception:
                    pass
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    user_id    INTEGER NOT NULL,
                    event      TEXT NOT NULL,
                    source     TEXT DEFAULT 'direct',
                    created_at TEXT,
                    UNIQUE(user_id, event)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_event ON events(event)")
            conn.commit()

    # ─────────────────────────────────────────
    #  EVENTS / ВОРОНКА
    # ─────────────────────────────────────────

    def log_event(self, user_id: int, event: str):
        """Фіксує крок воронки. Один запис на користувача+подію."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT source FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            source = row[0] if row and row[0] else "direct"
            conn.execute("""
                INSERT OR IGNORE INTO events (user_id, event, source, created_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, event, source, datetime.utcnow().isoformat()))
            conn.commit()

    def event_counts(self, source: Optional[str] = None) -> dict:
        with self._conn() as conn:
            if source:
                rows = conn.execute(
                    "SELECT event, COUNT(*) FROM events WHERE source = ? GROUP BY event",
                    (source,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT event, COUNT(*) FROM events GROUP BY event"
                ).fetchall()
        return {r[0]: r[1] for r in rows}

    def stuck_users(self) -> dict:
        """Скільки дійшло до етапу, але не пішло далі."""
        with self._conn() as conn:
            def q(sql):
                return conn.execute(sql).fetchone()[0]

            return {
                "guide_not_read": q("""
                    SELECT COUNT(*) FROM events e
                    WHERE e.event = 'guide_sent'
                      AND NOT EXISTS (SELECT 1 FROM events x WHERE x.user_id = e.user_id AND x.event = 'guide_read')
                """),
                "offer_no_click": q("""
                    SELECT COUNT(*) FROM events e
                    WHERE e.event = 'offer_sent'
                      AND NOT EXISTS (SELECT 1 FROM events x WHERE x.user_id = e.user_id AND x.event = 'buy_click')
                """),
                "buy_no_method": q("""
                    SELECT COUNT(*) FROM events e
                    WHERE e.event = 'buy_click'
                      AND NOT EXISTS (
                          SELECT 1 FROM events x WHERE x.user_id = e.user_id
                          AND x.event IN ('pay_card_click','pay_crypto_click')
                      )
                """),
                "method_no_pay": q("""
                    SELECT COUNT(DISTINCT e.user_id) FROM events e
                    WHERE e.event IN ('pay_card_click','pay_crypto_click')
                      AND NOT EXISTS (SELECT 1 FROM events x WHERE x.user_id = e.user_id AND x.event = 'paid')
                """),
                "buy_no_pay": q("""
                    SELECT COUNT(*) FROM events e
                    WHERE e.event = 'buy_click'
                      AND NOT EXISTS (SELECT 1 FROM events x WHERE x.user_id = e.user_id AND x.event = 'paid')
                """),
            }

    def add_user(self, user_id: int, username: Optional[str], first_name: Optional[str], source: str = "direct"):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO users (user_id, username, first_name, joined_at, source)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, username, first_name, datetime.utcnow().isoformat(), source))
            conn.commit()

    def user_exists(self, user_id: int) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return row is not None

    def is_paid(self, user_id: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT paid FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return bool(row and row[0])

    def mark_paid(self, user_id: int, method: str = "wayforpay"):
        with self._conn() as conn:
            conn.execute("""
                UPDATE users SET paid = 1, paid_at = ?, pay_method = ?
                WHERE user_id = ?
            """, (datetime.utcnow().isoformat(), method, user_id))
            conn.commit()
        self.log_event(user_id, "paid")

    def hot_leads(self) -> list:
        """Натиснули «Купить», але не оплатили. Найтепліші люди в базі."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT u.user_id, u.username, u.first_name, u.source, e.created_at
                FROM events e
                JOIN users u ON u.user_id = e.user_id
                WHERE e.event = 'buy_click' AND u.paid = 0
                ORDER BY e.created_at DESC
            """).fetchall()
        return [
            {"user_id": r[0], "username": r[1], "first_name": r[2],
             "source": r[3], "at": r[4]}
            for r in rows
        ]

    def get_source(self, user_id: int) -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT source FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return (row[0] if row and row[0] else "direct")

    # ─────────────────────────────────────────
    #  TELEGRAM STARS
    # ─────────────────────────────────────────

    def save_stars_charge(self, user_id: int, charge_id: str):
        """charge_id потрібен для refundStarPayment. Без нього повернення неможливе."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET stars_charge_id = ? WHERE user_id = ?",
                (charge_id, user_id)
            )
            conn.commit()

    def get_stars_charge(self, user_id: int) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT stars_charge_id FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return row[0] if row and row[0] else None

    def unmark_paid(self, user_id: int):
        """Знімає доступ після повернення коштів."""
        with self._conn() as conn:
            conn.execute("""
                UPDATE users SET paid = 0, pay_method = 'refunded'
                WHERE user_id = ?
            """, (user_id,))
            conn.execute(
                "DELETE FROM events WHERE user_id = ? AND event = 'paid'", (user_id,)
            )
            conn.commit()

    def mark_offer_sent(self, user_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE users SET offer_sent = 1 WHERE user_id = ?", (user_id,))
            conn.commit()

    def has_offer_sent(self, user_id: int) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT offer_sent FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return bool(row and row[0])

    def get_stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            paid = conn.execute("SELECT COUNT(*) FROM users WHERE paid = 1").fetchone()[0]
            # Per-source stats
            sources = conn.execute("""
                SELECT source, COUNT(*) as total, SUM(paid) as paid
                FROM users GROUP BY source ORDER BY total DESC
            """).fetchall()
        conversion = (paid / total * 100) if total else 0
        return {
            "total_users": total,
            "paid_users": paid,
            "conversion": conversion,
            "sources": [{"source": r[0], "total": r[1], "paid": r[2] or 0} for r in sources]
        }
