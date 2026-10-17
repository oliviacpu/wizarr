from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import login_required
from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import DataRequired, Length, Regexp

from app.extensions import db
from app.models import AccessRequest, MediaServer, Settings
from app.services import access_requests as service

access_requests_bp = Blueprint("access_requests", __name__)
# The upstream invitation limiter is disabled by default. Public requests always
# have their own limiter, using the same Flask-Limiter extension and storage API.
request_limiter = Limiter(key_func=get_remote_address, default_limits=[], enabled=True)


class RequestForm(FlaskForm):
    identifier = StringField(
        validators=[DataRequired(), Length(max=254), Regexp(r"^[^\s\x00-\x1f\x7f]+$")]
    )
    display_name = StringField(
        validators=[Length(max=100), Regexp(r"^[^\x00-\x1f\x7f]*$")]
    )
    oauth_token = StringField(validators=[DataRequired(), Length(max=1024)])


class TokenForm(FlaskForm):
    token = StringField(validators=[DataRequired(), Regexp(r"^[A-Za-z0-9_-]{43}$")])


class OAuthForm(FlaskForm):
    oauth_token = StringField(validators=[DataRequired(), Length(max=1024)])


@access_requests_bp.before_request
def limit_body():
    request.max_content_length = 8192
    if request.content_length and request.content_length > 8192:
        abort(413)


@access_requests_bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _validated(form):
    if not form.validate_on_submit():
        abort(400, "Invalid form or expired session. Refresh and try again.")


def _current_request():
    row = db.session.get(AccessRequest, session.get("access_request_id", 0))
    if row is None:
        abort(404)
    return row


def _onboarding_redirect(row):
    if row is None or row.status != "approved" or not row.invitation:
        return None
    from app.services.invitation_flow import InvitationFlowManager
    from app.services.invitation_flow.results import ProcessingStatus

    result = InvitationFlowManager().process_invitation_display(row.invitation.code)
    if result.status == ProcessingStatus.REDIRECT_REQUIRED:
        for key, value in (result.session_data or {}).items():
            session[key] = value
        return result.redirect_url
    return None


@access_requests_bp.route("/request-access", methods=["GET", "POST"])
@request_limiter.limit("5 per minute", methods=["POST"])
@request_limiter.limit("30 per hour", methods=["POST"])
def public_request():
    if not service.enabled():
        abort(404)
    if request.method == "GET":
        return render_template("access_requests/public.html", form=FlaskForm())
    form = RequestForm()
    _validated(form)
    try:
        account = service.verified_account(form.oauth_token.data)
        row, token = service.submit(
            form.identifier.data.strip(), form.display_name.data.strip(), account
        )
    except ValueError as error:
        return jsonify(error=str(error)), 400
    session["access_request_id"] = row.id
    destination = url_for("access_requests.status")
    if token:
        destination += "#" + token
    return jsonify(redirect=destination)


@access_requests_bp.route("/request-access/status")
@request_limiter.limit("60 per minute")
def status():
    row = db.session.get(AccessRequest, session.get("access_request_id", 0))
    if destination := _onboarding_redirect(row):
        return redirect(destination)
    return render_template("access_requests/status.html", row=row, form=FlaskForm())


@access_requests_bp.route("/request-access/claim", methods=["POST"])
@request_limiter.limit("10 per minute")
def claim():
    form = TokenForm()
    _validated(form)
    row = AccessRequest.query.filter_by(
        token_hash=service.token_hash(form.token.data)
    ).first()
    if row is None:
        abort(404)
    session["access_request_id"] = row.id
    return jsonify(redirect=url_for("access_requests.status"), request_id=row.id)


@access_requests_bp.route("/request-access/restore", methods=["POST"])
@request_limiter.limit("5 per minute")
def restore():
    form = OAuthForm()
    _validated(form)
    try:
        account = service.verified_account(form.oauth_token.data)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    row = AccessRequest.query.filter_by(plex_account_id=str(account.id)).first()
    if row is None:
        return jsonify(error="No request exists for this Plex account."), 404
    session["access_request_id"] = row.id
    return jsonify(redirect=url_for("access_requests.status"))


@access_requests_bp.route("/request-access/redeem", methods=["POST"])
@request_limiter.limit("5 per minute")
def redeem():
    form = OAuthForm()
    _validated(form)
    row = _current_request()
    request_id = row.id
    if destination := _onboarding_redirect(row):
        return jsonify(redirect=destination)
    try:
        account = service.verified_account(form.oauth_token.data)
        code = service.redeem(row, account, form.oauth_token.data)
    except ValueError as error:
        return jsonify(error=str(error)), 409
    except Exception:
        db.session.rollback()
        current_app.logger.warning(
            "Access request provisioning needs review (request id %s)", request_id
        )
        return jsonify(
            error="Setup could not be confirmed. Refresh status and contact the administrator."
        ), 503
    session["wizard_access"] = code
    session["invite_code"] = code
    return jsonify(redirect=url_for("wizard.start"))


@access_requests_bp.route("/access-requests")
@login_required
def admin():
    if not request.headers.get("HX-Request"):
        return redirect(url_for("admin.dashboard") + "#access-requests")
    page = max(1, request.args.get("page", 1, type=int))
    requests = AccessRequest.query.order_by(AccessRequest.created_at.desc()).paginate(
        page=page, per_page=25, error_out=False
    )
    servers = (
        MediaServer.query.filter_by(server_type="plex", verified=True)
        .order_by(MediaServer.name)
        .all()
    )
    return render_template(
        "access_requests/admin.html",
        requests=requests,
        servers=servers,
        enabled=service.enabled(),
        form=FlaskForm(),
    )


@access_requests_bp.route("/access-requests/settings", methods=["POST"])
@login_required
def settings():
    _validated(FlaskForm())
    row = Settings.query.filter_by(key="public_access_requests").first()
    if row is None:
        row = Settings(key="public_access_requests")
        db.session.add(row)
    row.value = "true" if request.form.get("enabled") == "true" else "false"
    db.session.commit()
    return redirect(url_for("admin.dashboard") + "#access-requests")


@access_requests_bp.route(
    "/access-requests/<int:request_id>/<action>", methods=["POST"]
)
@login_required
def decide(request_id, action):
    _validated(FlaskForm())
    if db.session.get(AccessRequest, request_id) is None:
        abort(404)
    try:
        if action == "approve":
            server_id = int(request.form.get("server_id", ""))
            libraries = [int(value) for value in request.form.getlist("libraries")]
            service.approve(request_id, server_id, sorted(set(libraries)))
        elif action == "reject":
            service.reject(request_id)
        elif action == "recover":
            service.recover(request_id)
        else:
            abort(404)
    except ValueError as error:
        return render_template(
            "access_requests/decision_error.html", error=str(error)
        ), 409
    except Exception:
        db.session.rollback()
        current_app.logger.warning(
            "Access request decision failed (request id %s)", request_id
        )
        return render_template(
            "access_requests/decision_error.html",
            error="The decision was not saved. Refresh and retry.",
        ), 503
    return redirect(url_for("admin.dashboard") + "#access-requests")
