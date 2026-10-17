from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.blueprints.access_requests.routes import request_limiter
from app.extensions import db
from app.models import AccessRequest, Invitation, Library, MediaServer, Settings, User
from app.services import access_requests as service


@pytest.fixture
def setup_requests(app, session, monkeypatch):
    request_limiter.reset()
    app.config["WTF_CSRF_ENABLED"] = False
    db.session.add(Settings(key="public_access_requests", value="true"))
    server = MediaServer(
        name="Test Plex",
        server_type="plex",
        url="http://plex.example.test",
        api_key="owner-secret",
        verified=True,
    )
    db.session.add(server)
    db.session.flush()
    library = Library(
        name="Movies", external_id="101", server_id=server.id, enabled=True
    )
    db.session.add(library)
    db.session.commit()
    account = SimpleNamespace(
        id=42,
        username="requester",
        email="requester@example.test",
        confirmed=True,
        guest=False,
    )
    monkeypatch.setattr(service, "MyPlexAccount", Mock(return_value=account))
    yield SimpleNamespace(server=server, library=library, account=account)
    app.config["WTF_CSRF_ENABLED"] = False
    request_limiter.reset()


def admin_login(client):
    from flask import g

    g.pop("_login_user", None)
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = "admin"
        browser_session["_fresh"] = True


def submit(client, **overrides):
    return client.post(
        "/request-access",
        data={
            "identifier": "requester",
            "display_name": "A friend",
            "oauth_token": "requester-secret",
            **overrides,
        },
    )


def approved(client, setup_requests):
    assert submit(client).status_code == 200
    row = AccessRequest.query.one()
    service.approve(row.id, setup_requests.server.id, [setup_requests.library.id])
    return row


@pytest.fixture
def plex(setup_requests, monkeypatch):
    account = setup_requests.account
    shares = []
    resources = []
    account.resources = Mock(side_effect=lambda: resources.copy())

    def accept(_owner):
        resources.append(SimpleNamespace(clientIdentifier="server-machine"))

    account.acceptInvite = Mock(side_effect=accept)
    client = Mock()
    client.server.machineIdentifier = "server-machine"
    client.server.library.sections.return_value = [SimpleNamespace(title="Movies")]
    client._get_all_library_global_ids.return_value = {"Movies": 101}
    client.admin.username = "owner"
    client.admin.users.side_effect = lambda: shares.copy()
    client.admin.pendingInvites.return_value = []

    def share(*_args, **_kwargs):
        share = SimpleNamespace(
            machineIdentifier="server-machine",
            sections=lambda: [SimpleNamespace(id=101, shared=True)],
        )
        shares.append(SimpleNamespace(id=42, servers=[share]))

    client.invite_friend.side_effect = share

    def create_user(data):
        user = User(**data)
        db.session.add(user)
        return user

    client._create_user_with_identity_linking.side_effect = create_user
    monkeypatch.setattr("app.services.media.plex.PlexClient", Mock(return_value=client))
    monkeypatch.setattr(
        "app.services.media.plex.get_client_for_media_server", Mock(return_value=client)
    )
    return SimpleNamespace(
        client=client, account=account, shares=shares, resources=resources
    )


def test_disabled_by_default_and_admin_opt_in(client, setup_requests):
    setting = Settings.query.filter_by(key="public_access_requests").one()
    db.session.delete(setting)
    db.session.commit()
    assert client.get("/request-access").status_code == 404
    assert submit(client).status_code == 404
    admin_login(client)
    assert (
        client.post("/access-requests/settings", data={"enabled": "true"}).status_code
        == 302
    )
    assert client.get("/request-access").status_code == 200


def test_submission_private_status_duplicate_and_restoration(
    app, client, setup_requests
):
    response = submit(client)
    assert response.status_code == 200
    row = AccessRequest.query.one()
    assert row.status == "pending"
    assert row.invitation is None
    assert Invitation.query.count() == User.query.count() == 0
    token = response.json["redirect"].split("#")[1]
    assert len(token) == 43 and row.token_hash != token
    assert "requester-secret" not in response.text
    assert "Waiting for review" in client.get("/request-access/status").text
    stranger = app.test_client()
    assert "requester" not in stranger.get("/request-access/status").text
    assert (
        stranger.post("/request-access/claim", data={"token": "a" * 43}).status_code
        == 404
    )
    assert (
        stranger.post("/request-access/claim", data={"token": token}).status_code == 200
    )
    assert "Waiting for review" in stranger.get("/request-access/status").text
    assert submit(client).status_code == 200
    assert AccessRequest.query.count() == 1
    assert "#" not in submit(client).json["redirect"]
    fresh = app.test_client()
    assert (
        fresh.post(
            "/request-access/restore", data={"oauth_token": "requester-secret"}
        ).status_code
        == 200
    )
    assert "Waiting for review" in fresh.get("/request-access/status").text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.parametrize(
    "data",
    [
        {"identifier": "someone-else"},
        {"identifier": "x" * 255},
        {"display_name": "x" * 101},
        {"identifier": "a\nb"},
        {"oauth_token": ""},
    ],
)
def test_invalid_submission(client, setup_requests, data):
    assert submit(client, **data).status_code == 400
    assert AccessRequest.query.count() == 0


@pytest.mark.parametrize(
    "field,value", [("guest", True), ("confirmed", False), ("id", None)]
)
def test_unverified_account_refused(client, setup_requests, field, value):
    setattr(setup_requests.account, field, value)
    assert submit(client).status_code == 400
    assert AccessRequest.query.count() == 0


def test_plex_failure_is_sanitized_and_does_not_save(
    client, setup_requests, monkeypatch, caplog
):
    monkeypatch.setattr(
        service,
        "MyPlexAccount",
        Mock(side_effect=RuntimeError("requester-secret owner-secret")),
    )
    response = submit(client)
    assert response.status_code == 400
    assert "secret" not in response.text + caplog.text
    assert AccessRequest.query.count() == 0


def test_admin_auth_and_csrf(app, client, setup_requests):
    assert (
        client.get("/access-requests", headers={"HX-Request": "true"}).status_code
        == 302
    )
    assert (
        client.post("/access-requests/settings", data={"enabled": "true"}).status_code
        == 302
    )
    app.config["WTF_CSRF_ENABLED"] = True
    assert submit(client).status_code == 400
    admin_login(client)
    assert (
        client.post("/access-requests/settings", data={"enabled": "true"}).status_code
        == 400
    )
    page = client.get("/access-requests", headers={"HX-Request": "true"})
    import re

    csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)[1]
    assert (
        client.post(
            "/access-requests/settings", data={"enabled": "true", "csrf_token": csrf}
        ).status_code
        == 302
    )


def test_rate_limit_active_even_when_upstream_limiter_disabled(client, setup_requests):
    for _ in range(5):
        assert submit(client).status_code == 200
    assert submit(client).status_code == 429
    assert AccessRequest.query.count() == 1


def test_reject_grants_nothing_and_cannot_be_approved(client, setup_requests, plex):
    submit(client)
    row = AccessRequest.query.one()
    admin_login(client)
    assert client.post(f"/access-requests/{row.id}/reject").status_code == 302
    assert row.status == "rejected"
    assert Invitation.query.count() == 0
    assert (
        client.post(
            "/request-access/redeem", data={"oauth_token": "requester-secret"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/access-requests/{row.id}/approve",
            data={
                "server_id": setup_requests.server.id,
                "libraries": setup_requests.library.id,
            },
        ).status_code
        == 409
    )
    plex.client.invite_friend.assert_not_called()
    assert User.query.count() == 0


def test_approval_redemption_and_repeated_completion(client, setup_requests, plex):
    row = approved(client, setup_requests)
    assert row.invitation.plex_account_id == "42"
    assert row.invitation.libraries == [setup_requests.library]
    plex.client.invite_friend.assert_not_called()
    assert service.redeem(row, plex.account, "requester-secret") == row.invitation.code
    assert row.status == "completed" and row.invitation.used
    assert User.query.one().username == "requester"
    plex.client.invite_friend.assert_called_once_with(
        "requester@example.test", ["Movies"], False, False, False, redact_errors=True
    )
    assert service.redeem(row, plex.account, "requester-secret") == row.invitation.code
    plex.client.invite_friend.assert_called_once()
    assert (
        client.post("/request-access/redeem", data={"oauth_token": "requester-secret"})
        .json["redirect"]
        .endswith("/wizard/")
    )


def test_wrong_account_and_legacy_paths_cannot_redeem(client, setup_requests, plex):
    row = approved(client, setup_requests)
    code = row.invitation.code
    wrong = SimpleNamespace(id=99)
    with pytest.raises(ValueError, match="approved"):
        service.redeem(row, wrong, "wrong-secret")
    assert (
        client.post("/join", data={"code": code, "token": "wrong-secret"}).status_code
        == 403
    )
    assert client.post("/join", data={"code": code}).status_code == 403
    assert (
        client.post(
            "/invitation/process", data={"code": code, "oauth_token": "wrong-secret"}
        ).status_code
        == 403
    )
    plex.client.invite_friend.assert_not_called()
    assert row.status == "approved" and not row.invitation.used


@pytest.mark.parametrize(
    "change", ["empty_libraries", "extra_libraries", "unlimited", "downloads"]
)
def test_scope_changes_fail_closed(client, setup_requests, plex, change):
    row = approved(client, setup_requests)
    if change == "empty_libraries":
        row.invitation.libraries = []
    elif change == "extra_libraries":
        row.invitation.libraries.append(
            Library(name="TV", external_id="102", server_id=setup_requests.server.id)
        )
    elif change == "unlimited":
        row.invitation.unlimited = True
    else:
        row.invitation.allow_downloads = True
    db.session.commit()
    with pytest.raises(ValueError, match="scope changed"):
        service.redeem(row, plex.account, "requester-secret")
    plex.client.invite_friend.assert_not_called()


def test_approval_rollback_does_not_orphan_invite(client, setup_requests, monkeypatch):
    submit(client)
    row = AccessRequest.query.one()
    original = service.create_invite

    def fail(form, **kwargs):
        original(form, **kwargs)
        raise RuntimeError("disk failure")

    monkeypatch.setattr(service, "create_invite", fail)
    with pytest.raises(RuntimeError):
        service.approve(row.id, setup_requests.server.id, [setup_requests.library.id])
    assert row.status == "pending"
    assert row.invitation_id is None and Invitation.query.count() == 0


@pytest.mark.parametrize("stage", ["sharing", "accepting", "local"])
def test_failure_recovery_reuses_invitation(client, setup_requests, plex, stage):
    row = approved(client, setup_requests)
    if stage == "sharing":
        plex.client.invite_friend.side_effect = RuntimeError(
            "owner-secret requester-secret"
        )
    elif stage == "accepting":
        plex.account.acceptInvite.side_effect = RuntimeError("requester-secret")
    else:
        plex.client._create_user_with_identity_linking.side_effect = RuntimeError(
            "database failure"
        )
    with pytest.raises(ValueError, match="review and recover") as failure:
        service.redeem(row, plex.account, "requester-secret")
    assert "secret" not in str(failure.value)
    assert row.status == "needs_review" and not row.invitation.used
    with pytest.raises(ValueError):
        service.redeem(row, plex.account, "requester-secret")
    assert plex.client.invite_friend.call_count == 1
    service.recover(row.id)
    assert row.status == "approved"
    if stage == "sharing":
        plex.client.invite_friend.side_effect = lambda *_args, **_kwargs: None
    if stage == "accepting":
        plex.account.acceptInvite.side_effect = lambda _owner: plex.resources.append(
            SimpleNamespace(clientIdentifier="server-machine")
        )
    if stage == "local":
        plex.client._create_user_with_identity_linking.side_effect = lambda data: (
            db.session.add(User(**data))
        )

        # Use the real local constructor again after the simulated write failure.
        def create(data):
            user = User(**data)
            db.session.add(user)
            return user

        plex.client._create_user_with_identity_linking.side_effect = create
    if stage == "sharing":
        plex.account.resources.return_value = []
    assert Invitation.query.count() == 1
    service.redeem(row, plex.account, "requester-secret")
    assert row.status == "completed"
    assert plex.client.invite_friend.call_count == (2 if stage == "sharing" else 1)


def test_stale_in_progress_requires_reconciliation(client, setup_requests, plex):
    row = approved(client, setup_requests)
    row.status = "redeeming"
    db.session.commit()
    with pytest.raises(service.RequestConflict, match="five minutes"):
        service.recover(row.id)
    row.updated_at = datetime.now(UTC) - timedelta(minutes=6)
    db.session.commit()
    service.recover(row.id)
    assert row.status == "approved"
    plex.client.invite_friend.assert_not_called()


@pytest.mark.parametrize("actions", [("approve", "approve"), ("approve", "reject")])
def test_concurrent_approval_and_rejection(app, client, setup_requests, actions):
    submit(client)
    row = AccessRequest.query.one()
    request_id, server_id, library_id = (
        row.id,
        setup_requests.server.id,
        setup_requests.library.id,
    )
    barrier = Barrier(2)

    def act(action):
        with app.app_context():
            barrier.wait()
            try:
                if action == "approve":
                    service.approve(request_id, server_id, [library_id])
                else:
                    service.reject(request_id)
                return "saved"
            except service.RequestConflict:
                return "conflict"

    # Two actual connections, not mocked row locks or serialized route calls.
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(act, actions)) == ["conflict", "saved"]
    db.session.expire_all()
    status = AccessRequest.query.one().status
    assert status in {"approved", "rejected"}
    assert Invitation.query.count() == (1 if status == "approved" else 0)


def test_concurrent_duplicate_submission(app, setup_requests):
    barrier = Barrier(2)

    def create(_number):
        with app.app_context():
            barrier.wait()
            row, _token = service.submit(
                "requester", "A friend", setup_requests.account
            )
            return row.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(set(pool.map(create, range(2)))) == 1
    assert AccessRequest.query.count() == 1
    assert Invitation.query.count() == 0


def test_concurrent_redemption_has_one_remote_operation(
    app, client, setup_requests, plex
):
    row = approved(client, setup_requests)
    request_id = row.id
    barrier = Barrier(2)

    def redeem(_number):
        with app.app_context():
            row = db.session.get(AccessRequest, request_id)
            barrier.wait()
            try:
                service.redeem(row, plex.account, "requester-secret")
                return "completed"
            except service.RequestConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(redeem, range(2))) == ["completed", "conflict"]
    db.session.expire_all()
    assert AccessRequest.query.one().status == "completed"
    plex.client.invite_friend.assert_called_once()
    assert User.query.count() == Invitation.query.count() == 1


def test_recovery_after_usage_commit_does_not_resend_or_duplicate_user(
    client, setup_requests, plex, monkeypatch
):
    row = approved(client, setup_requests)
    original = service.mark_server_used

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("process interrupted after usage commit")

    monkeypatch.setattr(service, "mark_server_used", interrupted)
    with pytest.raises(ValueError, match="review and recover"):
        service.redeem(row, plex.account, "requester-secret")
    assert row.invitation.used and row.status == "needs_review"
    monkeypatch.setattr(service, "mark_server_used", original)
    service.recover(row.id)
    service.redeem(row, plex.account, "requester-secret")
    assert row.status == "completed"
    plex.client.invite_friend.assert_called_once()
    assert User.query.count() == 1


def test_pre_invite_wizard_cannot_be_bypassed(client, setup_requests, plex):
    from app.models import WizardStep

    row = approved(client, setup_requests)
    db.session.add(
        WizardStep(
            server_type="plex",
            category="pre_invite",
            position=0,
            title="Before you join",
            markdown="# Before you join",
        )
    )
    db.session.commit()
    response = client.post(
        "/request-access/redeem", data={"oauth_token": "requester-secret"}
    )
    assert response.status_code == 200
    assert "pre-wizard" in response.json["redirect"]
    assert row.status == "approved"
    plex.client.invite_friend.assert_not_called()


def test_bad_recovery_and_ambiguous_library_fail_closed(client, setup_requests, plex):
    row = approved(client, setup_requests)
    plex.client.server.library.sections.return_value = [
        SimpleNamespace(title="Movies"),
        SimpleNamespace(title="movies"),
    ]
    with pytest.raises(ValueError, match="review and recover"):
        service.redeem(row, plex.account, "requester-secret")
    plex.client.invite_friend.assert_not_called()
    share = SimpleNamespace(
        machineIdentifier="server-machine",
        sections=lambda: [SimpleNamespace(id=999, shared=True)],
    )
    plex.shares.append(SimpleNamespace(id=42, servers=[share]))
    with pytest.raises(ValueError, match="could not confirm"):
        service.recover(row.id)
    assert row.status == "needs_review"
    assert Invitation.query.count() == 1


def test_notification_failure_does_not_lose_request(
    client, setup_requests, monkeypatch
):
    notify = Mock(side_effect=RuntimeError("notification failure"))
    monkeypatch.setattr("app.services.notifications.notify", notify)
    assert submit(client).status_code == 200
    assert submit(client).status_code == 200
    assert AccessRequest.query.count() == 1
    notify.assert_called_once()
    assert "requester" not in str(notify.call_args)


def test_bound_plex_adapters_redact_exception_credentials(
    client, setup_requests, caplog
):
    from app.services.media.plex import PlexClient, PlexInvitationError

    plex = object.__new__(PlexClient)
    plex._server = Mock(machineIdentifier="machine")
    plex._admin = Mock()
    plex.token = "owner-secret"
    plex._admin.inviteFriend.side_effect = RuntimeError("owner-secret requester-secret")
    with pytest.raises(PlexInvitationError) as failure:
        plex.invite_friend(
            "requester@example.test", ["Movies"], False, False, redact_errors=True
        )
    assert "secret" not in str(failure.value) + caplog.text


@pytest.mark.parametrize("path", ["claim", "restore", "redeem"])
def test_all_private_mutations_require_csrf(app, client, setup_requests, path):
    app.config["WTF_CSRF_ENABLED"] = True
    assert (
        client.post(
            f"/request-access/{path}",
            data={"token": "a" * 43, "oauth_token": "requester-secret"},
        ).status_code
        == 400
    )


def test_unexpected_provisioning_failure_is_sanitized(
    client, setup_requests, monkeypatch, caplog
):
    approved(client, setup_requests)
    monkeypatch.setattr(
        service,
        "redeem",
        Mock(side_effect=RuntimeError("owner-secret requester-secret")),
    )
    response = client.post(
        "/request-access/redeem", data={"oauth_token": "requester-secret"}
    )
    assert response.status_code == 503
    assert "secret" not in response.text + caplog.text


def test_streamed_body_cannot_bypass_request_size_limit(client, setup_requests):
    from io import BytesIO

    stream = BytesIO(b"identifier=" + b"a" * 9000)
    response = client.open(
        "/request-access",
        method="POST",
        content_type="application/x-www-form-urlencoded",
        input_stream=stream,
        environ_overrides={"CONTENT_LENGTH": "", "wsgi.input_terminated": True},
    )
    assert response.status_code in {400, 413}
    assert stream.tell() <= 8192
    assert AccessRequest.query.count() == 0
