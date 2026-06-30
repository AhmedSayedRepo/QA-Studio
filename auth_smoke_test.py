"""auth_smoke_test.py — quick check that SUPABASE_URL + SUPABASE_ANON_KEY work.

Run from a terminal where the env vars are set (reopen the terminal after `setx`):
    python auth_smoke_test.py                # config + reachability check only
    python auth_smoke_test.py you@email.com  # also do a real sign-up round-trip

The reachability check is read-only (hits /auth/v1/settings) — it does NOT create
any users. Pass an email to additionally exercise sign_up (this will create a user
and send a confirmation email to that address).
"""
import os
import sys
import requests

import auth_supabase as auth


def main():
    print("configured():", auth.configured())
    if not auth.configured():
        print("  -> SUPABASE_URL / SUPABASE_ANON_KEY not visible. Open a NEW "
              "terminal after `setx`, or set them in auth_supabase.py.")
        return

    url = auth.SUPABASE_URL
    key = auth.SUPABASE_ANON_KEY
    print("URL :", url)
    print("key :", key[:14] + "…")

    # 1) read-only reachability + key validity (no users created)
    try:
        r = requests.get(f"{url}/auth/v1/settings",
                         headers={"apikey": key}, timeout=15)
        print("settings endpoint:", r.status_code,
              "(200 = URL + key are valid and reachable)")
        if r.status_code != 200:
            print("  body:", r.text[:300])
            return
    except Exception as ex:
        print("  network error reaching the project:", ex)
        return

    # 2) optional full round-trip if an email was provided
    if len(sys.argv) > 1:
        email = sys.argv[1]
        ok, msg, user = auth.sign_up(email, "Test-Passw0rd!", name="Smoke Test")
        print("sign_up:", ok, "|", msg)
        ok, msg, user = auth.sign_in(email, "Test-Passw0rd!")
        print("sign_in:", ok, "|", msg,
              "| (expected to fail until you confirm the email)")

    print("\nConfig looks good." if True else "")


if __name__ == "__main__":
    main()
