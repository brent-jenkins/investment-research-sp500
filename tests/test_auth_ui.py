"""UI-entry-point tests for the per-user login flow and role-based page
gating added to main.py (see auth.py) -- replaces the previous single
shared Caddy basic-auth credential. Uses Streamlit's AppTest harness the
same way tests/test_main_ui.py does; see that file's own module docstring
for why every module-level side effect (scheduler, DB engine) must be
patched before AppTest.from_file("main.py") is constructed.
"""
import pytest
from streamlit.testing.v1 import AppTest

import auth

_ADMIN_ONLY_PAGES = ["instruments", "backtest", "status", "users"]


def _build_app(db_engine, monkeypatch, mocker, *, auth_user=None, page=None):
    mocker.patch("data.scheduler.start_scheduler")
    monkeypatch.setattr("main.get_engine", lambda: db_engine)
    monkeypatch.setattr("data.ingestion.get_all_watchlist_entries", lambda config: [])

    at = AppTest.from_file("main.py", default_timeout=30)
    if auth_user is not None:
        at.session_state["auth_user"] = auth_user
    if page is not None:
        at.session_state["page"] = page
    return at


@pytest.fixture
def app_factory(db_engine, monkeypatch, mocker):
    """Returns a builder so each test can choose its own auth_user/page,
    while sharing the same DB engine and scheduler/watchlist patches."""
    def _make(auth_user=None, page=None):
        return _build_app(db_engine, monkeypatch, mocker, auth_user=auth_user, page=page)
    return _make


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------

def test_unauthenticated_session_shows_login_form_not_app_content(app_factory):
    at = app_factory().run()
    assert not at.exception
    assert at.text_input(key="login_username")
    assert at.text_input(key="login_password")
    # No sidebar navigation / page content should render pre-login.
    assert not at.radio
    all_text = " ".join(m.value for m in at.markdown)
    assert "Open Positions" not in all_text


def test_login_with_wrong_password_shows_error_and_stays_unauthenticated(db_engine, app_factory):
    auth.create_user("carol", "correct-password", "admin")
    at = app_factory().run()

    at.text_input(key="login_username").set_value("carol")
    at.text_input(key="login_password").set_value("wrong-password")
    at.button(key="login_submit").click().run()

    assert not at.exception
    assert "auth_user" not in at.session_state
    assert any("invalid username or password" in e.value.lower() for e in at.error)


def test_login_with_unknown_username_shows_error(db_engine, app_factory):
    at = app_factory().run()

    at.text_input(key="login_username").set_value("nobody")
    at.text_input(key="login_password").set_value("whatever")
    at.button(key="login_submit").click().run()

    assert not at.exception
    assert "auth_user" not in at.session_state
    assert any("invalid username or password" in e.value.lower() for e in at.error)


def test_login_with_correct_credentials_authenticates_and_shows_dashboard(db_engine, app_factory):
    auth.create_user("dave", "hunter2", "admin")
    at = app_factory().run()

    at.text_input(key="login_username").set_value("dave")
    at.text_input(key="login_password").set_value("hunter2")
    at.button(key="login_submit").click().run()

    assert not at.exception
    assert at.session_state["auth_user"]["username"] == "dave"
    assert at.session_state["auth_user"]["role"] == "admin"
    assert any("Dashboard" in m.value for m in at.markdown)


def test_logout_clears_session_and_returns_to_login(db_engine, app_factory):
    auth.create_user("erin", "pw12345", "admin")
    user = auth.authenticate("erin", "pw12345")
    at = app_factory(auth_user=user, page="dashboard").run()

    logout_btn = next(b for b in at.button if b.label == "Log out")
    logout_btn.click().run()

    assert not at.exception
    assert "auth_user" not in at.session_state
    assert at.text_input(key="login_username")


# ---------------------------------------------------------------------------
# Role-based page gating
# ---------------------------------------------------------------------------

_DEMO_USER = {"id": 1, "username": "demo_viewer", "role": "demo"}
_ADMIN_USER = {"id": 2, "username": "admin_user", "role": "admin"}


def test_demo_role_can_access_dashboard(app_factory):
    at = app_factory(auth_user=_DEMO_USER, page="dashboard").run()
    assert not at.exception
    assert any("Dashboard" in m.value for m in at.markdown)
    assert not any("isn't available in your view" in i.value.lower() for i in at.info)


@pytest.mark.parametrize("page", _ADMIN_ONLY_PAGES)
def test_demo_role_is_blocked_from_admin_only_pages(app_factory, page):
    at = app_factory(auth_user=_DEMO_USER, page=page).run()
    assert not at.exception
    assert any("isn't available in your view" in i.value.lower() for i in at.info)
    # Confirm the actual restricted page body never rendered -- the denied
    # notice deliberately reuses the page's own title as a heading (so the
    # user can see what they tried to reach), so check for each page's
    # actual BODY content instead of its title, which would trivially
    # false-positive against the denied page's own heading text.
    all_text = " ".join(m.value for m in at.markdown)
    assert "Scheduler Health" not in all_text  # Status page body
    assert "Add a user" not in all_text        # Users page body
    assert not any("Run Full Validated Battery" in b.label for b in at.button)  # Backtest page body
    assert not at.dataframe  # Instruments/Status/Users tables never rendered


def test_demo_role_sidebar_only_offers_dashboard(app_factory):
    at = app_factory(auth_user=_DEMO_USER, page="dashboard").run()
    assert not at.exception
    nav_radio = next(r for r in at.radio if "Dashboard" in r.options)
    assert list(nav_radio.options) == ["Dashboard"]


@pytest.mark.parametrize("page", _ADMIN_ONLY_PAGES)
def test_admin_role_can_access_every_page(app_factory, page):
    at = app_factory(auth_user=_ADMIN_USER, page=page).run()
    assert not at.exception
    assert not any("isn't available in your view" in i.value.lower() for i in at.info)


def test_admin_role_sidebar_offers_every_page(app_factory):
    at = app_factory(auth_user=_ADMIN_USER, page="dashboard").run()
    assert not at.exception
    nav_radio = next(r for r in at.radio if "Dashboard" in r.options)
    assert set(nav_radio.options) == {"Dashboard", "Instruments", "Backtest", "Status", "Users"}


def test_access_denied_page_has_a_way_back_to_dashboard(app_factory):
    at = app_factory(auth_user=_DEMO_USER, page="status").run()
    assert not at.exception
    return_btn = next(b for b in at.button if b.label == "Return to Dashboard")
    return_btn.click().run()
    assert at.session_state["page"] == "dashboard"


# ---------------------------------------------------------------------------
# Users / Administration page -- admin CRUD via the real UI
# ---------------------------------------------------------------------------

def test_users_page_lists_existing_accounts(db_engine, app_factory):
    auth.create_user("frank", "pw12345", "demo")
    at = app_factory(auth_user=_ADMIN_USER, page="users").run()

    assert not at.exception
    df = at.dataframe[0].value
    assert "frank" in set(df["Username"])


def test_users_page_add_user_creates_a_working_login(db_engine, app_factory):
    at = app_factory(auth_user=_ADMIN_USER, page="users").run()

    at.text_input(key="add_user_username").set_value("grace")
    at.text_input(key="add_user_password").set_value("new-password-1")
    at.selectbox(key="add_user_role").set_value("demo")
    at.button(key="add_user_submit").click().run()

    assert not at.exception
    assert auth.authenticate("grace", "new-password-1") is not None
    assert auth.authenticate("grace", "new-password-1")["role"] == "demo"


def test_users_page_rejects_duplicate_username_via_ui(db_engine, app_factory):
    auth.create_user("henry", "pw12345", "demo")
    at = app_factory(auth_user=_ADMIN_USER, page="users").run()

    at.text_input(key="add_user_username").set_value("henry")
    at.text_input(key="add_user_password").set_value("another-password")
    at.selectbox(key="add_user_role").set_value("demo")
    at.button(key="add_user_submit").click().run()

    assert not at.exception
    assert any("already exists" in e.value.lower() for e in at.error)


def test_users_page_update_role_via_ui(db_engine, app_factory):
    auth.create_user("iris", "pw12345", "demo")
    at = app_factory(auth_user=_ADMIN_USER, page="users").run()

    at.selectbox(key="users_edit_select").set_value("iris (demo)")
    at.selectbox(key="users_role_select").set_value("admin")
    at.button(key="users_update_role").click().run()

    assert not at.exception
    updated = next(u for u in auth.list_users() if u["username"] == "iris")
    assert updated["role"] == "admin"


def test_users_page_reset_password_via_ui(db_engine, app_factory):
    auth.create_user("jack", "old-password", "demo")
    at = app_factory(auth_user=_ADMIN_USER, page="users").run()

    at.selectbox(key="users_edit_select").set_value("jack (demo)")
    at.text_input(key="users_reset_pw").set_value("brand-new-password")
    at.button(key="users_do_reset_pw").click().run()

    assert not at.exception
    assert auth.authenticate("jack", "old-password") is None
    assert auth.authenticate("jack", "brand-new-password") is not None


def test_users_page_delete_user_via_ui(db_engine, app_factory):
    auth.create_user("kate", "pw12345", "demo")
    at = app_factory(auth_user=_ADMIN_USER, page="users").run()

    at.selectbox(key="users_edit_select").set_value("kate (demo)")
    at.button(key="users_delete").click().run()

    assert not at.exception
    assert "kate" not in {u["username"] for u in auth.list_users()}


def test_users_page_cannot_delete_own_account(db_engine, app_factory):
    """A safety rail distinct from the last-admin guard in auth.py itself
    -- prevents an admin from locking themselves out of the current
    session via the UI, even if another admin exists."""
    auth.create_user("admin_user", "pw12345", "admin")
    auth.create_user("other_admin", "pw12345", "admin")
    me = auth.authenticate("admin_user", "pw12345")
    at = app_factory(auth_user=me, page="users").run()

    at.selectbox(key="users_edit_select").set_value("admin_user (admin)")
    at.button(key="users_delete").click().run()

    assert not at.exception
    assert any("cannot delete your own account" in e.value.lower() for e in at.error)
    assert "admin_user" in {u["username"] for u in auth.list_users()}
