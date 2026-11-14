"""User model and authentication."""
import bcrypt
import sqlite3
from datetime import datetime
from amplify.models.db import get_db_connection

class User:
    def __init__(self, id: int, username: str, email: str = None):
        self.id = id
        self.username = username
        self.email = email

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt."""
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """Verify a password against its hash."""
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))

    @staticmethod
    def create(username: str, password: str, email: str = None) -> "User":
        """Create a new user and store in database."""
        conn = get_db_connection()
        cursor = conn.cursor()

        password_hash = User.hash_password(password)

        try:
            cursor.execute(
                "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                (username, email, password_hash)
            )
            conn.commit()
            user_id = cursor.lastrowid
            return User(user_id, username, email)
        except sqlite3.IntegrityError:
            raise ValueError(f"Username '{username}' already exists")
        finally:
            conn.close()

    @staticmethod
    def get_by_username(username: str) -> "User":
        """Fetch user by username."""
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id, username, email FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return User(row[0], row[1], row[2])

    @staticmethod
    def get_by_id(user_id: int) -> "User":
        """Fetch user by ID."""
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id, username, email FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return User(row[0], row[1], row[2])

    @staticmethod
    def verify_credentials(username: str, password: str) -> "User":
        """Verify username and password; return User if valid, None otherwise."""
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id, username, email, password_hash FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        user_id, username_from_db, email, password_hash = row

        if not User.verify_password(password, password_hash):
            return None

        return User(user_id, username_from_db, email)
