import logging
import os

from flask import Blueprint, redirect, render_template, request, session, url_for
from flask_babel import _
from flask_login import login_required, login_user, logout_user
from werkzeug.security import check_password_hash

from app.extensions import db, limiter
from app.models import AdminAccount, AdminUser, Settings
from app.services import cloudflare_access

from .redirects import login_destination

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    next_url = login_destination(request.form.get("next") or request.args.get("next"))
    # Cloudflare Access mode takes precedence over every other login method,
    # including DISABLE_BUILTIN_AUTH: no valid Access token, no admin session.
    if cloudflare_access.enabled():
        claims = cloudflare_access.verified_claims()
        if claims is None:
            return (
                _("Sign in through Cloudflare Access to reach the admin pages."),
                403,
            )
        logging.info(
            "Admin login via Cloudflare Access: %s",
            claims.get("email") or claims.get("common_name") or "unknown",
        )
        login_user(AdminUser())
        return redirect(next_url)

    if os.getenv("DISABLE_BUILTIN_AUTH", "").lower() == "true":
        login_user(AdminUser(), remember=bool(request.form.get("remember")))
        return redirect(next_url)

    # Pre-compute shared template context
    from app.models import LDAPConfiguration, WebAuthnCredential

    has_passkeys = WebAuthnCredential.query.first() is not None
    ldap_config = LDAPConfiguration.query.first()
    ldap_enabled = bool(
        ldap_config and ldap_config.enabled and ldap_config.allow_admin_bind
    )
    media_server_url = request.cookies.get("wizarr_media_server_url")

    if request.method in ("GET", "HEAD"):
        return render_template(
            "login.html",
            next_url=next_url,
            has_passkeys=has_passkeys,
            ldap_enabled=ldap_enabled,
            media_server_url=media_server_url,
            error=request.args.get("error"),
        )

    username = request.form.get("username")
    password = request.form.get("password")
    auth_method = request.form.get("auth_method", "local")

    # ── Handle LDAP authentication ─────────────────────────────────────
    if auth_method == "ldap":
        from .ldap_auth import handle_ldap_login

        success, message = handle_ldap_login(username, password)
        if success:
            return redirect(next_url)

        return render_template(
            "login.html",
            next_url=next_url,
            error=message,
            has_passkeys=has_passkeys,
            ldap_enabled=ldap_enabled,
            media_server_url=media_server_url,
            selected_auth_method=auth_method,
        )

    # ── 1) Multi-admin accounts (preferred) ────────────────────────────
    if (
        account := AdminAccount.query.filter_by(username=username).first()
    ) and account.check_password(password):
        # Check if this account has passkeys registered - if so, require 2FA
        from app.models import WebAuthnCredential

        if WebAuthnCredential.query.filter_by(admin_account_id=account.id).first():
            # Store user in session for 2FA verification
            session["pending_2fa_user_id"] = account.id
            session["pending_2fa_remember"] = bool(request.form.get("remember"))
            session["pending_2fa_next"] = next_url
            return render_template(
                "login.html",
                show_2fa=True,
                username=username,
                has_passkeys=True,
                next_url=next_url,
            )
        # No passkeys, allow direct login
        login_user(account, remember=bool(request.form.get("remember")))
        return redirect(next_url)

    # fetch the stored admin credentials
    admin_username = (
        db.session.query(Settings.value).filter_by(key="admin_username").scalar()
    )
    admin_password_hash = (
        db.session.query(Settings.value).filter_by(key="admin_password").scalar()
    )

    if (
        username == admin_username
        and password
        and check_password_hash(admin_password_hash, password)
    ):
        # Legacy single-admin (Settings table)
        login_user(AdminUser(), remember=bool(request.form.get("remember")))
        return redirect(next_url)

    # Get IP address: prefer Cloudflare's header, then X-Forwarded-For, then remote_addr
    client_ip = (
        request.headers.get("CF-Connecting-IP")
        or (request.headers.get("X-Forwarded-For") or request.remote_addr or "")
        .split(",")[0]
        .strip()
    )

    # Log failed login with IP
    logging.warning(f"AUTH FAIL: Failed login for user '{username}' from {client_ip}")

    return render_template(
        "login.html",
        next_url=next_url,
        error=_("Invalid username or password"),
        has_passkeys=has_passkeys,
        ldap_enabled=ldap_enabled,
        media_server_url=media_server_url,
        selected_auth_method=auth_method,
    )


@auth_bp.route("/complete-2fa", methods=["POST"])
@limiter.limit("10 per minute")
def complete_2fa():
    """Complete 2FA authentication with passkey."""
    user_id = session.get("pending_2fa_user_id")
    remember = session.get("pending_2fa_remember", False)
    next_url = login_destination(session.get("pending_2fa_next"))

    if not user_id:
        # Check if there are any passkeys registered for error page
        from app.models import WebAuthnCredential

        has_passkeys = WebAuthnCredential.query.first() is not None
        return render_template(
            "login.html",
            next_url=next_url,
            error=_("No pending 2FA authentication"),
            has_passkeys=has_passkeys,
        )

    # Get the user account
    account = db.session.get(AdminAccount, user_id)
    if not account:
        session.pop("pending_2fa_user_id", None)
        session.pop("pending_2fa_remember", None)
        session.pop("pending_2fa_next", None)
        # Check if there are any passkeys registered for error page
        from app.models import WebAuthnCredential

        has_passkeys = WebAuthnCredential.query.first() is not None
        return render_template(
            "login.html",
            error=_("Authentication failed"),
            has_passkeys=has_passkeys,
            next_url=next_url,
        )

    # The actual WebAuthn verification will be handled by the existing WebAuthn route
    # This route is called after successful WebAuthn authentication
    session.pop("pending_2fa_user_id", None)
    session.pop("pending_2fa_remember", None)
    session.pop("pending_2fa_next", None)

    login_user(account, remember=remember)
    return redirect(next_url)


# ── Logout ────────────────────────────────────────────────────────────


@auth_bp.route("/logout", methods=["GET"])
@login_required
def logout():
    """Terminate session and redirect to login page."""
    logout_user()
    return redirect(url_for("auth.login"))
