"""Tests for auth.py — per-user account hashing, authentication, and
admin CRUD. All DB reads/writes go through the db_engine fixture's
in-memory SQLite database (tests/conftest.py); no test touches the real
project database.
"""
import pytest

import auth
from data.database import init_db


@pytest.fixture(autouse=True)
def _schema(db_engine, monkeypatch):
    """db_engine already creates every table via Base.metadata.create_all,
    but init_db()'s own seeding logic is production-only behaviour we don't
    want firing implicitly in every test — leave the users table empty and
    let each test create exactly the accounts it needs."""
    return db_engine


def test_password_is_hashed_never_stored_plaintext():
    auth.create_user("alice", "correct horse battery staple", "demo")
    engine = auth.get_engine()
    with engine.connect() as conn:
        from sqlalchemy import text
        row = conn.execute(text("SELECT password_hash FROM users WHERE username = 'alice'")).fetchone()
    assert row.password_hash != "correct horse battery staple"
    assert row.password_hash.startswith("$2")  # bcrypt hash prefix ($2a$/$2b$)


def test_authenticate_succeeds_with_correct_password():
    auth.create_user("bob", "hunter2", "admin")
    user = auth.authenticate("bob", "hunter2")
    assert user is not None
    assert user["username"] == "bob"
    assert user["role"] == "admin"


def test_authenticate_fails_with_wrong_password():
    auth.create_user("bob", "hunter2", "admin")
    assert auth.authenticate("bob", "wrong password") is None


def test_authenticate_fails_for_unknown_username():
    assert auth.authenticate("nobody", "irrelevant") is None


def test_create_user_rejects_duplicate_username():
    auth.create_user("carol", "pw12345", "demo")
    with pytest.raises(ValueError):
        auth.create_user("carol", "otherpw", "admin")


def test_create_user_rejects_invalid_role():
    with pytest.raises(ValueError):
        auth.create_user("dave", "pw12345", "superuser")


def test_create_user_rejects_empty_username_or_password():
    with pytest.raises(ValueError):
        auth.create_user("", "pw12345", "demo")
    with pytest.raises(ValueError):
        auth.create_user("erin", "", "demo")


def test_list_users_never_exposes_password_hash():
    auth.create_user("frank", "pw12345", "demo")
    users = auth.list_users()
    assert len(users) == 1
    assert "password_hash" not in users[0]
    assert users[0]["username"] == "frank"
    assert users[0]["role"] == "demo"


def test_update_user_role_changes_role():
    auth.create_user("grace", "pw12345", "demo")
    auth.create_user("admin2", "pw12345", "admin")
    user_id = next(u["id"] for u in auth.list_users() if u["username"] == "grace")
    auth.update_user_role(user_id, "admin")
    updated = next(u for u in auth.list_users() if u["id"] == user_id)
    assert updated["role"] == "admin"


def test_update_user_role_blocks_removing_last_admin():
    auth.create_user("sole_admin", "pw12345", "admin")
    user_id = auth.list_users()[0]["id"]
    with pytest.raises(ValueError):
        auth.update_user_role(user_id, "demo")


def test_delete_user_blocks_removing_last_admin():
    auth.create_user("sole_admin", "pw12345", "admin")
    user_id = auth.list_users()[0]["id"]
    with pytest.raises(ValueError):
        auth.delete_user(user_id)


def test_delete_user_allowed_when_another_admin_remains():
    auth.create_user("admin1", "pw12345", "admin")
    auth.create_user("admin2", "pw12345", "admin")
    user_id = next(u["id"] for u in auth.list_users() if u["username"] == "admin1")
    auth.delete_user(user_id)
    assert [u["username"] for u in auth.list_users()] == ["admin2"]


def test_update_user_password_changes_login():
    auth.create_user("hank", "old_password", "demo")
    user_id = auth.list_users()[0]["id"]
    auth.update_user_password(user_id, "new_password")
    assert auth.authenticate("hank", "old_password") is None
    assert auth.authenticate("hank", "new_password") is not None


def test_seed_default_admin_migrates_existing_bcrypt_hash(db_engine, monkeypatch):
    """init_db()'s seeding step must accept ADMIN_PASSWORD_HASH verbatim so
    the pre-existing Caddy basic_auth credential keeps working unchanged
    as the new admin account, without ever needing the real plaintext."""
    import bcrypt as _bcrypt

    legacy_plaintext = "the-real-existing-password"
    legacy_hash = _bcrypt.hashpw(legacy_plaintext.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", legacy_hash)

    init_db()

    user = auth.authenticate("admin", legacy_plaintext)
    assert user is not None
    assert user["role"] == "admin"


def test_seed_default_admin_is_noop_if_users_already_exist(db_engine):
    auth.create_user("preexisting", "pw12345", "admin")
    init_db()
    assert [u["username"] for u in auth.list_users()] == ["preexisting"]
