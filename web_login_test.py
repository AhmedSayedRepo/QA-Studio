"""Smoke-test the WebView login window standalone (run on Windows).

    pip install pywebview pythonnet
    python web_login_test.py

It opens the same 1120x760 login window the app will use, lets you try the
theme toggle / sign-in form, and prints what the bridge returned.
"""
import web_login

if __name__ == "__main__":
    res = web_login.run_login(1120, 760)
    print("LOGIN RESULT:", res)
