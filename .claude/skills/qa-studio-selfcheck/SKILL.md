---
name: qa-studio-selfcheck
description: >-
  Use to validate QA Studio explorer/locator changes against saved page HTML
  fixtures WITHOUT a live browser, Keycloak, or Azure. Runs the real engine.py
  functions (_classify_case, compile/_intents_from_raw_steps, _rank_candidates)
  over a fixture's DOM and reports, per intent, which element it binds to, the
  confidence, and any guesses — so a wrong bind is caught before it hits the live
  site. Trigger after changing the explorer, ranking, classification, harvest, or
  prompts, or whenever the user shares a page fixture + test case to check.
---

# QA Studio — explorer self-check

The explorer was historically validated only by `py_compile` + unit tests, never
observed against the real DOM. This harness closes that gap offline: it builds
element dicts from a saved HTML fixture (mimicking `_HARVEST_JS` + the error
harvest), then runs the actual `engine.py` decision functions over them.

## When to run

After any change to: `_HARVEST_JS`, `_rank_candidates`, `_classify_case`,
`compile_test_case`, `_intents_from_raw_steps`, `_norm`, `_kind_matches`, or the
step-generation prompts. Also whenever the user pastes a page snapshot and a test
case and asks "would this bind correctly?".

## How to run

```bash
python scripts/selfcheck.py --engine /path/to/engine.py \
    --fixture /path/to/login-error.html \
    --case /path/to/login-error.case.json
```

- `--engine` path to the engine.py under test (default: ./engine.py).
- `--fixture` the saved HTML (see the `qa-studio-fixtures` skill for capture).
- `--case` a test case JSON `{"title","steps":[{precondition,action,expected}]}`.
  If omitted, the script lists every interactive element it harvested from the
  fixture (useful for eyeballing what the DOM exposes).
- `--ai` (optional flag) call the real `compile_test_case` (needs a configured
  provider/key). Without it, the harness uses `_intents_from_raw_steps`, so it
  runs fully offline and deterministically.

## What it reports

For the case: the classification (`negative_login`/`presence`/`interaction`), the
compiled intents, and for each action/assertion intent the top candidate elements
with scores, the chosen element, and a flag when it would fall through to a
**guess** (score 0 / no candidate). That guess flag is the signal to fix
keywords, the harvest, or the ranking before deploying.

## Limits (be honest about them)

- Static HTML can't tell `visible` (layout) or run JS, so a fixture should be the
  post-JS DOM in the state you care about (DevTools → Copy outerHTML). Elements
  are assumed visible.
- `css`/`xpath` aren't computed (the harness doesn't click), so it validates
  BINDING and CLASSIFICATION, not the interception-proof `_act` retry ladder —
  that still needs a real run.
- This complements, not replaces, a live run. Green here = the right element is
  findable from its text/aria/role; it does not guarantee timing/overlay success.
