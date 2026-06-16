"""installer.py — QA Studio graphical installer.

Zero external dependencies (uses only Python's stdlib tkinter), so it can run
before any pip packages are installed. It:
  1. Verifies pip is available
  2. Installs everything in requirements.txt with a live progress bar
  3. Creates a Desktop shortcut with the app icon
  4. Offers to launch the app

Double-click  install.bat  (which just runs this with pythonw) — no console.
"""
import os
import sys
import threading
import subprocess
import tkinter as tk
from tkinter import ttk

APP_NAME = "QA Studio"
HERE = os.path.dirname(os.path.abspath(__file__))
REQ_FILE = os.path.join(HERE, "requirements.txt")
MAIN_PY = os.path.join(HERE, "main.py")
ICON_ICO = os.path.join(HERE, "app.ico")

# Brand palette (matches theme.py)
VIOLET = "#6A4DFF"
VIOLET_H = "#5A3DEE"
BG = "#FBFBFD"
CARD = "#FFFFFF"
INK = "#1B1A22"
INK_2 = "#74727E"
INK_3 = "#A3A1AD"
GREEN = "#1F9D57"
RED = "#E0474D"
BORDER = "#E8E7EE"


class Installer:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} — Installer")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        try:
            if os.path.exists(ICON_ICO):
                self.root.iconbitmap(ICON_ICO)
        except Exception:
            pass

        W, H = 470, 420
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//2}")
        self.root.minsize(W, H)

        # ── Header band ────────────────────────────────────────────────────
        header = tk.Frame(self.root, bg=VIOLET, height=92)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        tk.Label(header, text=f"  {APP_NAME}", bg=VIOLET, fg="white",
                 font=("Segoe UI", 20, "bold"), anchor="w").pack(fill="x", padx=22, pady=(20, 0))
        tk.Label(header, text="  AI-powered Azure DevOps test-case generator",
                 bg=VIOLET, fg="#D8CEFF", font=("Segoe UI", 10), anchor="w").pack(fill="x", padx=22)

        # ── Footer button (packed BEFORE body so it's always pinned bottom) ──
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", side="bottom", padx=26, pady=(0, 18))
        self.btn = tk.Button(footer, text="Install", bg=VIOLET, fg="white",
                             font=("Segoe UI", 11, "bold"), relief="flat",
                             cursor="hand2", bd=0, pady=11,
                             activebackground=VIOLET_H, activeforeground="white",
                             command=self.start_install)
        self.btn.pack(fill="x")
        self.btn.bind("<Enter>", lambda e: self.btn.config(bg=VIOLET_H))
        self.btn.bind("<Leave>", lambda e: self.btn.config(bg=VIOLET))

        # ── Body (fills remaining space between header and footer) ──────────
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, side="top", padx=26, pady=20)

        self.title_lbl = tk.Label(body, text="Ready to install",
                                  bg=BG, fg=INK, font=("Segoe UI", 13, "bold"), anchor="w")
        self.title_lbl.pack(fill="x")

        self.desc_lbl = tk.Label(body,
            text="This will install the required Python packages\nand add a Desktop shortcut to launch the app.",
            bg=BG, fg=INK_2, font=("Segoe UI", 10), anchor="w", justify="left")
        self.desc_lbl.pack(fill="x", pady=(6, 0))

        # progress bar (custom styled)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("QA.Horizontal.TProgressbar", troughcolor="#EAE8F4",
                        background=VIOLET, bordercolor="#EAE8F4",
                        lightcolor=VIOLET, darkcolor=VIOLET, thickness=10)

        self.bar = ttk.Progressbar(body, style="QA.Horizontal.TProgressbar",
                                   mode="determinate", maximum=100, length=408)
        self.bar.pack(fill="x", pady=(22, 6))

        self.status_lbl = tk.Label(body, text="", bg=BG, fg=INK_3,
                                   font=("Segoe UI", 9), anchor="w")
        self.status_lbl.pack(fill="x")

        # log box (scrolling install output, collapsed look)
        self.log = tk.Text(body, height=5, bg="#FCFCFE", fg=INK_3,
                           font=("Consolas", 8), relief="flat",
                           highlightthickness=1, highlightbackground=BORDER,
                           wrap="word", state="disabled", padx=8, pady=6)
        self.log.pack(fill="both", expand=True, pady=(14, 0))

    # ── UI helpers (always called on main thread via after) ────────────────
    def _set(self, pct=None, status=None, title=None, desc=None):
        if pct is not None:
            self.bar["value"] = pct
        if status is not None:
            self.status_lbl.config(text=status)
        if title is not None:
            self.title_lbl.config(text=title)
        if desc is not None:
            self.desc_lbl.config(text=desc)
        self.root.update_idletasks()

    def _log(self, line):
        self.log.config(state="normal")
        self.log.insert("end", line.rstrip() + "\n")
        self.log.see("end")
        self.log.config(state="disabled")
        self.root.update_idletasks()

    def ui(self, fn):
        self.root.after(0, fn)

    # ── Install flow (runs on a worker thread) ─────────────────────────────
    def start_install(self):
        self.btn.config(state="disabled", text="Installing…", bg=INK_3)
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        py = sys.executable  # the pythonw/python running this installer

        # 1. upgrade pip
        self.ui(lambda: self._set(pct=8, title="Installing dependencies",
                                  desc="Setting up the Python package manager…",
                                  status="Upgrading pip…"))
        self._run([py, "-m", "pip", "install", "--upgrade", "pip"])

        # 2. install requirements
        self.ui(lambda: self._set(pct=22, status="Installing packages (this can take a few minutes)…"))
        code = self._run([py, "-m", "pip", "install", "-r", REQ_FILE])
        if code != 0:
            self.ui(lambda: self._fail("Dependency installation failed.\n"
                                       "Check your internet connection and try again."))
            return

        # 3. desktop shortcut
        self.ui(lambda: self._set(pct=85, status="Creating Desktop shortcut…"))
        self._make_shortcut(py)

        # 4. done
        self.ui(lambda: self._done())

    def _run(self, cmd):
        """Run a subprocess, stream its output to the log, return exit code."""
        self.ui(lambda: self._log("> " + " ".join(os.path.basename(c) if i == 0 else c
                                                   for i, c in enumerate(cmd))))
        try:
            flags = 0
            if os.name == "nt":
                flags = 0x08000000  # CREATE_NO_WINDOW — no console flash
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    creationflags=flags)
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self.ui(lambda l=line: self._log(l))
                    # nudge the bar forward during the long pip step
                    if self.bar["value"] < 80:
                        self.ui(lambda: self.bar.step(0.6))
            proc.wait()
            return proc.returncode
        except Exception as e:
            self.ui(lambda: self._log(f"ERROR: {e}"))
            return 1

    def _make_shortcut(self, py):
        if os.name != "nt":
            return  # only Windows gets a .lnk
        try:
            pythonw = os.path.join(os.path.dirname(py), "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = py
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            lnk = os.path.join(desktop, f"{APP_NAME}.lnk")
            icon = ICON_ICO if os.path.exists(ICON_ICO) else pythonw
            ps = (
                "$ws = New-Object -ComObject WScript.Shell; "
                f"$s = $ws.CreateShortcut('{lnk}'); "
                f"$s.TargetPath = '{pythonw}'; "
                f"$s.Arguments = '\"{MAIN_PY}\"'; "
                f"$s.WorkingDirectory = '{HERE}'; "
                f"$s.IconLocation = '{icon}'; "
                f"$s.Description = '{APP_NAME} — AI Test Case Generator'; "
                "$s.Save()"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                          creationflags=0x08000000, check=False)
            self.ui(lambda: self._log("Desktop shortcut created."))
        except Exception as e:
            self.ui(lambda: self._log(f"Shortcut note: {e}"))

    def _done(self):
        self.bar["value"] = 100
        self._set(title="Installation complete  ✓",
                  desc="QA Studio is ready. A shortcut was added to your Desktop.",
                  status="")
        self.title_lbl.config(fg=GREEN)
        self.btn.config(state="normal", text="Launch QA Studio", bg=GREEN,
                       activebackground="#188044", command=self._launch)
        self.btn.bind("<Enter>", lambda e: self.btn.config(bg="#188044"))
        self.btn.bind("<Leave>", lambda e: self.btn.config(bg=GREEN))

    def _fail(self, msg):
        self._set(title="Installation failed", desc=msg, status="")
        self.title_lbl.config(fg=RED)
        self.btn.config(state="normal", text="Retry", bg=VIOLET,
                       command=self.start_install)

    def _launch(self):
        try:
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = sys.executable
            flags = 0x08000000 if os.name == "nt" else 0
            subprocess.Popen([pythonw, MAIN_PY], cwd=HERE, creationflags=flags)
        except Exception:
            pass
        self.root.after(400, self.root.destroy)


def _acquire_single_instance():
    """Prevent two installer windows from running at once. Returns a lock handle
    (kept alive) or None if another instance already holds the lock."""
    import tempfile
    lock_path = os.path.join(tempfile.gettempdir(), "qastudio_installer.lock")
    try:
        if os.name == "nt":
            # Exclusive create; fails if the file is held open by another instance
            try:
                fh = open(lock_path, "x")
            except FileExistsError:
                # Stale lock? try to remove and recreate
                try:
                    os.remove(lock_path)
                    fh = open(lock_path, "x")
                except Exception:
                    return None
            fh.write(str(os.getpid()))
            fh.flush()
            return fh
        else:
            import fcntl
            fh = open(lock_path, "w")
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
    except Exception:
        return None


def main():
    lock = _acquire_single_instance()
    if lock is None:
        # Another installer window is already open — don't open a second.
        return
    root = tk.Tk()
    inst = Installer(root)

    def _cleanup():
        try:
            import tempfile
            lock.close()
            os.remove(os.path.join(tempfile.gettempdir(), "qastudio_installer.lock"))
        except Exception:
            pass

    root.protocol("WM_DELETE_WINDOW", lambda: (_cleanup(), root.destroy()))
    try:
        root.mainloop()
    finally:
        _cleanup()


if __name__ == "__main__":
    main()