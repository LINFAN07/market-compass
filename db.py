# 訂閱者資料庫管理
# 使用 SQLite 儲存訂閱者 email 與上次評分等級

import sqlite3
from datetime import datetime

DB_PATH = r"c:\agent\market-compass\subscribers.db"


def _conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    """建立資料表（初次執行時呼叫）"""
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                email TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
        """)
        # 記錄上次寄出的評分等級，防止重複寄信
        con.execute("""
            CREATE TABLE IF NOT EXISTS state (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)


def add_subscriber(email: str) -> bool:
    """新增訂閱者，已存在則回傳 False"""
    try:
        with _conn() as con:
            con.execute(
                "INSERT INTO subscribers (email, created_at) VALUES (?, ?)",
                (email.strip().lower(), datetime.now().isoformat()),
            )
        return True
    except sqlite3.IntegrityError:
        return False  # email 已存在


def remove_subscriber(email: str) -> bool:
    """移除訂閱者，不存在則回傳 False"""
    with _conn() as con:
        cur = con.execute(
            "DELETE FROM subscribers WHERE email = ?",
            (email.strip().lower(),),
        )
    return cur.rowcount > 0


def list_subscribers() -> list[str]:
    """取得所有訂閱者 email 清單"""
    with _conn() as con:
        rows = con.execute("SELECT email FROM subscribers").fetchall()
    return [r[0] for r in rows]


def get_last_label() -> str | None:
    """取得上次記錄的評分等級"""
    with _conn() as con:
        row = con.execute(
            "SELECT value FROM state WHERE key = 'last_label'"
        ).fetchone()
    return row[0] if row else None


def set_last_label(label: str):
    """更新評分等級記錄"""
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO state (key, value) VALUES ('last_label', ?)",
            (label,),
        )
