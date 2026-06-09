import sqlite3
from datetime import datetime
from typing import Optional


class Database:
    def __init__(self, db_path: str = "bot.db"):
        self.db_path = db_path
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
            ]:
                try:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                except Exception:
                    pass
            conn.commit()

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
