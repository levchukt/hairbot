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
                    pay_method  TEXT
                )
            """)
            conn.commit()

    def add_user(self, user_id: int, username: Optional[str], first_name: Optional[str]):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO users (user_id, username, first_name, joined_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, username, first_name, datetime.utcnow().isoformat()))
            conn.commit()

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

    def get_stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            paid = conn.execute("SELECT COUNT(*) FROM users WHERE paid = 1").fetchone()[0]
        conversion = (paid / total * 100) if total else 0
        return {"total_users": total, "paid_users": paid, "conversion": conversion}
