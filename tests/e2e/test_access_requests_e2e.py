"""Real public/admin pages and CSRF; only Plex's external boundary is simulated."""

import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cachelib import FileSystemCache
from playwright.sync_api import expect
from werkzeug.serving import make_server

from app import create_app
from app.blueprints.access_requests.routes import request_limiter
from app.config import BaseConfig
from app.extensions import db
from app.models import (
    AccessRequest,
    AdminAccount,
    Library,
    MediaServer,
    Settings,
    User,
    WizardStep,
)


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    directory = tmp_path_factory.mktemp("public-requests")

    class Config(BaseConfig):
        TESTING = True
        WTF_CSRF_ENABLED = True
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{directory}/requests.db"
        SESSION_CACHELIB = FileSystemCache(str(directory / "sessions"))

    application = create_app(Config)
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.drop_all()


@pytest.fixture
def request_site(app, monkeypatch):
    request_limiter.reset()
    with app.app_context():
        db.session.add(Settings(key="public_access_requests", value="true"))
        admin = AdminAccount(username="reviewer")
        admin.set_password("test-review-password")
        db.session.add(admin)
        server = MediaServer(
            name="Example Plex",
            server_type="plex",
            url="http://plex.example.test",
            api_key="test-owner-key",
            verified=True,
        )
        db.session.add(server)
        db.session.flush()
        db.session.add(Library(name="Movies", external_id="101", server_id=server.id))
        db.session.add(
            WizardStep(
                server_type="plex",
                category="post_invite",
                position=0,
                title="Welcome",
                markdown="# Welcome to Plex\nYour access is ready.",
            )
        )
        db.session.commit()
    resources = []
    account = SimpleNamespace(
        id=42,
        username="requester",
        email="requester@example.test",
        confirmed=True,
        guest=False,
        resources=Mock(side_effect=lambda: resources.copy()),
        acceptInvite=Mock(
            side_effect=lambda _owner: resources.append(
                SimpleNamespace(clientIdentifier="test-machine")
            )
        ),
    )
    other = SimpleNamespace(
        id=99, username="other", email="other@example.test", confirmed=True, guest=False
    )
    monkeypatch.setattr(
        "app.services.access_requests.MyPlexAccount",
        lambda token, **_kwargs: other if token == "wrong-account" else account,
    )
    plex = Mock()
    plex.admin.users.return_value = []
    plex.admin.pendingInvites.return_value = []
    plex.admin.username = "owner"
    plex.server.machineIdentifier = "test-machine"
    plex.server.library.sections.return_value = [SimpleNamespace(title="Movies")]
    plex._get_all_library_global_ids.return_value = {"Movies": 101}

    def create_user(data):
        user = User(**data)
        db.session.add(user)
        return user

    plex._create_user_with_identity_linking.side_effect = create_user
    monkeypatch.setattr("app.services.media.plex.PlexClient", Mock(return_value=plex))
    monkeypatch.setattr(
        "app.services.media.plex.get_client_for_media_server", Mock(return_value=plex)
    )
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", plex=plex)
    server.shutdown()
    thread.join(timeout=5)
    with app.app_context():
        # All fixture data is disposable; clear associations before parent rows.
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()


def plex_sign_in(page, token):
    def pin(route):
        payload = (
            {"id": 1, "code": "test-pin"}
            if route.request.method == "POST"
            else {"authToken": token}
        )
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(payload)
        )

    def login(route):
        route.fulfill(
            status=200,
            content_type="text/html",
            body="""<a id="confirm">Confirm Plex sign-in</a><script>
        document.getElementById('confirm').href = new URLSearchParams(location.hash.split('?')[1]).get('forwardUrl');
        </script>""",
        )

    page.route("https://plex.tv/api/v2/pins**", pin)
    page.route("https://app.plex.tv/auth/**", login)


@pytest.mark.parametrize("width", [1280, 390])
def test_request_review_and_bound_onboarding(
    app, browser, request_site, tmp_path, width
):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    plex_sign_in(page, "test-requester-token")
    page.goto(request_site.url + "/request-access")
    expect(page.get_by_role("heading", name="Request Plex access")).to_be_visible()
    page.screenshot(path=str(tmp_path / f"public-{width}.png"), full_page=True)
    page.get_by_label("Plex email or username").fill("requester")
    page.get_by_label("Who are you?").fill("A friend")
    page.get_by_role("button", name="Sign in with Plex and submit").click()
    page.get_by_role("link", name="Confirm Plex sign-in").click()
    expect(page.get_by_role("heading", name="Waiting for review")).to_be_visible()
    expect(
        page.get_by_role("link", name="Save this private status link")
    ).to_be_visible()
    private_link = page.get_by_role(
        "link", name="Save this private status link"
    ).get_attribute("href")
    page.screenshot(path=str(tmp_path / f"pending-{width}.png"), full_page=True)
    with app.app_context():
        row = AccessRequest.query.one()
        assert row.invitation_id is None
    admin_context = browser.new_context(viewport={"width": width, "height": 900})
    admin = admin_context.new_page()
    admin.goto(request_site.url + "/login")
    admin.locator('[name="username"]').fill("reviewer")
    admin.locator('[name="password"]').fill("test-review-password")
    admin.locator("#password-login").click()
    admin.goto(request_site.url + "/admin#access-requests")
    expect(admin.get_by_role("heading", name="Access requests")).to_be_visible()
    admin.locator('[name="server_id"]').select_option(label="Example Plex")
    admin.get_by_role("checkbox", name="Movies").check()
    admin.screenshot(path=str(tmp_path / f"admin-{width}.png"), full_page=True)
    admin.get_by_role("button", name="Approve selected access").click()
    page.get_by_role("link", name="Refresh status").click()
    expect(page.get_by_role("heading", name="Request approved")).to_be_visible()
    page.screenshot(path=str(tmp_path / f"approved-{width}.png"), full_page=True)
    # A private status capability permits reading, never redemption as another account.
    stranger_context = browser.new_context()
    stranger = stranger_context.new_page()
    plex_sign_in(stranger, "wrong-account")
    stranger.goto(private_link)
    expect(stranger.get_by_role("heading", name="Request approved")).to_be_visible()
    stranger.get_by_role("button", name="Continue with Plex").click()
    stranger.get_by_role("link", name="Confirm Plex sign-in").click()
    expect(stranger.get_by_role("alert")).to_contain_text("approved for this request")
    request_site.plex.invite_friend.assert_not_called()
    page.get_by_role("button", name="Continue with Plex").click()
    page.get_by_role("link", name="Confirm Plex sign-in").click()
    page.wait_for_url("**/wizard/**")
    expect(page.get_by_text("Welcome to Plex")).to_be_visible()
    with app.app_context():
        row = AccessRequest.query.one()
        assert row.status == "completed" and row.invitation.used
    request_site.plex.invite_friend.assert_called_once()
    assert (
        page.evaluate("Object.values(sessionStorage).join(' ')").find(
            "test-requester-token"
        )
        == -1
    )
    context.close()
    admin_context.close()
    stranger_context.close()
