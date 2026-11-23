"""Database setup and initialization."""
import sqlite3
from pathlib import Path
from amplify_server.config import settings

def get_db_connection():
    """Get SQLite database connection."""
    db_path = settings.DATABASE_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    """Initialize database schema."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            last_login_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            ip_address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log(user_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_activity_action ON activity_log(action)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at)
    """)

    _migrate(cursor)

    conn.commit()
    conn.close()

def _migrate(cursor):
    """Add columns that may be missing from older databases."""
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(users)").fetchall()}
    migrations = {
        "is_admin":      "ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0",
        "is_active":     "ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1",
        "last_login_at": "ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP",
        "must_change_password": "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0",
    }
    for col, ddl in migrations.items():
        if col not in existing:
            cursor.execute(ddl)

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
