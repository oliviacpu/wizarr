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

## Images and Actions

`.github/workflows/fork.yml` runs the locked Python and static builds, Ruff,
template formatting, targeted type checks and the test suite, including Chromium
browser tests and disposable SQLite migrations. Upstream's broad type check is
advisory; the new service and routes are checked as a required step.

Only a dated tag such as `oliviacpu-2026.10.16` (or `.2` for another release that
day), whose commit is on this fork's `main`, publishes
`ghcr.io/oliviacpu/wizarr:<tag>` for **linux/amd64** after those checks pass.
Pushes to `main` and PRs test without publishing. Manual dispatch publishes only
when run on a valid release tag. Pushing a release tag is a release operation.

The image job is serialized across all tags. `scripts/fork-image.sh` checks the
remote tag and existing registry image: an existing tag at the same source commit
is reused, and a different revision or a registry error fails the job. Tagged
images are never deliberately overwritten. After publishing, a fresh tag fetch
must still identify this tag as the newest dated `oliviacpu-*` tag before `:latest`
can move. An older build or rerun cannot move `:latest` backward. Deploy a dated
image or digest to select an immutable version.

Inherited publishing, release, manifest, translation and notification jobs have
an exact `github.repository == 'wizarrrr/wizarr'` guard. They retain their upstream
source but cannot execute in this fork. Review new upstream workflows on updates.
The fork workflow requires no upstream publishing credentials.

## Changes

### Bound streamed request bodies (2026-10-16)

- **Problem:** a chunked public POST may omit Content-Length, so a header-only
  size check cannot bound form parsing.
- **Change:** apply Flask's per-request 8 KiB body limit before reading the form,
  including requests without a length header. Infrastructure errors during
  provisioning also return a generic response without exception contents.
- **Tests:** streamed oversized-body rejection with a bounded stream read, plus
  sanitized unexpected provisioning failures in `tests/test_access_requests.py`.
- **Upstream:** not sent.

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

### Fork-only tested container publishing (2026-10-16)

- **Problem:** upstream Actions publish upstream builds, run bots and assume
  upstream credentials; the fork needs a separate release path.
- **Change:** guarded upstream jobs, dedicated tests-before-image workflow,
  dated immutable tags, linux/amd64 builds and serialized newest-tag promotion.
- **Tests:** `tests/test_fork_workflow.py` exercises the publishing script against
  disposable local Git repositories and a simulated registry CLI. No registry
  image, GitHub release tag or deployment is created by these tests.
- **Upstream:** fork configuration only.
