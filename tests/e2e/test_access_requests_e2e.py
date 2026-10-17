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
    yield SimpleNamespace(
        url=f"http://127.0.0.1:{server.server_port}", plex=plex, account=account
    )
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
    page.get_by_role("button", name="I have an account", exact=True).click()
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
    admin.goto(request_site.url + "/login?next=%2Fadmin%23access-requests")
    admin.locator('[name="username"]').fill("reviewer")
    admin.locator('[name="password"]').fill("test-review-password")
    admin.locator("#password-login").click()
    admin.wait_for_url(request_site.url + "/admin#access-requests")
    expect(admin.get_by_role("heading", name="Access requests")).to_be_visible()
    admin.locator('[name="server_id"]').select_option(label="Example Plex")
    admin.get_by_role("checkbox", name="Movies").check()
    admin.screenshot(path=str(tmp_path / f"admin-{width}.png"), full_page=True)
    with admin.expect_response(
        lambda response: (
            response.request.method == "POST" and response.url.endswith("/approve")
        )
    ) as approval:
        admin.get_by_role("button", name="Approve selected access").click()
    assert approval.value.status == 302, approval.value.text()
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


@pytest.mark.parametrize("width", [1280, 390])
def test_new_account_guidance_preserves_form_and_waits_for_sign_in(
    app, browser, request_site, tmp_path, width
):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    plex_sign_in(page, "test-requester-token")
    context.route(
        "https://www.plex.tv/sign-up/",
        lambda route: route.fulfill(
            content_type="text/html", body="<h1>Example Plex signup</h1>"
        ),
    )
    external_requests = []
    page.on(
        "request",
        lambda request: (
            external_requests.append(request.url)
            if "plex.tv/api/v2/pins" in request.url
            else None
        ),
    )
    page.goto(request_site.url + "/request-access")
    page.get_by_role("button", name="I need an account", exact=True).focus()
    page.keyboard.press("Enter")
    heading = page.get_by_role("heading", name="Create your Plex account")
    expect(heading).to_be_visible()
    expect(heading).to_be_focused()
    expect(page.get_by_text("Finish signing up and confirm your email")).to_be_visible()
    expect(page.get_by_text("Return to this tab and continue below")).to_be_visible()
    expect(page.get_by_role("link", name="Check an existing request")).to_be_visible()
    signup = page.get_by_role("link", name="Create a Plex account (opens in a new tab)")
    expect(signup).to_have_attribute("rel", "noopener noreferrer")
    page.screenshot(path=str(tmp_path / f"signup-{width}.png"), full_page=True)
    with context.expect_page() as opened:
        signup.click()
    signup_page = opened.value
    expect(
        signup_page.get_by_role("heading", name="Example Plex signup")
    ).to_be_visible()
    assert signup_page.url == "https://www.plex.tv/sign-up/"
    signup_page.close()
    page.get_by_role("button", name="I have my account—continue").click()
    expect(page.get_by_label("Plex email or username")).to_be_focused()
    page.get_by_label("Plex email or username").fill("requester")
    page.get_by_label("Who are you?").fill("A new friend")
    page.get_by_role("button", name="I need to create an account first").click()
    page.get_by_role("button", name="Back", exact=True).click()
    page.get_by_role("button", name="I have an account", exact=True).click()
    expect(page.get_by_label("Plex email or username")).to_have_value("requester")
    expect(page.get_by_label("Who are you?")).to_have_value("A new friend")
    assert external_requests == []
    assert page.evaluate("sessionStorage.getItem('wizarr.accessRequestSignIn')") is None
    with app.app_context():
        assert AccessRequest.query.count() == User.query.count() == 0
    request_site.plex.invite_friend.assert_not_called()
    page.get_by_role("button", name="Sign in with Plex and submit").click()
    page.get_by_role("link", name="Confirm Plex sign-in").click()
    expect(page.get_by_role("heading", name="Waiting for review")).to_be_visible()
    with app.app_context():
        assert AccessRequest.query.one().status == "pending"
    request_site.plex.invite_friend.assert_not_called()
    context.close()


def test_unconfirmed_account_can_retry_after_confirmation(app, browser, request_site):
    context = browser.new_context()
    page = context.new_page()
    plex_sign_in(page, "test-requester-token")
    request_site.account.confirmed = False
    page.goto(request_site.url + "/request-access")
    page.get_by_role("button", name="I have an account", exact=True).click()
    page.get_by_label("Plex email or username").fill("requester")
    page.get_by_label("Who are you?").fill("A friend")
    page.get_by_role("button", name="Sign in with Plex and submit").click()
    page.get_by_role("link", name="Confirm Plex sign-in").click()
    expect(page.get_by_role("alert")).to_contain_text("Confirm your email in Plex")
    expect(page.get_by_label("Who are you?")).to_have_value("A friend")
    expect(
        page.get_by_role("button", name="Sign in with Plex and submit")
    ).to_be_enabled()
    with app.app_context():
        assert AccessRequest.query.count() == User.query.count() == 0
    request_site.account.confirmed = True
    page.get_by_role("button", name="Sign in with Plex and submit").click()
    page.get_by_role("link", name="Confirm Plex sign-in").click()
    expect(page.get_by_role("heading", name="Waiting for review")).to_be_visible()
    with app.app_context():
        assert AccessRequest.query.one().status == "pending"
    context.close()


def test_submission_locks_steps_until_failure_then_allows_retry(browser, request_site):
    context = browser.new_context()
    page = context.new_page()
    plex_sign_in(page, "test-requester-token")
    submissions = []

    def hold_submission(route):
        if route.request.method == "POST":
            submissions.append(route)
        else:
            route.continue_()

    page.route(request_site.url + "/request-access", hold_submission)
    page.goto(request_site.url + "/request-access")
    page.get_by_role("button", name="I have an account", exact=True).click()
    page.get_by_label("Plex email or username").fill("requester")
    page.get_by_role("button", name="Sign in with Plex and submit").click()
    page.get_by_role("link", name="Confirm Plex sign-in").click()
    expect(page.get_by_role("alert")).to_contain_text("Completing Plex sign-in")
    expect(
        page.get_by_role("button", name="I need to create an account first")
    ).to_be_disabled()
    expect(page.get_by_role("button", name="Back", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="Cancel sign-in", exact=True)).to_be_hidden()
    assert len(submissions) == 1
    submissions[0].fulfill(
        status=400,
        content_type="application/json",
        body=json.dumps({"error": "Please try again."}),
    )
    expect(page.get_by_role("alert")).to_contain_text("Please try again.")
    expect(
        page.get_by_role("button", name="I need to create an account first")
    ).to_be_enabled()
    expect(page.get_by_role("button", name="Back", exact=True)).to_be_enabled()
    expect(
        page.get_by_role("button", name="Sign in with Plex and submit")
    ).to_be_enabled()
    page.unroute(request_site.url + "/request-access", hold_submission)
    page.get_by_role("button", name="Sign in with Plex and submit").click()
    page.get_by_role("link", name="Confirm Plex sign-in").click()
    expect(page.get_by_role("heading", name="Waiting for review")).to_be_visible()
    context.close()


def resume_sign_in(page, url, *, expired=False):
    page.goto(url + "/request-access")
    state = {
        "mode": "submit",
        "pinId": 1,
        "clientId": "example-client",
        "identifier": "requester",
        "display_name": "A returning friend",
        "duration": -1 if expired else 300000,
    }
    page.evaluate(
        """state => {
            state.deadline = Date.now() + state.duration;
            delete state.duration;
            sessionStorage.setItem('wizarr.accessRequestSignIn', JSON.stringify(state));
        }""",
        state,
    )
    page.reload(wait_until="domcontentloaded")


def test_expired_sign_in_keeps_form_and_allows_retry(browser, request_site):
    context = browser.new_context()
    page = context.new_page()
    plex_sign_in(page, "test-requester-token")
    resume_sign_in(page, request_site.url, expired=True)
    expect(page.get_by_role("alert")).to_contain_text("could not be completed")
    expect(page.get_by_label("Plex email or username")).to_have_value("requester")
    expect(page.get_by_label("Who are you?")).to_have_value("A returning friend")
    assert page.evaluate("sessionStorage.getItem('wizarr.accessRequestSignIn')") is None
    expect(
        page.get_by_role("button", name="Sign in with Plex and submit")
    ).to_be_enabled()
    page.get_by_role("button", name="Sign in with Plex and submit").click()
    page.get_by_role("link", name="Confirm Plex sign-in").click()
    expect(page.get_by_role("heading", name="Waiting for review")).to_be_visible()
    context.close()


@pytest.mark.parametrize(
    "result",
    [
        {"authToken": "test-requester-token"},
        {"errors": [{"message": "private upstream error"}]},
    ],
)
def test_cancelled_sign_in_ignores_late_results(app, browser, request_site, result):
    context = browser.new_context()
    page = context.new_page()
    pending = []
    page.route("https://plex.tv/api/v2/pins/1", lambda route: pending.append(route))
    resume_sign_in(page, request_site.url)
    expect(page.get_by_role("alert")).to_contain_text("Confirming Plex sign-in")
    page.get_by_role("button", name="Cancel sign-in", exact=True).click()
    expect(page.get_by_label("Who are you?")).to_have_value("A returning friend")
    assert len(pending) == 1
    with page.expect_event(
        "requestfinished", predicate=lambda req: "/pins/1" in req.url
    ):
        pending[0].fulfill(
            content_type="application/json",
            body=json.dumps(result),
        )
    page.evaluate("() => new Promise(requestAnimationFrame)")
    expect(page.get_by_role("alert")).to_be_empty()
    expect(
        page.get_by_role("button", name="Sign in with Plex and submit")
    ).to_be_enabled()
    assert page.evaluate("sessionStorage.getItem('wizarr.accessRequestSignIn')") is None
    with app.app_context():
        assert AccessRequest.query.count() == User.query.count() == 0
    request_site.plex.invite_friend.assert_not_called()
    context.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_plex_permissions_hide_retired_options(app, browser, request_site, width):
    with app.app_context():
        server = MediaServer.query.one()
        server_id = server.id
        server.allow_downloads = True
        server.allow_live_tv = True
        server.allow_mobile_uploads = True
        user = User(
            username="example-viewer",
            email="viewer@example.test",
            token="test-token",
            code="EXAMPLE",
            server_id=server_id,
            allow_live_tv=True,
            allow_camera_upload=True,
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    page.goto(request_site.url + "/login?next=%2Fsettings")
    page.locator('[name="username"]').fill("reviewer")
    page.locator('[name="password"]').fill("test-review-password")
    page.locator("#password-login").click()
    page.wait_for_url(request_site.url + "/settings")
    for endpoint in ["/settings/servers/create", f"/settings/servers/{server_id}/edit"]:
        # Use the real admin shell and HTMX, including execution of the modal script.
        page.goto(request_site.url + "/settings")
        page.locator('button[hx-get="/settings/servers"]').click()
        page.locator(f'button[hx-get="{endpoint}"]').click()
        modal = page.locator("#create-server-modal")
        expect(
            modal.get_by_text(
                "Plex's Downloads subscription requirements still apply.", exact=False
            )
        ).to_be_visible()
        expect(modal.get_by_text("Allow Live TV", exact=True)).to_be_hidden()
        expect(modal.get_by_text("Allow Mobile Uploads", exact=True)).to_be_hidden()
        expect(modal.locator('[name="allow_live_tv"]')).to_be_disabled()
        expect(modal.locator('[name="allow_mobile_uploads"]')).to_be_disabled()
        if endpoint.endswith("/edit"):
            expect(modal.locator('[name="allow_downloads"]')).to_be_checked()
        modal.locator("#server_type").select_option("emby")
        expect(modal.get_by_text("Allow Live TV", exact=True)).to_be_visible()
        expect(modal.get_by_text("Allow Mobile Uploads", exact=True)).to_be_visible()
        expect(modal.locator('[name="allow_live_tv"]')).to_be_enabled()
        expect(modal.locator('[name="allow_mobile_uploads"]')).to_be_enabled()
        for name, label in [
            ("allow_live_tv", "Allow Live TV"),
            ("allow_mobile_uploads", "Allow Mobile Uploads"),
        ]:
            if not modal.locator(f'[name="{name}"]').is_checked():
                modal.get_by_text(label, exact=True).click()
        modal.locator("#server_type").select_option("plex")
        submitted = modal.locator("form").evaluate(
            "form => [...new FormData(form).keys()]"
        )
        assert "allow_live_tv" not in submitted
        assert "allow_mobile_uploads" not in submitted
        expect(modal.get_by_text("Allow Live TV", exact=True)).to_be_hidden()
        expect(modal.get_by_text("Allow Mobile Uploads", exact=True)).to_be_hidden()
    invite = context.request.get(
        request_site.url + "/invite", headers={"HX-Request": "true"}
    )
    assert invite.status == 200
    assert 'name="allow_live_tv"' not in invite.text()
    assert 'name="allow_mobile_uploads"' not in invite.text()
    assert "Leave off for ordinary library sharing" in invite.text()
    details = context.request.get(request_site.url + f"/user/{user_id}")
    assert details.status == 200
    assert "Toggle live TV permission" not in details.text()
    assert "Toggle camera upload permission" not in details.text()
    assert "Allow offline copies of shared media" in details.text()
    summary = context.request.get(request_site.url + f"/user/{user_id}/details")
    assert summary.status == 200
    assert "Camera Upload" not in summary.text()
    assert "Live TV" not in summary.text()
    with app.app_context():
        db.session.expire_all()
        # Viewing the new UI does not rewrite existing stored permissions.
        assert db.session.get(MediaServer, server_id).allow_live_tv is True
        assert db.session.get(User, user_id).allow_camera_upload is True
    context.close()
