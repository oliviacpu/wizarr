# Guide new Plex users through requesting access

Status: implemented on the fork feature branch, 2026-10-17. This document records
the design; `FORK.md` describes the shipped change and the PR records validation.

## Problem

The public request page assumes the visitor already has a Plex account. It asks
for an identifier and offers **Sign in with Plex and submit**, but gives a new
user no explanation of account creation or how to return afterward. The service
also rejects unconfirmed accounts with the same generic message used for other
verification failures.

## Requester experience

1. Start with **Do you have a Plex account?** Explain that a personal Plex
   account identifies who is requesting access, and that the administrator
   reviews the request before granting access.
2. **I have an account** continues to the existing request form.
3. **I need an account** shows a short setup step:
   - Create a free account on Plex using **Create a Plex account**.
   - Finish signup and confirm the account's email if Plex requires it.
   - Return to this tab and choose **I have my account—continue**.
   Open Plex's official signup page in a separate tab, announce that behavior,
   and keep the request tab and any entered form values intact. Keep an
   **I already have an account** route back from these instructions.
4. Show the current Plex email/username and optional name fields. Explain that
   the identifier must match the account used to sign in. The existing Plex
   sign-in submits the verified request and opens its private status page.
5. Keep **Check an existing request** available throughout the account step.

Suggested setup copy:

> Create your own free Plex account. After signing up, confirm your email if
> Plex asks, then return here to request access. Creating an account does not
> give you access to this server; an administrator still needs to approve you.

Account creation being free is separate from Plex's playback/subscription
requirements. Do not describe all Plex use as free.

## Implementation

- Add the account choice and setup instructions to
  `app/templates/access_requests/public.html`, using the existing layout,
  translation functions and accessible controls. Keep keyboard focus and back
  navigation predictable; returning to the form must preserve entered values.
- Use `https://www.plex.tv/sign-up/` for the signup link, with
  `target="_blank"` and `rel="noopener noreferrer"`. Confirm the official
  destination during implementation. Describe returning to the original tab;
  do not depend on an undocumented signup redirect or automatic return.
- In `app/static/js/access-requests.js`, start the existing PIN flow only when
  the ready requester submits the form. Signup and email confirmation must not
  consume its five-minute window. Preserve the current OAuth state cleanup.
  Opening or returning from signup must not submit a request automatically.
- In `app/services/access_requests.py`, distinguish an authenticated account
  that still needs email confirmation from other verification failures. Show
  **Confirm your email in Plex, then return and try again** without exposing
  account details, tokens or upstream exception text. Other failures retain a
  safe retry message. Preserve the confirmed, non-guest account requirement.
- Retain the existing identifier matching, account binding, private status
  access, duplicates, CSRF checks, rate limits and manual approval. This is a
  small UI/verification-message change with the existing database and invitation
  machinery.
- Update `docs/plex-access-requests.md` and add an implemented entry in
  `FORK.md` with the final behavior and validation when the feature lands.

## Acceptance checks

- An existing user can continue to the form and complete the current request
  flow without visiting signup.
- A new user sees signup, email-confirmation and return instructions before
  leaving Wizarr. The official link opens separately; the original form stays
  usable with its values intact. Changing the account-choice selection does
  not create a request, invitation, share or OAuth PIN.
- An authenticated but unconfirmed account gets actionable guidance and
  creates no request. Retrying after confirmation creates one pending request;
  repeat submission still returns that same account's request.
- Cancelled or expired sign-in can be retried without losing the form or
  bypassing identity checks. A mismatched identifier remains refused.
- Extend `tests/e2e/test_access_requests_e2e.py` for both branches, keyboard
  navigation, return behavior and desktop/mobile layouts. Simulate the external
  signup destination; tests must not create real Plex accounts.
- Extend `tests/test_access_requests.py` for the safe unconfirmed-account
  response and successful retry. Retain the existing decision/redemption
  regression checks, including rejection and wrong-account refusal.
- Run the relevant existing lint, template, JavaScript/build and request tests.
  Visually inspect both branches at desktop and phone widths.

## Live verification and delivery

Plex's official support describes free account creation and its signup entry
point: [Plex Accounts](https://support.plex.tv/articles/201862428-plex-accounts/).
The official signup URL is [Sign Up](https://www.plex.tv/sign-up/).
The browser flow, email confirmation and subsequent PIN sign-in still need
verification with a newly created test account. Do not promise automatic return
from signup before that behavior is verified.

Deliver this as a focused PR against the fork's `main`. After checks, load an
image built from the PR commit into the existing test instance while preserving
its data. The release workflow continues to require a dated tag on `main`;
the test image does not advance the release channel. Record final validation
in the PR and `FORK.md`.
