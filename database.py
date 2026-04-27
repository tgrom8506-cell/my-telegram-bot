import sqlite3
from datetime import datetime, timedelta

DB_NAME = "subscriptions.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY,
                  subscription_until TIMESTAMP,
                  answer_mode INTEGER DEFAULT 1)''')
    conn.commit()
    conn.close()

def get_subscription_until(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT subscription_until FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        return datetime.fromisoformat(row[0])
    return None

def set_subscription(user_id, until_date: datetime):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, subscription_until, answer_mode) VALUES (?, ?, COALESCE((SELECT answer_mode FROM users WHERE user_id = ?), 1))",
              (user_id, until_date.isoformat(), user_id))
    conn.commit()
    conn.close()

def is_active(user_id) -> bool:
    until = get_subscription_until(user_id)
    return until and until > datetime.now()

def get_answer_mode(user_id) -> int:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT answer_mode FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 1   # по умолчанию подробный режим (1)

def set_answer_mode(user_id, mode: int):  # 0 = короткий, 1 = подробный
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, subscription_until, answer_mode) VALUES (?, NULL, ?)", (user_id, mode))
    c.execute("UPDATE users SET answer_mode = ? WHERE user_id = ?", (mode, user_id))
    conn.commit()
    conn.close()
