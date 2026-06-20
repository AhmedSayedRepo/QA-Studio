---
name: qa-studio-fixtures
description: >-
  Use when capturing or organizing saved HTML page snapshots ("fixtures") of the
  QA Studio target site (login page, language-switcher, error/validation states)
  so explorer/locator changes can be validated offline without a live browser,
  Keycloak, or Azure. Trigger when the user wants to capture a page, add a
  fixture, or asks to test locator binding against the real DOM.
---

# QA Studio — page fixtures

The explorer's locator binding (`_rank_candidates`) depends entirely on the real
DOM shape: element text, aria-labels, ids, roles. The fastest way to validate a
change without spinning up Chrome + Keycloak + the test site is to save a few
HTML snapshots and run the `qa-studio-selfcheck` harness against them.

## What to capture

Capture the pages the explorer actually walks, in the states that matter:
- `login.html` — the Keycloak login form (fresh).
- `login-error.html` — the login page AFTER a bad submit (so the error/validation
  message node is present). Critical for validating negative-login error capture.
- `language-menu-open.html` — the post-login page with the language dropdown open
  (the element that kept getting clicked four times in the real run).
- Any other screen under test (e.g. an employee/movement screen).

## How to capture (no extra tooling)

1. Open the page in Chrome, get it into the exact state you want (e.g. submit a
   wrong password so the error shows, or open the language menu).
2. DevTools (F12) → Elements → right-click the `<html>` node → **Copy → Copy
   outerHTML**. Paste into a file. This keeps the live DOM (post-JS), which is
   what matters — "Save page as" or View-Source gives pre-JS HTML and is less
   useful.
3. Save it under this skill's `fixtures/` folder (or the repo at
   `tests/fixtures/`), named for the state, e.g. `login-error.html`.

Privacy: these are TEST-environment pages, but still scrub any real tokens,
session ids, emails, or PII from the saved HTML before committing. Never capture
production pages with real user data.

## Fixture + intents pairing

Alongside a fixture, optionally save the test case you expect to walk it, as
`<name>.case.json`:

```json
{
  "title": "التحقق من وجود ايقونه تغيير اللغه قي صفحة تسجيل الدخول",
  "steps": [
    {"precondition": "المستخدم على صفحة تسجيل الدخول", "action": "النقر على أيقونة تغيير اللغة", "expected": "تظهر قائمة اللغات"}
  ]
}
```

The harness will classify the case, compile (or fall back to) intents, and show
which fixture element each intent binds to and with what confidence — so you can
see a "guess" or a wrong bind before it ever runs against the live site.

## Keep fixtures fresh

Re-capture when the site's markup changes. A stale fixture validates against a
DOM that no longer exists. Date them in a comment at the top if helpful.
