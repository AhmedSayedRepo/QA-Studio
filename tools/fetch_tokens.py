#!/usr/bin/env python3
"""fetch_tokens.py — pre-fetch a per-user auth token for a load test.

Reads a CSV of users/passwords, logs each one in, grabs the bearer token (from
the JSON response body OR a response header), and writes a NEW CSV with a `token`
column added — ready to feed QA Studio's Data CSV so every virtual user carries
its own token.

Zero dependencies (Python 3.8+, standard library only). Concurrent, with retries.

──────────────────────────────────────────────────────────────────────────────
QUICK START
  1. Edit the CONFIG block below to match your login API.
  2. Run:  python fetch_tokens.py users.csv users_with_tokens.csv
  3. In QA Studio → Performance:
        • Data CSV      = users_with_tokens.csv
        • Auth header   = Bearer {{token}}
     Each virtual user then logs in with its own token.
──────────────────────────────────────────────────────────────────────────────

Copyright (c) 2026 Ahmed Sayed. All rights reserved.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import sys
import time
import urllib.error
import urllib.request

# ════════════════════════════════════════════════════════════════════════════
#  CONFIG — edit these to match YOUR login endpoint
# ════════════════════════════════════════════════════════════════════════════
LOGIN_URL   = "https://your-app.example.com/api/login"   # the login endpoint
HTTP_METHOD = "POST"

# How the login request sends credentials:
#   "json" -> body is JSON: {"email": "...", "password": "..."}
#   "form" -> body is form-encoded: email=...&password=...
BODY_FORMAT = "json"

# The FIELD NAMES your API expects in the login request body:
API_USER_FIELD = "email"
API_PASS_FIELD = "password"

# Which COLUMNS in your input CSV hold the username and password:
CSV_USER_COLUMN = "email"
CSV_PASS_COLUMN = "password"

# Where the token comes back. Set ONE of these:
#   TOKEN_JSON_PATH  -> dotted path into the JSON response, e.g. "data.access_token"
#                       or "token" or "auth.jwt". Leave "" if it's in a header.
#   TOKEN_HEADER     -> a response header name, e.g. "authorization". Leave "" if
#                       it's in the JSON body.
TOKEN_JSON_PATH = "access_token"
TOKEN_HEADER    = ""

# Extra headers to send on the login request (e.g. an API key, content-type):
EXTRA_HEADERS = {
    # "X-Api-Key": "…",
}

# The column name to WRITE the token into (matches Auth header {{token}}):
OUTPUT_TOKEN_COLUMN = "token"

# If the returned value already includes a scheme (e.g. "Bearer eyJ…"), the
# script strips a leading "Bearer " so the CSV holds the bare token; you then use
# `Bearer {{token}}` in QA Studio. Set to False to keep the value verbatim.
STRIP_BEARER_PREFIX = True

CONCURRENCY = 20       # parallel logins (lower this if you hit rate limits)
TIMEOUT_S   = 20
RETRIES     = 2        # retries per user on transient failure
PACING_MS   = 0        # small delay between requests per worker (rate-limit help)
# ════════════════════════════════════════════════════════════════════════════


def _dig(obj, path):
    """Follow a dotted path into nested dicts; return '' if any hop is missing."""
    cur = obj
    for part in [p for p in path.split(".") if p]:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return ""
    return cur if isinstance(cur, (str, int, float)) else ""


def _login_once(username, password):
    """Return (token, error). token is '' on failure; error explains why."""
    if BODY_FORMAT == "json":
        data = json.dumps({API_USER_FIELD: username, API_PASS_FIELD: password}).encode()
        content_type = "application/json"
    else:
        from urllib.parse import urlencode
        data = urlencode({API_USER_FIELD: username, API_PASS_FIELD: password}).encode()
        content_type = "application/x-www-form-urlencoded"

    headers = {"Content-Type": content_type, "Accept": "application/json"}
    headers.update(EXTRA_HEADERS)
    req = urllib.request.Request(LOGIN_URL, data=data, headers=headers, method=HTTP_METHOD)

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = resp.read().decode("utf-8", "replace")
            if TOKEN_HEADER:
                token = resp.headers.get(TOKEN_HEADER, "") or ""
            else:
                try:
                    token = str(_dig(json.loads(body), TOKEN_JSON_PATH))
                except Exception:
                    return "", f"couldn't parse JSON (HTTP {resp.status})"
            token = (token or "").strip()
            if STRIP_BEARER_PREFIX and token.lower().startswith("bearer "):
                token = token[7:].strip()
            if not token:
                return "", f"no token in response (HTTP {resp.status})"
            return token, ""
    except urllib.error.HTTPError as ex:
        return "", f"HTTP {ex.code}"
    except Exception as ex:
        return "", str(ex)[:80]


def _login_with_retry(username, password):
    err = "unknown"
    for attempt in range(RETRIES + 1):
        token, err = _login_once(username, password)
        if token:
            return token, ""
        if PACING_MS:
            time.sleep(PACING_MS / 1000.0)
        time.sleep(min(0.5 * (attempt + 1), 2.0))   # small backoff
    return "", err


def main():
    ap = argparse.ArgumentParser(description="Pre-fetch per-user auth tokens into a CSV.")
    ap.add_argument("input_csv", help="CSV with user + password columns")
    ap.add_argument("output_csv", help="where to write the CSV with a token column")
    ap.add_argument("--limit", type=int, default=0,
                    help="only process the first N rows (test your config first!)")
    args = ap.parse_args()

    with open(args.input_csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("Input CSV is empty.")
    for col in (CSV_USER_COLUMN, CSV_PASS_COLUMN):
        if col not in rows[0]:
            sys.exit(f"Input CSV has no '{col}' column. Columns: {list(rows[0].keys())}")
    if args.limit:
        rows = rows[: args.limit]

    print(f"Logging in {len(rows)} user(s) at {LOGIN_URL} "
          f"({CONCURRENCY} at a time)…")

    results = [None] * len(rows)

    def work(i):
        r = rows[i]
        token, err = _login_with_retry(r.get(CSV_USER_COLUMN, ""),
                                       r.get(CSV_PASS_COLUMN, ""))
        results[i] = (token, err)
        return i

    ok = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, CONCURRENCY)) as ex:
        for done, _ in enumerate(ex.map(work, range(len(rows))), 1):
            if done % 50 == 0 or done == len(rows):
                got = sum(1 for x in results if x and x[0])
                print(f"  …{done}/{len(rows)} done, {got} tokens so far")

    fieldnames = list(rows[0].keys())
    if OUTPUT_TOKEN_COLUMN not in fieldnames:
        fieldnames.append(OUTPUT_TOKEN_COLUMN)

    failures = []
    with open(args.output_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for i, r in enumerate(rows):
            token, err = results[i] or ("", "no result")
            if token:
                ok += 1
            else:
                failures.append((r.get(CSV_USER_COLUMN, ""), err))
            out = dict(r)
            out[OUTPUT_TOKEN_COLUMN] = token
            w.writerow(out)

    print(f"\nDone. {ok}/{len(rows)} tokens written to {args.output_csv}")
    if failures:
        print(f"{len(failures)} login(s) failed. First few:")
        for user, err in failures[:10]:
            print(f"  • {user}: {err}")
        print("Tip: run with --limit 3 first and check one login works before the full run.")


if __name__ == "__main__":
    main()
