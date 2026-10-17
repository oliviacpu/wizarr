# Public Plex access requests

Requests are disabled on new and existing installations. Apply the normal
database migrations (`flask --app run db upgrade`) before running the new code.
In Wizarr's admin interface, open **Requests**, enable **Accept public Plex access
requests**, and save. Publish the public page, for example:

`https://media.example.com/request-access`

Use HTTPS and Wizarr's normal persistent database and session directories.
Keep `/access-requests` and the other admin paths behind normal admin
authentication. Public routes live under `/request-access`; an external login
gateway must permit those paths without granting access to admin routes.

## Request and decision

The requester enters their Plex username or email and optionally identifies
themselves. Plex sign-in confirms which account is requesting access; it does
not grant server access. The identifier must match the signed-in account.
Pending requests appear in **Requests**, with the entered value and verified
Plex username, email and account ID shown separately.

Approve by selecting a verified Plex server and at least one enabled library,
or reject. Approval creates one account-bound Wizarr invitation. The requester
then completes Plex sign-in from the private status page, accepts the share and
continues through Wizarr's onboarding wizard. Pending and rejected requests
never create a share. Disabling public requests stops new submissions; previously
submitted requests can still check status and finish an approval.

There is one request per Plex account, including rejected and completed requests.
A duplicate submission returns that authenticated account's existing request;
it does not reset a rejection or create another invitation. Losing a private
link does not require a second request: **Find a request with Plex sign-in**
restores the account's status session.

The initial private status link contains an unguessable fragment. Anyone with
that link can read status, so keep it private. Receiving access always requires
the approved Plex account, even when someone else knows the status link or
invitation code. Tokens are not sent as URL query parameters.

## Recovery

Plex and SQLite cannot commit a remote share in one transaction. On any uncertain
result, the request shows **Setup needs confirmation** rather than claiming
failure means no access exists. Automatic retries are blocked.

In **Requests**, use **Reconcile with Plex and allow retry**. Recovery reads fresh
accepted and pending shares for the exact account ID and server. If it finds the
approved library IDs, the requester can resume acceptance/local setup without
sending another share. If no share exists, a new sharing attempt is allowed using
the same Wizarr invitation. A mismatched or unreadable share stays in review;
correct its library selection in Plex and reconcile again. An interrupted
`redeeming` or `recovering` operation must be at least five minutes old before
an administrator can reconcile it.

Do not edit a request invitation into an unlimited invitation, Plex Home invite,
another server, broader libraries or extra permissions. Those changes fail
closed at redemption. Scan renamed or changed libraries before reviewing a
request. Duplicate library titles are refused during provisioning because the
upstream Plex adapter shares by title.

## Rate limits, sessions and notifications

All mutations use Flask-WTF CSRF validation and admin decisions also require
Wizarr's existing Flask-Login authorization. Public submissions are limited to
5/minute and 30/hour per client address; status-claim and sign-in endpoints have
their own limits. These limits remain enabled when upstream's invitation limiter
is disabled. Body sizes, field lengths and control characters are constrained.

The default limiter storage is per-process memory, appropriate to Wizarr's
default single worker. For multiple processes or replicas, configure a shared
Flask-Limiter storage backend through `RATELIMIT_STORAGE_URI` and install its
required backend dependencies. Storage errors fail closed. Configure
`TRUST_PROXY_HEADERS` and `PROXY_FIX_X_FOR` only for a trusted proxy chain;
otherwise forwarded client addresses must not be trusted. Sessions must share
the same persistent session directory and secret key across workers.

Existing notification agents can opt into **Access request** in their event
settings. The notification only says a request is waiting in Wizarr. Requesters
check their private page; there is no separate email delivery dependency.

## Verification before relying on live Plex access

Automated tests simulate Plex at its external boundary. They verify submission,
rejection, scope selection, intended-account redemption, wrong-account refusal,
CSRF/authentication, private links, duplicates, concurrent decisions, partial
failures and admin reconciliation. Browser tests exercise desktop and phone
layouts. SQLite migration tests cover fresh installation and an upgrade and
downgrade with an existing invitation and server association.

Before using this with real requesters, verify with disposable Plex accounts:

1. The Plex PIN sign-in and return work on desktop and mobile, including any
   reverse-proxy path prefix; cancellation/expiry can be retried.
2. A previously unconnected confirmed account can receive and accept a share,
   and can see only the selected libraries on the selected server.
3. A different signed-in Plex account cannot redeem the request, status link or
   invitation through any route. Pending/rejected requests create no access.
4. Plex's pending/accepted share lists expose the expected account ID, machine
   identifier and global library section IDs during recovery. Check interrupted
   sharing, acceptance and delayed resource visibility.
5. A completed request reaches the expected Wizarr wizard and selected server's
   links. Verify configured notification delivery independently.

No live Plex accounts, deployment changes or release tags are required to run
the automated suite.
