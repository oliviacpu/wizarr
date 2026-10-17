# About this fork

Personal fork of [wizarrrr/wizarr](https://github.com/wizarrrr/wizarr).
Upstream history, repository instructions, documentation and the MIT license
in `LICENSE.md` are retained. This fork does not include the private Plus submodule.

- **Base:** stable [`v2026.10.1`](https://github.com/wizarrrr/wizarr/releases/tag/v2026.10.1),
  commit `936143363897efd3ca68568fdf6fdd9a0d63f559`, released 2026-10-12.
- **Remotes:** `origin` is `https://github.com/oliviacpu/wizarr.git`;
  `upstream` is `https://github.com/wizarrrr/wizarr.git`.
- **Updates:** fetch upstream tags, merge the chosen stable tag into an update
  branch, run the checks, and open a PR against this fork's `main`. Preserve the
  patch commits rather than replacing upstream history. Update the base here and
  the image's `APP_VERSION` build argument in `.github/workflows/fork.yml`.
- **Patches:** keep a change, its tests and its documentation in the same commit.
  Feature and fork-build changes are separate commits so the feature can be
  proposed upstream independently. No upstream PR is part of this change.

## Changes

### Public Plex access requests with manual approval (2026-10-16)

- **Problem:** a visitor needs a way to request access before an administrator
  creates an invitation.
- **Change:** off by default. Administrators enable public requests in the new
  **Requests** tab. `/request-access` accepts an entered Plex email or username
  and optional name, then requires Plex sign-in before saving a pending request.
  The entered text and authenticated account are shown separately in admin UI.
- **Identity:** no global username lookup is claimed or attempted. PlexAPI's
  `MyPlexAccount.user()` only searches connected users. A fresh token resolves
  its own account; a confirmed, non-guest account is required and the entered
  identifier must match that account's username or email. Approval binds the
  invitation to the immutable numeric account ID, not entered text.
  See [PlexAPI's account and user API](https://python-plexapi.readthedocs.io/en/latest/modules/myplex.html).
- **Approval:** select one verified Plex server and explicit enabled libraries.
  A conditional database update and `create_invite(commit=False)` make the
  request decision, invitation and scope one transaction. Concurrent approvals,
  rejection races and retries cannot create a second invitation. Rejection and
  pending status create neither invitations nor Plex shares.
- **Redemption:** a private status page signs the requester into Plex again and
  verifies the approved account before any provisioning. It reuses Wizarr's
  invitation creation, Plex sharing adapter, identity linking, invitation usage
  and onboarding wizard. Scope changes or missing libraries fail closed; empty
  selection never falls back to all libraries. Legacy redemption paths refuse
  request-bound invitations.
- **Privacy:** a 256-bit random status capability travels in a URL fragment,
  is exchanged by CSRF-protected POST for a server-side session and is stored
  only as a hash in the database. Plex sign-in can also restore status. Status
  responses are private/no-store and suppress referrers. OAuth tokens never go
  into URLs, web storage, request rows or error responses.
- **Failure handling:** provisioning has durable checkpoints. An uncertain Plex
  result becomes `needs_review`, with no automatic remote retry. Admin recovery
  reads the account's accepted and pending shares and verifies the exact library
  IDs before allowing the existing invitation to resume. A crashed operation
  can be reconciled after five minutes. Recovery failures remain reviewable.
  Partial Plex access may already exist and is stated explicitly on the status
  page; recovery does not claim to undo a successful remote operation.
- **Notifications:** existing agents may subscribe to the new **Access request**
  event. Notifications contain no identifiers or private links. The status page
  is the requester notification mechanism; no new email service is introduced.
- **Checks:** service/route tests, real SQLite concurrency, failure and recovery
  tests, migration upgrade/downgrade tests, and desktop/mobile browser tests with
  a simulated Plex provider. Build-policy tests cover immutable images, registry
  failures, moved tags, repository guards and stale `:latest` builds.
- **Upstream:** not sent.

See [the operator guide](docs/plex-access-requests.md) for configuration,
recovery and the remaining live Plex checks.
