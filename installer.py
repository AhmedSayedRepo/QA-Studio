"""installer.py — QA Studio graphical installer.

Zero external dependencies (uses only Python's stdlib tkinter), so it can run
before any pip packages are installed. It:
  1. Verifies pip is available
  2. Installs everything in requirements.txt with a live progress bar + %
  3. Creates a Desktop shortcut with the app icon
  4. Offers to launch the app

UI redesign: light header with the QA Studio logo, a 3-step checklist, a slim
violet progress bar, a refined dark log console, and Ready → Installing → Done
states — matching the app + email visual language.
"""
import os
import sys
import threading
import subprocess
import tkinter as tk
from tkinter import ttk

APP_NAME = "QA Studio"
APP_VER  = "v1.4"
HERE = os.path.dirname(os.path.abspath(__file__))
REQ_FILE = os.path.join(HERE, "requirements.txt")
MAIN_PY = os.path.join(HERE, "main.py")
ICON_ICO = os.path.join(HERE, "app.ico")
LOGO_PNG = next((os.path.join(HERE, n) for n in ("qa-logo.png", "app.png")
                 if os.path.exists(os.path.join(HERE, n))), "")
LOGO_FULL_PNG = (os.path.join(HERE, "qa-logo-full.png")
                if os.path.exists(os.path.join(HERE, "qa-logo-full.png")) else "")

# Brand palette (matches theme.py / the email design)
PAPER     = "#F4F3F8"
CARD      = "#FFFFFF"
TINT      = "#FAFAFC"
VIOLET    = "#6A4DFF"
VIOLET_H  = "#5C3FF2"
VIOLET_INK= "#5234E0"
VIOLET_SOFT="#EEEAFF"
INK       = "#1B1A22"
INK_2     = "#6B6975"
INK_3     = "#9C9AA6"
GREEN     = "#1F8A52"
GREEN_H   = "#188044"
GREEN_SOFT= "#E7F4ED"
RED       = "#D6414A"
AMBER     = "#AB780C"
AMBER_SOFT= "#F7EFD8"
LINE      = "#E7E6EE"
LINE_2    = "#F0EFF5"
LOG_BG    = "#16151C"      # dark log surface
LOG_TOP   = "#1E1D26"
LOG_DIM   = "#6B697A"
LOG_OK    = "#5BD99A"
LOG_ERR   = "#FF7A80"
LOG_WARN  = "#E7B450"
LOG_INFO  = "#9B86FF"
LOG_INK   = "#B7B5C4"

# ASCII-safe spinner (avoids UTF-8 corruption across editors/transfers)
SPIN = ["|", "/", "-", "\\"]

# Step glyphs / colors per state
DOT_WAIT = ("\u25CB", INK_3)     # ○
DOT_ACTIVE = ("\u25CF", VIOLET_INK)  # ●
DOT_DONE = ("\u2713", GREEN)     # ✓


def _round_points(x1, y1, x2, y2, r):
    return [x1+r, y1, x2-r, y1, x2, y1, x2, y1+r, x2, y2-r, x2, y2,
            x2-r, y2, x1+r, y2, x1, y2, x1, y2-r, x1, y1+r, x1, y1]


class RoundedButton(tk.Canvas):
    """A flat, rounded-rectangle button (Tkinter has no native rounded buttons)."""
    def __init__(self, parent, text="", command=None, fill=VIOLET, hover=VIOLET_H,
                 fg="#FFFFFF", height=50, radius=13,
                 font=("Segoe UI Semibold", 11, "bold"), bg=PAPER):
        super().__init__(parent, height=height, bg=bg, highlightthickness=0, bd=0)
        self._command = command
        self._fill = fill; self._hover = hover; self._fg = fg
        self._radius = radius; self._font = font; self._text = text
        self._enabled = True; self._cur = fill
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _draw(self):
        self.delete("all")
        w = self.winfo_width(); h = self.winfo_height()
        if w <= 1:
            return
        pts = _round_points(1, 1, w - 1, h - 1, self._radius)
        self.create_polygon(pts, smooth=True, splinesteps=24,
                            fill=self._cur, outline=self._cur)
        self.create_text(w / 2, h / 2, text=self._text, fill=self._fg, font=self._font)

    def _on_click(self, e):
        if self._enabled and self._command:
            self._command()

    def _on_enter(self, e):
        if self._enabled:
            self._cur = self._hover; self.config(cursor="hand2"); self._draw()

    def _on_leave(self, e):
        self._cur = self._fill; self.config(cursor=""); self._draw()

    def set(self, text=None, fill=None, hover=None, fg=None, command=None, enabled=None):
        if text is not None: self._text = text
        if fill is not None: self._fill = fill; self._cur = fill
        if hover is not None: self._hover = hover
        if fg is not None: self._fg = fg
        if command is not None: self._command = command
        if enabled is not None: self._enabled = enabled
        self._draw()


class Installer:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} Setup")
        self.root.configure(bg=PAPER)
        self.root.resizable(False, False)
        try:
            if os.path.exists(ICON_ICO):
                self.root.iconbitmap(ICON_ICO)
        except Exception:
            pass

        W, H = 540, 720
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//2}")
        self.root.minsize(W, H)

        self._pct = 0.0
        self._target = 0.0
        self._spin_i = 0
        self._running = False
        self._logo_img = None
        self._steps = []   # list of (dot_label, text_label, meta_label)

        # thin violet accent line
        tk.Frame(self.root, bg=VIOLET, height=3).pack(fill="x", side="top")

        # ── Header: full QA Studio logo lockup (mark + wordmark) ────────────
        header = tk.Frame(self.root, bg=PAPER)
        header.pack(fill="x", side="top", padx=28, pady=(16, 0))

        if LOGO_FULL_PNG:
            try:
                img = tk.PhotoImage(file=LOGO_FULL_PNG)
                f = max(1, round(img.height() / 132))
                self._logo_img = img.subsample(f, f)
            except Exception:
                self._logo_img = None
        if self._logo_img is not None:
            tk.Label(header, image=self._logo_img, bg=PAPER).pack(anchor="center")
        else:
            tk.Label(header, text=APP_NAME, bg=PAPER, fg=INK,
                     font=("Segoe UI Semibold", 20, "bold")).pack(anchor="center")
        tk.Label(header, text="AI-powered Azure DevOps test-case generator",
                 bg=PAPER, fg=INK_2, font=("Segoe UI", 10)).pack(anchor="center", pady=(4, 0))

        # divider
        tk.Frame(self.root, bg=LINE, height=1).pack(fill="x", padx=28, pady=(18, 0))

        # ── Footer button (pinned bottom; packed before body) ───────────────
        footer = tk.Frame(self.root, bg=PAPER)
        footer.pack(fill="x", side="bottom", padx=28, pady=(0, 22))
        self.btn = RoundedButton(footer, text="Install QA Studio",
                                 command=self.start_install,
                                 fill=VIOLET, hover=VIOLET_H, fg="#FFFFFF",
                                 height=50, radius=13)
        self.btn.pack(fill="x")
        self._btn_idle = VIOLET

        # ── Body ────────────────────────────────────────────────────────────
        body = tk.Frame(self.root, bg=PAPER)
        body.pack(fill="both", expand=True, side="top", padx=28, pady=(18, 6))

        # lead + spinner
        trow = tk.Frame(body, bg=PAPER)
        trow.pack(fill="x")
        self.title_lbl = tk.Label(trow, text="Ready to install", bg=PAPER, fg=INK,
                                  font=("Segoe UI Semibold", 14, "bold"), anchor="w")
        self.title_lbl.pack(side="left")
        self.spin_lbl = tk.Label(trow, text="", bg=PAPER, fg=VIOLET,
                                 font=("Consolas", 16, "bold"))
        self.spin_lbl.pack(side="right")

        self.desc_lbl = tk.Label(body,
            text="This sets up the Python packages QA Studio needs and adds a\n"
                 "Desktop shortcut so you can launch it any time.",
            bg=PAPER, fg=INK_2, font=("Segoe UI", 10), anchor="w", justify="left")
        self.desc_lbl.pack(fill="x", pady=(6, 0))

        # ── Steps checklist card ────────────────────────────────────────────
        card = tk.Frame(body, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        card.pack(fill="x", pady=(16, 0))
        steps_def = [
            ("Check Python & environment", "~5s"),
            ("Install required packages", "5 pkgs"),
            ("Create Desktop shortcut", ""),
        ]
        for i, (label, meta) in enumerate(steps_def):
            if i > 0:
                tk.Frame(card, bg=LINE_2, height=1).pack(fill="x")
            row = tk.Frame(card, bg=CARD)
            row.pack(fill="x", padx=14, pady=11)
            dot = tk.Label(row, text=DOT_WAIT[0], fg=DOT_WAIT[1], bg=CARD,
                           font=("Segoe UI", 13, "bold"), width=2)
            dot.pack(side="left")
            txt = tk.Label(row, text=label, bg=CARD, fg=INK_3,
                           font=("Segoe UI", 11), anchor="w")
            txt.pack(side="left", padx=(6, 0))
            mt = tk.Label(row, text=meta, bg=CARD, fg=INK_3,
                          font=("Consolas", 9), anchor="e")
            mt.pack(side="right")
            self._steps.append((dot, txt, mt))

        # ── Progress bar + percentage ───────────────────────────────────────
        prow = tk.Frame(body, bg=PAPER)
        prow.pack(fill="x", pady=(20, 4))
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("QA.Horizontal.TProgressbar", troughcolor="#E5E3EE",
                        background=VIOLET, bordercolor="#E5E3EE",
                        lightcolor=VIOLET, darkcolor=VIOLET, thickness=8)
        self.bar = ttk.Progressbar(prow, style="QA.Horizontal.TProgressbar",
                                   mode="determinate", maximum=100, length=380)
        self.bar.pack(side="left", fill="x", expand=True)
        self.pct_lbl = tk.Label(prow, text="0%", bg=PAPER, fg=VIOLET_INK,
                                font=("Consolas", 13, "bold"), width=5, anchor="e")
        self.pct_lbl.pack(side="right", padx=(12, 0))

        self.status_lbl = tk.Label(body, text="", bg=PAPER, fg=INK_3,
                                   font=("Segoe UI", 9), anchor="w")
        self.status_lbl.pack(fill="x")

        # ── Dark log console ────────────────────────────────────────────────
        console = tk.Frame(body, bg=LOG_BG, highlightthickness=1,
                           highlightbackground="#2A2933")
        console.pack(fill="both", expand=True, pady=(14, 0))
        ctop = tk.Frame(console, bg=LOG_TOP, height=30)
        ctop.pack(fill="x")
        ctop.pack_propagate(False)
        for c in ("#FF5F57", "#FEBC2E", "#28C840"):
            tk.Label(ctop, text="\u25CF", fg=c, bg=LOG_TOP,
                     font=("Segoe UI", 8)).pack(side="left", padx=(8 if c == "#FF5F57" else 1, 0))
        tk.Label(ctop, text="install log", fg=LOG_DIM, bg=LOG_TOP,
                 font=("Consolas", 9)).pack(side="left", padx=(8, 0))
        self.log = tk.Text(console, height=6, bg=LOG_BG, fg=LOG_INK,
                           font=("Consolas", 9), relief="flat", wrap="word",
                           state="disabled", padx=12, pady=10, bd=0,
                           insertbackground=LOG_INK)
        self.log.pack(fill="both", expand=True)
        for tag, col in (("dim", LOG_DIM), ("ok", LOG_OK), ("err", LOG_ERR),
                         ("warn", LOG_WARN), ("info", LOG_INFO), ("ink", LOG_INK)):
            self.log.tag_configure(tag, foreground=col)
        self._log("$ Ready \u2014 click \u201cInstall QA Studio\u201d to begin.", "dim")

        self._animate()  # start the smooth bar + spinner loop

    # ── step state helper ───────────────────────────────────────────────────
    def _step(self, i, state, meta=None):
        if i < 0 or i >= len(self._steps):
            return
        dot, txt, mt = self._steps[i]
        glyph, col = {"wait": DOT_WAIT, "active": DOT_ACTIVE, "done": DOT_DONE}[state]
        dot.config(text=glyph, fg=col)
        txt.config(fg=(INK if state != "wait" else INK_3),
                   font=("Segoe UI", 11, "bold") if state != "wait" else ("Segoe UI", 11))
        if meta is not None:
            mt.config(text=meta, fg=(VIOLET_INK if state == "active" else INK_3))

    # ── animation loop (runs on main thread via after) ─────────────────────
    def _animate(self):
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
        self.btn.set(text="Installing\u2026", fill="#C9C2E8", hover="#C9C2E8", enabled=False)
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        py = sys.executable

        self.ui(lambda: (self._step(0, "active", "running"),
                         self._set(target=10, title="Installing\u2026",
                                   desc="Setting up QA Studio. You can watch progress below.",
                                   status="Checking environment & upgrading pip\u2026")))
        self._run([py, "-m", "pip", "install", "--upgrade", "pip"], 10, 22)

        self.ui(lambda: (self._step(0, "done", "done"),
                         self._step(1, "active", "working"),
                         self._set(target=30, status="Installing packages — this can take a few minutes\u2026")))
        code = self._run([py, "-m", "pip", "install", "-r", REQ_FILE], 30, 88)
        if code != 0:
            self.ui(lambda: self._fail("Dependency installation failed.\n"
                                       "Check your internet connection and try again."))
            return

        self.ui(lambda: (self._step(1, "done", "done"),
                         self._step(2, "active", "working"),
                         self._set(target=94, status="Creating Desktop shortcut\u2026")))
        self._make_shortcut(py)

        self.ui(lambda: (self._step(2, "done", "done"),
                         self._set(target=100, status="")))
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
        self.spin_lbl.config(text="\u2713", fg=GREEN)
        self._set(title="Installation complete", status="",
                  desc="QA Studio is ready. A shortcut was added to your Desktop.")
        self.title_lbl.config(fg=GREEN)
        self._btn_idle = GREEN
        self.btn.set(text="Launch QA Studio  \u2192", fill=GREEN, hover=GREEN_H,
                     enabled=True, command=self._launch)

    def _fail(self, msg):
        self._running = False
        self.spin_lbl.config(text="\u2715", fg=RED)
        self._set(title="Installation failed", desc=msg, status="")
        self.title_lbl.config(fg=RED)
        self._btn_idle = VIOLET
        self.btn.set(text="Retry", fill=VIOLET, hover=VIOLET_H,
                     enabled=True, command=self.start_install)

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
