# About this fork

Personal fork of [wizarrrr/wizarr](https://github.com/wizarrrr/wizarr).
Upstream history, repository instructions, documentation and the MIT license
in `LICENSE.md` are retained. This fork does not include the private Plus submodule.

- **Base:** stable [`v2026.10.1`](https://github.com/wizarrrr/wizarr/releases/tag/v2026.10.1),
  commit `936143363897efd3ca68568fdf6fdd9a0d63f559`, released 2026-10-12.
- **Preserved snapshot:** the initial upstream development tip remains on
  `upstream-snapshot` at `89da0aebd3e99bfb455f83288b08f399b589e14f`.
  `main` starts at the stable base above.
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
The application reports the upstream base version, so Wizarr's strict version
parser and upstream update check keep working. The fork release tag is recorded
in the immutable image tag and OCI version label instead of `APP_VERSION`.

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
The fork workflow requires no upstream publishing credentials. The eight
inherited publishing/release/bot workflows are also disabled in the fork
settings, including upstream CI that only publishes the `:dev` image. The
upstream test, lint and translation-verification workflows remain enabled in
settings; their repository guards prevent duplicate work on the fork PR.

## Changes

### Remove obsolete Plex controls and explain sharing permissions (2026-10-17)

- **Problem:** Plex Camera Upload was retired, and the inherited Live TV switch
  wrote the legacy `allowChannels` permission rather than modern tuner access.
- **Change:** remove both Plex controls from server, invitation and user forms;
  explain Downloads and Plex Home. Keep supported Jellyfin/Emby options and
  disable hidden controls when changing server types. Legacy database/API fields
  remain compatible, with no migration or changes to existing Plex shares.
- **Checks:** desktop/mobile browser coverage of create/edit forms, switching
  server types with checked values, invitation/user rendering and saved values;
  existing invitation, permissions and request-flow regressions.
- **Details:** [Plex sharing permissions](docs/plex-permissions.md).
- **Upstream:** not sent.

### Preserve the administrator's login destination (2026-10-17)

- **Problem:** the login form dropped `next`, and successful login always
  redirected to `/`. A public landing-page redirect then sent administrators
  back to the requester form.
- **Change:** preserve a local `next` path through password login, retries,
  LDAP, Cloudflare Access and passkey authentication, including two-factor
  completion. The default destination is `/admin`. External, ambiguous and
  control-character destinations fall back to the admin dashboard.
- **Tests:** rendered form/retry round trips, legacy credentials, LDAP,
  passkey/second-factor completion, unsafe destinations and browser login
  directly into Requests on desktop and mobile.
- **Upstream:** not sent.

### Guide new Plex accounts before requesting access (2026-10-17)

- **Problem:** the public request page assumed an existing Plex account, and
  unconfirmed accounts received generic verification failures.
- **Change:** ask whether the visitor has a Plex account. New users get official
  signup in a separate tab, email-confirmation and return instructions; existing
  users continue to the request form. Form values survive switching steps,
  OAuth returns, expiry and cancellation. An authenticated unconfirmed account
  gets a safe confirmation prompt. Late results from cancelled sign-ins cannot
  submit a request or replace the current error state. Step navigation is locked
  while the verified request is being submitted and restored after a failure.
- **Tests:** keyboard focus and desktop/mobile signup flows, preserving fields,
  no early PIN/request, confirmation then retry, expired sign-in recovery and
  late token/error refusal after cancellation. Existing approval, account
  binding, private status and manual provisioning tests remain required.
- **Design:** [account signup plan](docs/plans/plex-account-signup.md).
- **Upstream:** not sent.

### Keep admin CSRF stable during parallel dashboard loads (2026-10-16)

- **Problem:** an initial Home fragment can save an older session snapshot after
  Requests creates the session's first CSRF token. The displayed decision form
  then fails validation even though the administrator just opened it.
- **Change:** initialize the CSRF token in the admin shell before its parallel
  fragments load. CSRF checks remain required on every request decision.
- **Tests:** a deterministic overlapping Home/Requests load reproduces the old
  400 response and verifies successful approval after the fix. Browser tests
  also wait for and assert the approval response before refreshing status.
- **Upstream:** not sent.

### Exclude runtime data from container images (2026-10-16)

- **Problem:** running tests or a local instance creates `database/` with a
  session signing secret and session files. Docker's source copy could include
  those files in an image even though Git ignores them.
- **Change:** exclude database/data/session directories, secrets files, database
  files and logs from the container build context. Instances create their own
  secrets in their persistent data directory.
- **Checks:** build the linux/amd64 image with local runtime data present,
  inspect the resulting image for excluded files, and start it with fresh
  persistent storage to exercise migrations and health checks.
- **Upstream:** not sent.

### Preserve companion provisioning after bound redemption (2026-10-16)

- **Problem:** account-bound invitations bypassed the normal OAuth handler's
  provisioning of opted-in companion request systems.
- **Change:** the existing background provisioning is shared by ordinary invites
  and the request service. It runs only after a request completes, including
  after recovery, and completion retries do not schedule it again. Companion
  failures remain best effort and raw remote errors/bodies stay out of logs.
- **Tests:** both redemption entry points, opt-in behavior, repeat/concurrent
  completion, wrong accounts, failed setup/recovery, ordinary multi-server
  invitations, and sanitized lookup/thread/adapter failures.
- **Upstream:** not sent.

### Limit reconciliation to uncertain operations (2026-10-16)

- **Problem:** a late recovery POST could reopen a completed request or change
  a pending/rejected request into a provisioning error.
- **Change:** recovery accepts only `needs_review` or an interrupted
  `redeeming`/`recovering` operation at least five minutes old. The conditional
  update still checks the observed status and timestamp against concurrent edits.
- **Tests:** admin POSTs preserve pending, rejected, approved and completed
  requests without Plex calls; both in-progress states enforce the five-minute
  delay, and concurrent reconciliation has one winner.
- **Upstream:** not sent.

### Refresh retained users during request redemption (2026-10-16)

- **Problem:** a local user left behind by a removed Plex share or synchronization
  can contain a placeholder token, old username or expired/disabled state.
- **Change:** successful bound redemption refreshes the existing row from the
  verified account and current invitation, preserving its ID and local notes.
- **Tests:** code and email matches refresh credentials, identity fields, expiry,
  enabled state and libraries without creating another user.
- **Upstream:** not sent.

### Expire request invitations before migration downgrade (2026-10-16)

- **Problem:** older Wizarr code cannot enforce the account binding after its
  database column is removed.
- **Change:** downgrade expires account-bound invitations and clears unlimited
  redemption before dropping the binding. Ordinary invitations are preserved.
- **Tests:** the disposable database round trip preserves an ordinary invite
  and its server association, and invalidates a bound invitation on downgrade.
- **Upstream:** not sent.

### Refuse request invitations in legacy form redemption (2026-10-16)

- **Problem:** older media-specific forms and the multi-server password prompt
  can bypass the dedicated requester flow, especially if an administrator adds
  another server to an approved invitation.
- **Change:** the legacy invitation manager and password prompt refuse any
  account-bound request invitation before creating users or granting wizard access.
- **Tests:** all five registered legacy media forms refuse a bound invitation
  after a server-scope edit; GET and POST password-prompt bypasses are refused.
- **Upstream:** not sent.

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
  The release script uses standard runner utilities; it does not require ripgrep.
  `APP_VERSION` remains the upstream base: appending a dated fork tag produces
  an invalid version for Wizarr's update checker and can break the admin page.
- **Tests:** `tests/test_fork_workflow.py` exercises the publishing script against
  disposable local Git repositories and a simulated registry CLI. No registry
  image, GitHub release tag or deployment is created by these tests.
  The test environment uses a minimal system PATH to catch undeclared tools.
  A container-version test exercises Wizarr's actual upstream update checker.
- **Upstream:** fork configuration only.
