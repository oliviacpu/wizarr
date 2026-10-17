"""Manual decisions are local transactions; Plex provisioning has durable checkpoints."""

import hashlib
import secrets
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from plexapi.myplex import MyPlexAccount
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AccessRequest, Invitation, Library, MediaServer, Settings, User
from app.services.invites import create_invite, mark_server_used


class RequestConflict(ValueError):
    pass


def enabled() -> bool:
    setting = Settings.query.filter_by(key="public_access_requests").first()
    return bool(setting and setting.value == "true")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _get_request(request_id: int) -> AccessRequest:
    row = db.session.get(AccessRequest, request_id)
    if row is None:
        raise ValueError("Request not found.")
    return row


def verified_account(token: str):
    """Resolve only the account that authenticated; never search the owner's friends."""
    try:
        account = MyPlexAccount(token=token, timeout=15)
        account_id = str(account.id)
        if (
            not account_id.isdecimal()
            or int(account_id) <= 0
            or len(account_id) > 32
            or not account.confirmed
            or account.guest
            or not account.email
            or not account.username
        ):
            raise ValueError("Account is not a confirmed Plex account")
    except Exception:
        raise ValueError(
            "Plex sign-in could not be verified. Please try again."
        ) from None
    return account


def submit(identifier: str, display_name: str, account):
    if identifier.casefold() not in {
        account.email.casefold(),
        account.username.casefold(),
    }:
        raise ValueError(
            "The entered identifier does not match the signed-in Plex account."
        )
    existing = AccessRequest.query.filter_by(plex_account_id=str(account.id)).first()
    if existing:
        return existing, None
    token = secrets.token_urlsafe(32)
    row = AccessRequest(
        entered_identifier=identifier,
        display_name=display_name,
        plex_account_id=str(account.id),
        plex_username=account.username,
        plex_email=account.email,
        token_hash=token_hash(token),
    )
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = AccessRequest.query.filter_by(
            plex_account_id=str(account.id)
        ).first()
        if existing is None:
            raise
        return existing, None
    from app.services.notifications import notify

    # No requester information or private links go to notification providers.
    with suppress(Exception):
        notify(
            "Access request",
            "A Plex access request is waiting in Wizarr.",
            "inbox",
            event_type="access_request",
        )
    return row, token


def _transition(
    request_id: int, states: list[str], *, expected_updated_at=None, **values
) -> None:
    query = AccessRequest.query.filter(
        AccessRequest.id == request_id, AccessRequest.status.in_(states)
    )
    if expected_updated_at is not None:
        query = query.filter(AccessRequest.updated_at == expected_updated_at)
    changed = query.update(
        {**values, "updated_at": datetime.now(UTC)}, synchronize_session=False
    )
    if changed != 1:
        db.session.rollback()
        raise RequestConflict(
            "This request has already changed. Refresh to see its status."
        )
    db.session.expire_all()


def approve(request_id: int, server_id: int, library_ids: list[int]):
    """Claim and create the invitation in one transaction, including all associations."""
    try:
        _transition(request_id, ["pending"], status="approving")
        row = _get_request(request_id)
        server = db.session.get(MediaServer, server_id)
        libraries = Library.query.filter(
            Library.id.in_(library_ids),
            Library.server_id == server_id,
            Library.enabled.is_(True),
        ).all()
        if not server or server.server_type != "plex" or not server.verified:
            raise ValueError("Select a verified Plex server.")
        if not library_ids or {lib.id for lib in libraries} != set(library_ids):
            raise ValueError(
                "Select at least one enabled library on the selected server."
            )
        invite = create_invite(
            {
                "server_ids": [str(server_id)],
                "libraries": [str(lib.id) for lib in libraries],
                "library_picker_used": "1",
                "expires": "never",
            },
            commit=False,
        )
        invite.plex_account_id = row.plex_account_id
        row.invitation = invite
        row.approved_scope = {
            "server_id": server_id,
            "library_ids": sorted(library_ids),
        }
        row.status = "approved"
        row.redemption_stage = "ready"
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        raise


def reject(request_id: int) -> None:
    _transition(request_id, ["pending"], status="rejected")
    db.session.commit()


def approved_scope(row: AccessRequest):
    invite = row.invitation
    scope = row.approved_scope or {}
    servers = list(invite.servers) if invite else []
    libraries = list(invite.libraries) if invite else []
    if (
        not invite
        or invite.plex_account_id != row.plex_account_id
        or invite.unlimited
        or invite.plex_home
        or any(
            (
                invite.allow_downloads,
                invite.allow_live_tv,
                invite.allow_mobile_uploads,
                invite.create_ldap_user,
            )
        )
        or len(servers) != 1
        or servers[0].id != scope.get("server_id")
        or servers[0].server_type != "plex"
        or not libraries
        or sorted(lib.id for lib in libraries) != scope.get("library_ids")
        or any(not lib.enabled or lib.server_id != servers[0].id for lib in libraries)
    ):
        raise ValueError(
            "The approved invitation scope changed. An administrator must review it."
        )
    return invite, servers[0], libraries


def _checkpoint(row: AccessRequest, stage: str):
    row.redemption_stage = stage
    row.updated_at = datetime.now(UTC)
    db.session.commit()


def _matching_shares(client, account_id: str):
    # Both are fresh Plex reads, not the application's user-list cache.
    people = list(client.admin.users()) + list(
        client.admin.pendingInvites(includeReceived=False)
    )
    return [
        share
        for person in people
        if str(person.id) == account_id
        for share in person.servers
        if share.machineIdentifier == client.server.machineIdentifier
    ]


def recover(request_id: int) -> None:
    """An admin reconciles an uncertain operation before allowing another attempt."""
    row = _get_request(request_id)
    if row is None:
        raise ValueError("Request not found.")
    if row.status in {"redeeming", "recovering"} and row.updated_at > datetime.now(
        UTC
    ).replace(tzinfo=None) - timedelta(minutes=5):
        raise RequestConflict(
            "Provisioning may still be running. Wait five minutes before recovery."
        )
    _transition(
        request_id,
        [row.status],
        expected_updated_at=row.updated_at,
        status="recovering",
    )
    db.session.commit()
    row = _get_request(request_id)
    try:
        _invite, server, libraries = approved_scope(row)
        from app.services.media.plex import PlexClient

        client = PlexClient(media_server=server)
        shares = _matching_shares(client, row.plex_account_id)
        if shares:
            expected = {str(lib.external_id) for lib in libraries}
            if (
                len(shares) != 1
                or {
                    str(section.id)
                    for section in shares[0].sections()
                    if section.shared
                }
                != expected
            ):
                raise ValueError(
                    "Plex sharing differs from the approval. Correct it in Plex, then reconcile again."
                )
        row.redemption_stage = "shared" if shares else "ready"
        row.status = "approved"
        row.updated_at = datetime.now(UTC)
        db.session.commit()
    except Exception:
        db.session.rollback()
        row = _get_request(request_id)
        row.status = "needs_review"
        row.updated_at = datetime.now(UTC)
        db.session.commit()
        raise ValueError(
            "Recovery could not confirm the approved Plex share. Check Plex and try again."
        ) from None


def redeem(row: AccessRequest, account, token: str) -> str:
    """Provision the bound account once; uncertain remote outcomes need admin recovery."""
    if str(account.id) != row.plex_account_id:
        raise ValueError("Sign in with the Plex account approved for this request.")
    if row.status == "completed":
        if not row.invitation:
            raise ValueError("The onboarding invitation is no longer available.")
        return row.invitation.code
    invite, server, libraries = approved_scope(row)
    from app.services.invites import is_invite_valid

    if not is_invite_valid(invite.code)[0] and not (
        invite.used and row.redemption_stage == "shared"
    ):
        raise ValueError(
            "The approved invitation is no longer valid. Contact the administrator."
        )
    _transition(row.id, ["approved"], status="redeeming")
    db.session.commit()
    row = _get_request(row.id)
    try:
        from app.services.media.plex import PlexClient, _invite_user

        client = PlexClient(media_server=server)
        if row.redemption_stage != "shared":
            # Refuse to silently alter an existing share or send another invite.
            if _matching_shares(client, row.plex_account_id):
                raise ValueError("Existing Plex sharing needs administrator review.")
            live_titles = [
                section.title.casefold() for section in client.server.library.sections()
            ]
            global_ids = client._get_all_library_global_ids(redact_errors=True)
            if any(
                live_titles.count(lib.name.casefold()) != 1
                or str(global_ids.get(lib.name)) != str(lib.external_id)
                for lib in libraries
            ):
                raise ValueError(
                    "The approved libraries no longer match Plex. Scan and review the server."
                )
            _checkpoint(row, "sharing")
            _invite_user(account.email, invite.code, 0, server)
            _checkpoint(row, "shared")

        _checkpoint(row, "accepting")
        # A share may already have been accepted after a previous interrupted attempt.
        if not any(
            resource.clientIdentifier == client.server.machineIdentifier
            for resource in account.resources()
        ):
            account.acceptInvite(client.admin.username)
        if not any(
            resource.clientIdentifier == client.server.machineIdentifier
            for resource in account.resources()
        ):
            raise ValueError("Plex has not confirmed server access yet.")

        _checkpoint(row, "local")
        user = User.query.filter_by(code=invite.code, server_id=server.id).first()
        if user is None:
            user = User.query.filter_by(
                email=account.email, server_id=server.id
            ).first()
        if user is None:
            from app.services.expiry import calculate_user_expiry

            user = client._create_user_with_identity_linking(
                {
                    "username": account.username,
                    "email": account.email,
                    "token": token,
                    "code": invite.code,
                    "expires": calculate_user_expiry(invite, server.id),
                    "server_id": server.id,
                }
            )
        user.code = invite.code
        user.set_accessible_libraries([lib.name for lib in libraries])
        db.session.flush()
        mark_server_used(invite, server.id, user, log_details=False)
        row.status = "completed"
        row.redemption_stage = "complete"
        row.updated_at = datetime.now(UTC)
        db.session.commit()
        from app.services.notifications import notify

        with suppress(Exception):
            notify(
                "User Joined",
                "A Plex requester completed onboarding.",
                "tada",
                event_type="user_joined",
            )
        return invite.code
    except Exception:
        db.session.rollback()
        row = _get_request(row.id)
        row.status = "needs_review"
        row.updated_at = datetime.now(UTC)
        db.session.commit()
        # Exceptions from Plex may contain account credentials or response bodies.
        raise ValueError(
            "Plex setup could not be confirmed. The administrator can review and recover this request."
        ) from None


def bound_request(invitation: Invitation):
    row = AccessRequest.query.filter_by(invitation_id=invitation.id).first()
    if row is None or row.plex_account_id != invitation.plex_account_id:
        raise ValueError("The approved request is unavailable.")
    return row
