import argparse
import sqlite3
import time
from pathlib import Path

from werkzeug.security import generate_password_hash


def get_db_path() -> Path:
    return Path(__file__).resolve().parent / "database" / "registrations.db"


def ensure_users_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            role TEXT NOT NULL DEFAULT 'admin',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
        """
    )


def upsert_user(
    username: str,
    password: str,
    display_name: str,
    role: str,
    is_active: int,
    retries: int,
    retry_delay: float,
) -> None:
    db_path = get_db_path()
    password_hash = generate_password_hash(password)
    username = username.strip().lower()
    display_name = (display_name or "").strip() or username
    role = (role or "admin").strip() or "admin"
    is_active = 1 if int(is_active) else 0

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                ensure_users_table(conn)
                row = conn.execute(
                    "SELECT id FROM users WHERE username = ?",
                    (username,),
                ).fetchone()
                if row:
                    conn.execute(
                        """
                        UPDATE users
                        SET password_hash = ?, display_name = ?, role = ?, is_active = ?
                        WHERE username = ?
                        """,
                        (password_hash, display_name, role, is_active, username),
                    )
                    action = "updated"
                else:
                    conn.execute(
                        """
                        INSERT INTO users (username, password_hash, display_name, role, is_active)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (username, password_hash, display_name, role, is_active),
                    )
                    action = "created"
                conn.commit()
                print(
                    f"User {action}: username={username}, display_name={display_name}, "
                    f"role={role}, is_active={is_active}"
                )
                return
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "locked" in str(exc).lower() and attempt < retries:
                time.sleep(retry_delay)
                continue
            raise

    raise RuntimeError(f"Could not write credentials after {retries} attempts: {last_error}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or update a user credential in SQLite.")
    parser.add_argument("--username", required=True, help="Username (stored lowercase)")
    parser.add_argument("--password", required=True, help="Plain password to hash and store")
    parser.add_argument("--display-name", default="", help="Display name")
    parser.add_argument("--role", default="admin", help="Role (default: admin)")
    parser.add_argument("--is-active", type=int, default=1, help="1 active, 0 inactive")
    parser.add_argument("--retries", type=int, default=20, help="Retry count when DB is locked")
    parser.add_argument("--retry-delay", type=float, default=0.25, help="Retry delay seconds")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    upsert_user(
        username=args.username,
        password=args.password,
        display_name=args.display_name,
        role=args.role,
        is_active=args.is_active,
        retries=max(1, int(args.retries)),
        retry_delay=max(0.05, float(args.retry_delay)),
    )


# アカウント作成方法
# --username : アカウント名
# --password : パスワード
# --display-name : 表示名
# python create_credentials.py --username user1 --password 123456 --display-name "User One"
