"""installer.py — QA Studio graphical installer.

Zero external dependencies (uses only Python's stdlib tkinter), so it can run
before any pip packages are installed. It:
  1. Verifies pip is available
  2. Installs everything in requirements.txt with a live progress bar + %
  3. Creates a Desktop shortcut with the app icon
  4. Offers to launch the app
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
VIOLET    = "#6A4DFF"
VIOLET_H  = "#5A3DEE"
VIOLET_INK= "#5234E0"
VIOLET_SOFT="#EFEBFF"
BG        = "#F7F7FB"
CARD      = "#FFFFFF"
INK       = "#1B1A22"
INK_2     = "#6B6877"
INK_3     = "#A3A1AD"
GREEN     = "#1F9D57"
GREEN_H   = "#188044"
RED       = "#E0474D"
AMBER     = "#C2860C"
BORDER    = "#E8E7EE"
LOG_BG    = "#16141E"      # dark log surface
LOG_DIM   = "#8C8A99"
LOG_OK    = "#56D08A"
LOG_ERR   = "#FF7A80"
LOG_WARN  = "#E7B450"
LOG_INK   = "#D7D5E0"

# Weighted install phases for a smooth, believable percentage
SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


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

        W, H = 500, 470
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//2}")
        self.root.minsize(W, H)

        self._pct = 0.0
        self._target = 0.0
        self._spin_i = 0
        self._running = False

        # ── Header band ────────────────────────────────────────────────────
        header = tk.Frame(self.root, bg=VIOLET, height=96)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        tk.Label(header, text=f"  {APP_NAME}", bg=VIOLET, fg="white",
                 font=("Segoe UI Semibold", 21, "bold"), anchor="w").pack(fill="x", padx=24, pady=(22, 0))
        tk.Label(header, text="  AI-powered Azure DevOps test-case generator",
                 bg=VIOLET, fg="#D9CEFF", font=("Segoe UI", 10), anchor="w").pack(fill="x", padx=24)

        # ── Footer button (pinned bottom; packed before body) ───────────────
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", side="bottom", padx=28, pady=(0, 20))
        self.btn = tk.Button(footer, text="Install", bg=VIOLET, fg="white",
                             font=("Segoe UI Semibold", 11, "bold"), relief="flat",
                             cursor="hand2", bd=0, pady=12,
                             activebackground=VIOLET_H, activeforeground="white",
                             command=self.start_install)
        self.btn.pack(fill="x")
        self.btn.bind("<Enter>", lambda e: self.btn.config(bg=VIOLET_H) if self.btn["state"] == "normal" else None)
        self.btn.bind("<Leave>", lambda e: self.btn.config(bg=self._btn_idle) if self.btn["state"] == "normal" else None)
        self._btn_idle = VIOLET

        # ── Body ────────────────────────────────────────────────────────────
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, side="top", padx=28, pady=(20, 6))

        # title row + spinner
        trow = tk.Frame(body, bg=BG)
        trow.pack(fill="x")
        self.title_lbl = tk.Label(trow, text="Ready to install", bg=BG, fg=INK,
                                  font=("Segoe UI Semibold", 14, "bold"), anchor="w")
        self.title_lbl.pack(side="left")
        self.spin_lbl = tk.Label(trow, text="", bg=BG, fg=VIOLET,
                                 font=("Consolas", 15, "bold"))
        self.spin_lbl.pack(side="right")

        self.desc_lbl = tk.Label(body,
            text="This will install the required Python packages and add a\nDesktop shortcut to launch the app.",
            bg=BG, fg=INK_2, font=("Segoe UI", 10), anchor="w", justify="left")
        self.desc_lbl.pack(fill="x", pady=(6, 0))

        # progress bar + percentage
        prow = tk.Frame(body, bg=BG)
        prow.pack(fill="x", pady=(20, 4))
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("QA.Horizontal.TProgressbar", troughcolor="#E7E4F2",
                        background=VIOLET, bordercolor="#E7E4F2",
                        lightcolor=VIOLET, darkcolor=VIOLET, thickness=12)
        self.bar = ttk.Progressbar(prow, style="QA.Horizontal.TProgressbar",
                                   mode="determinate", maximum=100, length=380)
        self.bar.pack(side="left", fill="x", expand=True)
        self.pct_lbl = tk.Label(prow, text="0%", bg=BG, fg=VIOLET_INK,
                                font=("Segoe UI Semibold", 11, "bold"), width=5, anchor="e")
        self.pct_lbl.pack(side="right", padx=(10, 0))

        self.status_lbl = tk.Label(body, text="", bg=BG, fg=INK_3,
                                   font=("Segoe UI", 9), anchor="w")
        self.status_lbl.pack(fill="x")

        # dark log surface
        wrap = tk.Frame(body, bg=LOG_BG, highlightthickness=1,
                        highlightbackground=BORDER)
        wrap.pack(fill="both", expand=True, pady=(14, 0))
        self.log = tk.Text(wrap, height=6, bg=LOG_BG, fg=LOG_INK,
                           font=("Consolas", 9), relief="flat", wrap="word",
                           state="disabled", padx=12, pady=10, bd=0,
                           insertbackground=LOG_INK)
        self.log.pack(fill="both", expand=True)
        for tag, col in (("dim", LOG_DIM), ("ok", LOG_OK), ("err", LOG_ERR),
                         ("warn", LOG_WARN), ("ink", LOG_INK)):
            self.log.tag_configure(tag, foreground=col)

        self._animate()  # start the smooth bar + spinner loop

    # ── animation loop (runs on main thread via after) ─────────────────────
    def _animate(self):
        # ease the displayed percentage toward the target
        if self._pct < self._target:
            self._pct = min(self._target, self._pct + max(0.4, (self._target - self._pct) * 0.18))
        self.bar["value"] = self._pct
        self.pct_lbl.config(text=f"{int(round(self._pct))}%")
        if self._running:
            self._spin_i = (self._spin_i + 1) % len(SPIN)
            self.spin_lbl.config(text=SPIN[self._spin_i])
        self.root.after(60, self._animate)

    # ── UI helpers ──────────────────────────────────────────────────────────
    def _set(self, target=None, status=None, title=None, desc=None):
        if target is not None:
            self._target = float(target)
        if status is not None:
            self.status_lbl.config(text=status)
        if title is not None:
            self.title_lbl.config(text=title)
        if desc is not None:
            self.desc_lbl.config(text=desc)

    def _log(self, line, tag="ink"):
        self.log.config(state="normal")
        self.log.insert("end", line.rstrip() + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def ui(self, fn):
        self.root.after(0, fn)

    # ── Install flow (worker thread) ─────────────────────────────────────────
    def start_install(self):
        self._running = True
        self.btn.config(state="disabled", text="Installing…", bg=INK_3,
                       disabledforeground="white")
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        py = sys.executable

        self.ui(lambda: self._set(target=10, title="Installing dependencies",
                                  desc="Setting up the Python package manager…",
                                  status="Upgrading pip…"))
        self._run([py, "-m", "pip", "install", "--upgrade", "pip"], 10, 22)

        self.ui(lambda: self._set(target=30, status="Installing packages — this can take a few minutes…"))
        code = self._run([py, "-m", "pip", "install", "-r", REQ_FILE], 30, 88)
        if code != 0:
            self.ui(lambda: self._fail("Dependency installation failed.\n"
                                       "Check your internet connection and try again."))
            return

        self.ui(lambda: self._set(target=94, status="Creating Desktop shortcut…"))
        self._make_shortcut(py)

        self.ui(lambda: self._set(target=100, status=""))
        self.ui(self._done)

    def _run(self, cmd, lo, hi):
        """Stream a subprocess to the log; nudge target between lo..hi."""
        nice = " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd))
        self.ui(lambda: self._log("> " + nice, "dim"))
        try:
            flags = 0x08000000 if os.name == "nt" else 0
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    creationflags=flags)
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                low = line.lower()
                tag = "dim"
                if "error" in low or "failed" in low:
                    tag = "err"
                elif "warning" in low or "not on path" in low:
                    tag = "warn"
                elif "successfully installed" in low or "satisfied" in low:
                    tag = "ok"
                self.ui(lambda l=line, t=tag: self._log(l, t))
                # creep the target forward within the band
                if self._target < hi:
                    self.ui(lambda: setattr(self, "_target", min(hi, self._target + 0.8)))
            proc.wait()
            return proc.returncode
        except Exception as e:
            self.ui(lambda: self._log(f"ERROR: {e}", "err"))
            return 1

    def _make_shortcut(self, py):
        if os.name != "nt":
            return
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
            self.ui(lambda: self._log("Desktop shortcut created.", "ok"))
        except Exception as e:
            self.ui(lambda: self._log(f"Shortcut note: {e}", "warn"))

    def _done(self):
        self._running = False
        self._target = 100
        self.spin_lbl.config(text="✓", fg=GREEN)
        self._set(title="Installation complete", status="",
                  desc="QA Studio is ready. A shortcut was added to your Desktop.")
        self.title_lbl.config(fg=GREEN)
        self._btn_idle = GREEN
        self.btn.config(state="normal", text="Launch QA Studio", bg=GREEN,
                       activebackground=GREEN_H)
        self.btn.config(command=self._launch)

    def _fail(self, msg):
        self._running = False
        self.spin_lbl.config(text="✕", fg=RED)
        self._set(title="Installation failed", desc=msg, status="")
        self.title_lbl.config(fg=RED)
        self._btn_idle = VIOLET
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
    import tempfile
    lock_path = os.path.join(tempfile.gettempdir(), "qastudio_installer.lock")
    try:
        if os.name == "nt":
            try:
                fh = open(lock_path, "x")
            except FileExistsError:
                try:
                    os.remove(lock_path); fh = open(lock_path, "x")
                except Exception:
                    return None
            fh.write(str(os.getpid())); fh.flush()
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
        return
    root = tk.Tk()
    Installer(root)

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
