from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from werkzeug.security import generate_password_hash

from app.blueprints.auth.redirects import login_destination
from app.extensions import db
from app.models import AdminAccount, Settings, WebAuthnCredential


class LoginForm(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.action = None
        self.next_url = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.action = attributes.get("action")
        if tag == "input" and attributes.get("name") == "next":
            self.next_url = attributes.get("value")


@pytest.fixture
def administrator(session):
    account = AdminAccount(username="reviewer")
    account.set_password("test-review-password")
    session.add(account)
    session.commit()
    return account


@pytest.mark.parametrize(
    "destination",
    ["/admin", "/admin#access-requests", "/settings/general?tab=display"],
)
def test_password_login_preserves_next_through_form(client, administrator, destination):
    page = client.get("/login?" + urlencode({"next": destination}))
    form = LoginForm(page.text)
    target = form.next_url
    assert target == destination
    response = client.post(
        form.action,
        data={"username": "reviewer", "password": "wrong", "next": target},
    )
    assert response.status_code == 200
    assert LoginForm(response.text).next_url == destination
    response = client.post(
        form.action,
        data={
            "username": "reviewer",
            "password": "test-review-password",
            "next": target,
        },
    )
    assert response.status_code == 302
    assert response.location == destination


@pytest.mark.parametrize(
    "destination",
    [
        None,
        "",
        "/",
        "/login",
        "/logout",
        "https://evil.example/",
        "//evil.example/",
        "///evil.example/",
        "/\\evil.example/",
        "/%2fevil.example/",
        "/%5cevil.example/",
        "/\nevil.example/",
        "/%09/evil.example/",
        "admin",
        {"next": "/admin"},
    ],
)
def test_unsafe_or_missing_next_defaults_to_admin(app, destination):
    with app.test_request_context():
        assert login_destination(destination) == "/admin"


def test_login_defaults_to_admin_and_rejects_external_next(client, administrator):
    for target in [None, "https://evil.example/"]:
        data = {"username": "reviewer", "password": "test-review-password"}
        if target:
            data["next"] = target
        assert client.post("/login", data=data).location == "/admin"


def test_legacy_password_login_honors_query_next(client, session):
    session.add_all(
        [
            Settings(key="admin_username", value="legacy"),
            Settings(
                key="admin_password", value=generate_password_hash("legacy-password")
            ),
        ]
    )
    session.commit()
    response = client.post(
        "/login?next=/admin", data={"username": "legacy", "password": "legacy-password"}
    )
    assert response.location == "/admin"


def test_ldap_login_honors_next(client, session, monkeypatch):
    monkeypatch.setattr(
        "app.blueprints.auth.ldap_auth.handle_ldap_login", lambda *_args: (True, "")
    )
    response = client.post(
        "/login", data={"auth_method": "ldap", "next": "/admin#settings"}
    )
    assert response.location == "/admin#settings"


@pytest.mark.parametrize("two_factor", [False, True])
def test_passkey_login_preserves_next(client, administrator, monkeypatch, two_factor):
    import app.blueprints.webauthn.routes as webauthn

    db.session.add(
        WebAuthnCredential(
            admin_account_id=administrator.id,
            credential_id=b"credential",
            public_key=b"public",
            name="Test passkey",
            sign_count=0,
        )
    )
    db.session.commit()
    destination = "/admin#access-requests"
    if two_factor:
        response = client.post(
            "/login",
            data={
                "username": "reviewer",
                "password": "test-review-password",
                "next": destination,
            },
        )
        assert "Two-Factor Authentication" in response.text
    monkeypatch.setattr(
        webauthn,
        "get_rp_config",
        lambda: (
            "example.test",
            "Example",
            "https://example.test",
        ),
    )
    response = client.post("/webauthn/authenticate/begin", json={"next": destination})
    assert response.status_code == 200
    monkeypatch.setattr(
        webauthn,
        "parse_authentication_credential_json",
        lambda _value: SimpleNamespace(raw_id=b"credential"),
    )
    verified = []

    def verify(**kwargs):
        verified.append(kwargs)
        return SimpleNamespace(new_sign_count=1)

    monkeypatch.setattr(webauthn, "verify_authentication_response", verify)
    response = client.post("/webauthn/authenticate/complete", json={"credential": {}})
    assert response.json["verified"]
    assert len(verified) == 1
    if two_factor:
        response = client.post(response.json["redirect"])
        assert response.location == destination
    else:
        assert response.json["redirect"] == destination
    with client.session_transaction() as saved:
        assert "pending_2fa_next" not in saved
        assert "webauthn_next" not in saved
