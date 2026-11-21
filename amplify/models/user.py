"""User model and authentication."""
import bcrypt
import sqlite3
from datetime import datetime
from amplify.models.db import get_db_connection


MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_BYTES = 72     # bcrypt only uses the first 72 bytes (and bcrypt 5 refuses anything longer)

_COLUMNS = "id, username, email, is_admin, is_active, must_change_password, last_login_at, created_at"


class User:
    def __init__(self, id: int, username: str, email: str = None,
                 is_admin: bool = False, is_active: bool = True,
                 last_login_at: str = None, created_at: str = None,
                 must_change_password: bool = False):
        self.id = id
        self.username = username
        self.email = email
        self.is_admin = is_admin
        self.is_active = is_active
        self.last_login_at = last_login_at
        self.created_at = created_at
        self.must_change_password = must_change_password     # set by an admin reset, cleared when they choose a new one

    def to_dict(self) -> dict:
        return {
            "id": self.id, "username": self.username, "email": self.email,
            "is_admin": self.is_admin, "is_active": self.is_active,
            "last_login_at": self.last_login_at, "created_at": self.created_at,
            "must_change_password": self.must_change_password,
        }

    @staticmethod
    def _from_row(row) -> "User":
        if not row:
            return None
        return User(
            id=row["id"], username=row["username"], email=row["email"],
            is_admin=bool(row["is_admin"]), is_active=bool(row["is_active"]),
            last_login_at=row["last_login_at"], created_at=row["created_at"],
            must_change_password=bool(row["must_change_password"]),
        )

    @staticmethod
    def password_problem(password) -> "str | None":
        """Why this can't be a password (a message fit for a 400), or None if it's fine."""
        if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
            return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
        if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
            return f"Password is too long (at most {MAX_PASSWORD_BYTES} bytes)"
        return None

    @staticmethod
    def hash_password(password: str) -> str:
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))

    @staticmethod
    def create(username: str, password: str, email: str = None, is_admin: bool = False) -> "User":
        conn = get_db_connection()
        cursor = conn.cursor()
        password_hash = User.hash_password(password)
        try:
            cursor.execute(
                "INSERT INTO users (username, email, password_hash, is_admin) VALUES (?, ?, ?, ?)",
                (username, email, password_hash, int(is_admin))
            )
            conn.commit()
            user_id = cursor.lastrowid
            return User(user_id, username, email, is_admin=is_admin)
        except sqlite3.IntegrityError:
            raise ValueError(f"Username '{username}' already exists")
        finally:
            conn.close()

    @staticmethod
    def get_by_username(username: str) -> "User":
        conn = get_db_connection()
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()
        return User._from_row(row)

    @staticmethod
    def get_by_id(user_id: int) -> "User":
        conn = get_db_connection()
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        conn.close()
        return User._from_row(row)

    @staticmethod
    def get_all() -> list["User"]:
        conn = get_db_connection()
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM users ORDER BY id"
        ).fetchall()
        conn.close()
        return [User._from_row(r) for r in rows]

    @staticmethod
    def verify_credentials(username: str, password: str) -> "User":
        conn = get_db_connection()
        row = conn.execute(
            f"SELECT {_COLUMNS}, password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()
        if not row:
            return None
        if not row["is_active"]:
            return None
        if not User.verify_password(password, row["password_hash"]):
            return None
        return User._from_row(row)

    @staticmethod
    def update_last_login(user_id: int) -> None:
        conn = get_db_connection()
        conn.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def set_active(user_id: int, active: bool) -> None:
        conn = get_db_connection()
        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (int(active), user_id))
        conn.commit()
        conn.close()

    @staticmethod
    def set_admin(user_id: int, is_admin: bool) -> None:
        conn = get_db_connection()
        conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(is_admin), user_id))
        conn.commit()
        conn.close()

    @staticmethod
    def reset_password(user_id: int, new_password: str, must_change: bool = False) -> None:
        """Set a password for someone else (an admin reset). With `must_change` the user has to choose their
        own before they can use anything else."""
        conn = get_db_connection()
        hashed = User.hash_password(new_password)
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = ?, updated_at = CURRENT_TIMESTAMP "
                     "WHERE id = ?", (hashed, int(must_change), user_id))
        conn.commit()
        conn.close()

    @staticmethod
    def change_password(user_id: int, new_password: str) -> None:
        """The user chose a new password themselves: store it and lift any "must change" requirement."""
        conn = get_db_connection()
        hashed = User.hash_password(new_password)
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = CURRENT_TIMESTAMP "
                     "WHERE id = ?", (hashed, user_id))
        conn.commit()
        conn.close()

    @staticmethod
    def delete(user_id: int) -> None:
        conn = get_db_connection()
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def count() -> int:
        conn = get_db_connection()
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        conn.close()
        return row[0]


class ActivityLog:
    @staticmethod
    def record(user_id: int, action: str, detail: str = None, ip: str = None) -> None:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO activity_log (user_id, action, detail, ip_address) VALUES (?, ?, ?, ?)",
            (user_id, action, detail, ip)
        )
        conn.commit()
        conn.close()

    @staticmethod
    def recent(limit: int = 100) -> list[dict]:
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT a.id, a.user_id, u.username, a.action, a.detail, a.ip_address, a.created_at "
            "FROM activity_log a JOIN users u ON a.user_id = u.id "
            "ORDER BY a.created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def for_user(user_id: int, limit: int = 50) -> list[dict]:
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT id, action, detail, ip_address, created_at "
            "FROM activity_log WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def metrics() -> dict:
        conn = get_db_connection()
        total = conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
        today = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE DATE(created_at) = DATE('now')"
        ).fetchone()[0]
        by_action = conn.execute(
            "SELECT action, COUNT(*) as cnt FROM activity_log GROUP BY action ORDER BY cnt DESC"
        ).fetchall()
        active_users_today = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM activity_log WHERE DATE(created_at) = DATE('now')"
        ).fetchone()[0]
        active_users_week = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM activity_log "
            "WHERE created_at >= DATETIME('now', '-7 days')"
        ).fetchone()[0]
        daily_counts = conn.execute(
            "SELECT DATE(created_at) as day, COUNT(*) as cnt "
            "FROM activity_log WHERE created_at >= DATETIME('now', '-30 days') "
            "GROUP BY day ORDER BY day"
        ).fetchall()
        conn.close()
        return {
            "total_events": total,
            "events_today": today,
            "active_users_today": active_users_today,
            "active_users_week": active_users_week,
            "by_action": {r["action"]: r["cnt"] for r in by_action},
            "daily_counts": [{"date": r["day"], "count": r["cnt"]} for r in daily_counts],
        }
