"""Per-user account authentication and management.

Replaces the previous single shared Caddy basic-auth credential (see
CLAUDE.md's per-user-accounts section) with real per-user records: a
username, a bcrypt password hash (never plaintext), and a role
("admin" / "demo") that main.py uses to gate page access.

Every read/write of the `users` table goes through this module — nothing
else should touch data.database.User directly — so hashing and role
validation apply uniformly regardless of caller (the login form, the
Users admin page, a future script).
"""

from datetime import datetime
from typing import Optional

import bcrypt
from sqlalchemy import text

from data.database import get_engine

VALID_ROLES = ("admin", "demo")


def hash_password(password: str) -> str:
    """Return a bcrypt hash of *password*. Never store or log the plaintext."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Return True if *password* matches *password_hash* (a bcrypt hash)."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed/empty hash — never treat as a match.
        return False


def authenticate(username: str, password: str) -> Optional[dict]:
    """Verify *username*/*password* against the users table.

    Returns {"id", "username", "role"} on success, None on any failure
    (unknown username or wrong password — deliberately not distinguished
    to the caller, so a login form can't be used to enumerate usernames).
    """
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, username, password_hash, role FROM users WHERE username = :u"),
            {"u": username},
        ).fetchone()
    if row is None:
        return None
    if not verify_password(password, row.password_hash):
        return None
    return {"id": row.id, "username": row.username, "role": row.role}


def list_users() -> list[dict]:
    """Return every user account as {"id", "username", "role", "created_at"}
    — never includes password_hash."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, username, role, created_at FROM users ORDER BY username")
        ).fetchall()
    return [
        {"id": r.id, "username": r.username, "role": r.role, "created_at": r.created_at}
        for r in rows
    ]


def _admin_count(conn, exclude_id: "int | None" = None) -> int:
    if exclude_id is None:
        return conn.execute(
            text("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        ).scalar()
    return conn.execute(
        text("SELECT COUNT(*) FROM users WHERE role = 'admin' AND id != :id"),
        {"id": exclude_id},
    ).scalar()


def create_user(username: str, password: str, role: str) -> None:
    """Create a new user account. Raises ValueError on a duplicate
    username, an invalid role, or an empty username/password."""
    username = username.strip()
    if not username:
        raise ValueError("Username cannot be empty.")
    if not password:
        raise ValueError("Password cannot be empty.")
    if role not in VALID_ROLES:
        raise ValueError(f"Role must be one of {VALID_ROLES}.")

    engine = get_engine()
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM users WHERE username = :u"), {"u": username}
        ).first()
    if exists:
        raise ValueError(f"A user named '{username}' already exists.")

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (username, password_hash, role, created_at) "
                "VALUES (:u, :h, :r, :t)"
            ),
            {"u": username, "h": hash_password(password), "r": role, "t": datetime.utcnow()},
        )


def update_user_role(user_id: int, role: str) -> None:
    """Change a user's role. Raises ValueError for an invalid role or if
    this would leave the system with no admin accounts at all."""
    if role not in VALID_ROLES:
        raise ValueError(f"Role must be one of {VALID_ROLES}.")

    engine = get_engine()
    with engine.begin() as conn:
        current = conn.execute(
            text("SELECT role FROM users WHERE id = :id"), {"id": user_id}
        ).fetchone()
        if current is None:
            raise ValueError("User not found.")
        if current.role == "admin" and role != "admin" and _admin_count(conn, exclude_id=user_id) == 0:
            raise ValueError("Cannot remove the last remaining admin account.")
        conn.execute(text("UPDATE users SET role = :r WHERE id = :id"), {"r": role, "id": user_id})


def update_user_password(user_id: int, new_password: str) -> None:
    """Reset a user's password. Raises ValueError for an empty password."""
    if not new_password:
        raise ValueError("Password cannot be empty.")
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("UPDATE users SET password_hash = :h WHERE id = :id"),
            {"h": hash_password(new_password), "id": user_id},
        )
        if result.rowcount == 0:
            raise ValueError("User not found.")


def delete_user(user_id: int) -> None:
    """Delete a user account. Raises ValueError if this would leave the
    system with no admin accounts at all."""
    engine = get_engine()
    with engine.begin() as conn:
        current = conn.execute(
            text("SELECT role FROM users WHERE id = :id"), {"id": user_id}
        ).fetchone()
        if current is None:
            raise ValueError("User not found.")
        if current.role == "admin" and _admin_count(conn, exclude_id=user_id) == 0:
            raise ValueError("Cannot remove the last remaining admin account.")
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
