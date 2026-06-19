"""installer.py — QA Studio graphical installer (modern web UI).

Zero external dependencies: uses only Python's standard library (http.server,
webbrowser, subprocess), so it runs BEFORE `pip install` — it cannot depend on
Flet/Qt/etc. because those aren't installed yet. Instead of tkinter, it spins up
a tiny localhost web server and opens a polished HTML/CSS interface in the user's
default browser, streaming live install progress over Server-Sent Events.

Run:  python installer.py
"""

import os
import sys
import json
import time
import queue
import socket
import threading
import subprocess
import webbrowser
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "QA Studio"
APP_VER  = "v1.4"
HERE = os.path.dirname(os.path.abspath(__file__))
REQ_FILE = os.path.join(HERE, "requirements.txt")
MAIN_PY  = os.path.join(HERE, "main.py")
ICON_ICO = os.path.join(HERE, "app.ico")

# ── Brand palette (matches theme.py / the app + emails) ───────────────────────
PALETTE = {
    "paper": "#F4F3F8", "card": "#FFFFFF", "tint": "#FAFAFC",
    "ink": "#1B1A22", "ink2": "#6B6975", "ink3": "#9C9AA6",
    "line": "#E7E6EE", "line2": "#F0EFF5",
    "violet": "#3A57D6", "violetH": "#2C44BE", "violetInk": "#2940C2",
    "violetSoft": "#E7ECFF", "grad1": "#1C80E0", "grad2": "#6A33A8",
    "green": "#1F8A52", "greenSoft": "#E7F4ED",
    "red": "#D6414A", "amber": "#AB780C",
}


def _logo_data_uri(names=("qa-logo-full.png", "qa-logo.png", "app.png")):
    for name in names:
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            try:
                with open(p, "rb") as f:
                    b = base64.b64encode(f.read()).decode("ascii")
                return f"data:image/png;base64,{b}"
            except Exception:
                pass
    return ""


# ── Event bus (worker → browser via SSE), with replay buffer ──────────────────
_EVENTS = []
_COND = threading.Condition()
_STATE = {"started": False, "finished": False, "ok": False}


def emit(ev):
    with _COND:
        _EVENTS.append(ev)
        _COND.notify_all()


# ── Install worker ────────────────────────────────────────────────────────────
def _run(cmd, lo, hi, progress):
    """Stream a subprocess to the log; nudge progress between lo..hi."""
    nice = " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd))
    emit({"type": "log", "tone": "dim", "msg": "> " + nice})
    cur = lo
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
            tone = "dim"
            if "error" in low or "failed" in low:
                tone = "err"
            elif "warning" in low or "not on path" in low:
                tone = "warn"
            elif "successfully installed" in low or "satisfied" in low:
                tone = "ok"
            emit({"type": "log", "tone": tone, "msg": line})
            if cur < hi:
                cur = min(hi, cur + 0.8)
                emit({"type": "progress", "value": round(cur)})
        proc.wait()
        return proc.returncode
    except Exception as e:
        emit({"type": "log", "tone": "err", "msg": f"ERROR: {e}"})
        return 1


def _make_shortcut(py):
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
        emit({"type": "log", "tone": "ok", "msg": "Desktop shortcut created."})
    except Exception as e:
        emit({"type": "log", "tone": "warn", "msg": f"Shortcut note: {e}"})


def _work():
    py = sys.executable

    emit({"type": "status", "title": "Installing\u2026",
          "desc": "Setting up QA Studio. You can watch progress below \u2014 this only takes a moment."})
    emit({"type": "step", "i": 0, "state": "active", "meta": "running"})
    emit({"type": "progress", "value": 10})
    _run([py, "-m", "pip", "install", "--upgrade", "pip"], 10, 22, None)

    emit({"type": "step", "i": 0, "state": "done", "meta": "done"})
    emit({"type": "step", "i": 1, "state": "active", "meta": "working"})
    emit({"type": "progress", "value": 30})
    code = _run([py, "-m", "pip", "install", "-r", REQ_FILE], 30, 88, None)
    if code != 0:
        emit({"type": "step", "i": 1, "state": "error", "meta": "failed"})
        emit({"type": "fail",
              "msg": "Dependency installation failed. Check your internet connection and try again."})
        _STATE["finished"] = True
        return

    emit({"type": "step", "i": 1, "state": "done", "meta": "done"})
    emit({"type": "step", "i": 2, "state": "active", "meta": "working"})
    emit({"type": "progress", "value": 94})
    _make_shortcut(py)

    emit({"type": "step", "i": 2, "state": "done", "meta": "done"})
    emit({"type": "progress", "value": 100})
    emit({"type": "done"})
    _STATE["finished"] = True
    _STATE["ok"] = True


def _launch_app():
    try:
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        flags = 0x08000000 if os.name == "nt" else 0
        subprocess.Popen([pythonw, MAIN_PY], cwd=HERE, creationflags=flags)
    except Exception:
        pass


# ── HTML UI ───────────────────────────────────────────────────────────────────
def _page():
    P = PALETTE
    logo = _logo_data_uri()
    logo_html = ("<img src='" + logo + "' alt='QA Studio' style='height:118px;display:block'/>"
                 if logo else
                 "<div style='font:800 26px Segoe UI;color:" + P["violetInk"] + "'>QA STUDIO</div>")
    html = _PAGE_TMPL
    for k, v in P.items():
        html = html.replace("__" + k.upper() + "__", v)
    html = html.replace("__LOGO__", logo_html)
    html = html.replace("__FAVICON__", _logo_data_uri(("app.png", "qa-logo.png")) or logo or "")
    html = html.replace("__APP__", APP_NAME)
    return html


_PAGE_TMPL = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>__APP__ Setup</title>
<link rel="icon" type="image/png" href="__FAVICON__"/>
<style>
:root{
  --paper:__PAPER__;--card:__CARD__;--tint:__TINT__;
  --ink:__INK__;--ink2:__INK2__;--ink3:__INK3__;
  --line:__LINE__;--line2:__LINE2__;
  --violet:__VIOLET__;--violetH:__VIOLETH__;--violetInk:__VIOLETINK__;
  --violetSoft:__VIOLETSOFT__;--grad1:__GRAD1__;--grad2:__GRAD2__;
  --green:__GREEN__;--greenSoft:__GREENSOFT__;--red:__RED__;--amber:__AMBER__;
  --ui:'Segoe UI Variable','Segoe UI',system-ui,Roboto,sans-serif;
  --mono:'Cascadia Code',Consolas,'JetBrains Mono',monospace;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:#E5E4EC;font-family:var(--ui);color:var(--ink);min-height:100vh;
  display:flex;align-items:center;justify-content:center;padding:22px}
.win{width:600px;max-width:96vw;background:var(--paper);border-radius:18px;overflow:hidden;
  display:flex;flex-direction:column;
  box-shadow:0 30px 70px -25px rgba(20,16,40,.5),0 2px 6px -2px rgba(20,16,40,.18),0 0 0 1px rgba(20,16,40,.05)}
.accent{height:4px;background:linear-gradient(90deg,var(--grad1),var(--violet),var(--grad2))}
.head{text-align:center;padding:30px 40px 6px;display:flex;flex-direction:column;align-items:center}
.head .tagline{font-size:13px;font-weight:600;color:var(--ink2);margin-top:10px}
.divider{height:1px;background:var(--line);margin:22px 36px}
.body{padding:0 40px 34px;flex:1;display:flex;flex-direction:column}
.lead{font-size:17px;font-weight:800;color:var(--ink);margin:0}
.leadsub{font-size:13px;font-weight:500;color:var(--ink2);margin:7px 0 0;line-height:1.55}
.steps{margin:20px 0 4px;border:1px solid var(--line);border-radius:12px;background:var(--card);overflow:hidden}
.step{display:flex;align-items:center;gap:13px;padding:13px 16px;border-top:1px solid var(--line2)}
.step:first-child{border-top:0}
.step .dot{width:24px;height:24px;border-radius:50%;flex:0 0 24px;display:grid;place-items:center;
  font-size:13px;font-weight:800}
.step.wait .dot{background:var(--line2);color:var(--ink3)}
.step.active .dot{background:var(--violetSoft);color:var(--violetInk)}
.step.done .dot{background:var(--greenSoft);color:var(--green)}
.step.error .dot{background:#FBE7E8;color:var(--red)}
.step .stext{font-size:13.5px;font-weight:600;color:var(--ink)}
.step.wait .stext{color:var(--ink3);font-weight:500}
.step .smeta{margin-left:auto;font-family:var(--mono);font-size:11px;font-weight:600;color:var(--ink3)}
.step.active .smeta{color:var(--violetInk)}
.step.done .smeta{color:var(--green)}
.progwrap{margin:22px 0 6px;display:none}
.progwrap.show{display:block}
.progtop{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:9px}
.progtop .lbl{font-size:12.5px;font-weight:600;color:var(--ink2)}
.progtop .pct{font-family:var(--mono);font-size:15px;font-weight:800;color:var(--violetInk)}
.track{height:8px;border-radius:99px;background:#E5E3EE;overflow:hidden}
.spinner{width:15px;height:15px;border-radius:50%;border:2px solid var(--violetSoft);
  border-top-color:var(--violet);animation:spin .7s linear infinite;display:inline-block;
  vertical-align:-2px;box-sizing:border-box}
.pspin{margin-right:9px}
.step.active .dot .spinner{width:13px;height:13px;border-color:#CFD6FF;border-top-color:var(--violet)}
@keyframes spin{to{transform:rotate(360deg)}}
.fill{height:100%;border-radius:99px;width:0%;
  background:linear-gradient(90deg,var(--violet),#6276E8);transition:width .5s ease;
  box-shadow:0 0 12px -2px rgba(58,87,214,.6)}
.console{margin-top:18px;background:#15151F;border:1px solid #2A2A38;border-radius:12px;overflow:hidden;display:none}
.console.show{display:block}
.console .ctop{display:flex;align-items:center;gap:7px;padding:9px 14px;background:#1D1D29;border-bottom:1px solid #2A2A38}
.console .ctop .cd{width:9px;height:9px;border-radius:50%}
.console .ctop .lbl{margin-left:6px;font-family:var(--mono);font-size:10.5px;font-weight:600;color:#7E7C8C}
.console .lines{padding:12px 16px;max-height:200px;overflow-y:auto;font-family:var(--mono);font-size:11.5px;line-height:1.85}
.console .lines::-webkit-scrollbar{width:8px}
.console .lines::-webkit-scrollbar-thumb{background:#34343F;border-radius:8px}
.cl{white-space:pre-wrap;color:#B7B5C4}
.cl.ok{color:#5BD99A}.cl.err{color:#FF8A8F}.cl.warn{color:#F2C94C}.cl.dim{color:#6B6979}
.actions{display:flex;gap:12px;margin-top:auto;padding-top:26px;align-items:center}
.btn{height:48px;border-radius:12px;border:0;cursor:pointer;font:800 14px var(--ui);
  display:inline-flex;align-items:center;justify-content:center;gap:9px;transition:background .15s,transform .05s}
.btn:active{transform:translateY(1px)}
.btn svg{width:17px;height:17px}
.btn-primary{flex:1;background:var(--violet);color:#fff;box-shadow:0 10px 22px -10px rgba(58,87,214,.9)}
.btn-primary:hover{background:var(--violetH)}
.btn-primary[disabled]{background:#BFC6E8;box-shadow:none;cursor:default}
.btn-green{background:var(--green);color:#fff;box-shadow:0 10px 22px -10px rgba(31,138,82,.85)}
.btn-green:hover{background:#188044}
.done-note{display:flex;align-items:center;gap:9px;font-size:13px;font-weight:700;color:var(--green)}
.done-note.err{color:var(--red)}
.done-note svg{width:18px;height:18px}
</style></head>
<body>
<div class="win">
  <div class="accent"></div>
  <div class="head">__LOGO__<div class="tagline">AI-powered Azure DevOps test-case generator</div></div>
  <div class="divider"></div>
  <div class="body">
    <p class="lead" id="lead">Ready to install</p>
    <p class="leadsub" id="leadsub">This installs the Python packages QA Studio needs and adds a Desktop shortcut so you can launch it any time. Takes about a minute.</p>

    <div class="steps" id="steps">
      <div class="step wait" data-i="0"><span class="dot">1</span><span class="stext">Check Python &amp; upgrade pip</span><span class="smeta">~5s</span></div>
      <div class="step wait" data-i="1"><span class="dot">2</span><span class="stext">Install required packages</span><span class="smeta"></span></div>
      <div class="step wait" data-i="2"><span class="dot">3</span><span class="stext">Create Desktop shortcut</span><span class="smeta"></span></div>
    </div>

    <div class="progwrap" id="progwrap">
      <div class="progtop"><span class="lbl"><span class="spinner pspin" id="pspin"></span><span id="proglbl">Installing&hellip;</span></span><span class="pct" id="pct">0%</span></div>
      <div class="track"><div class="fill" id="fill"></div></div>
    </div>

    <div class="console" id="console">
      <div class="ctop"><span class="cd" style="background:#FF5F57"></span><span class="cd" style="background:#FEBC2E"></span><span class="cd" style="background:#28C840"></span><span class="lbl">install log</span></div>
      <div class="lines" id="lines"></div>
    </div>

    <div class="actions" id="actions">
      <button class="btn btn-primary" id="install" onclick="startInstall()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg>
        Install __APP__
      </button>
    </div>
  </div>
</div>

<script>
const $=s=>document.querySelector(s);
const APP="__APP__";
function setStep(i,state,meta){
  const el=document.querySelector('.step[data-i="'+i+'"]'); if(!el)return;
  el.className='step '+state;
  const dot=el.querySelector('.dot');
  if(state==='done')dot.innerHTML='&#10003;';
  else if(state==='error')dot.innerHTML='&#10005;';
  else if(state==='active')dot.innerHTML='<span class="spinner"></span>';
  else dot.textContent=String(i+1);
  if(meta!=null)el.querySelector('.smeta').textContent=meta;
}
function logLine(tone,msg){
  const box=$('#lines');const d=document.createElement('div');
  d.className='cl '+(tone||'dim');d.textContent=msg;box.appendChild(d);
  box.scrollTop=box.scrollHeight;
}
function setProgress(v){$('#fill').style.width=v+'%';$('#pct').textContent=Math.round(v)+'%';}
function startInstall(){
  $('#install').disabled=true;$('#install').innerHTML='Installing&hellip;';
  $('#progwrap').classList.add('show');
  fetch('/install',{method:'POST'});
}
function onDone(){
  var ps=$('#pspin'); if(ps)ps.style.display='none';
  $('#lead').textContent='Installation complete';$('#lead').style.color='var(--green)';
  $('#leadsub').textContent='QA Studio is ready. A shortcut has been added to your Desktop.';
  $('#proglbl').textContent='All set';
  $('#actions').innerHTML=
    '<div class="done-note"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/></svg>Setup finished successfully</div>'+
    '<button class="btn btn-green" style="flex:0 0 auto;padding:0 26px" onclick="launch()">Launch '+APP+' &rarr;</button>';
}
function onFail(msg){
  var ps=$('#pspin'); if(ps)ps.style.display='none';
  $('#lead').textContent='Installation failed';$('#lead').style.color='var(--red)';
  $('#leadsub').textContent=msg||'Something went wrong.';
  $('#actions').innerHTML=
    '<div class="done-note err"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>Install failed</div>'+
    '<button class="btn btn-primary" style="flex:0 0 auto;padding:0 24px" onclick="retry()">Retry</button>';
}
function launch(){
  fetch('/launch',{method:'POST'});
  setTimeout(function(){document.body.innerHTML='<div style="font:600 15px var(--ui);color:#6B6975;text-align:center">QA Studio is launching&hellip; you can close this tab.</div>';},400);
}
function retry(){location.reload();}
window.addEventListener('beforeunload',function(){try{navigator.sendBeacon('/shutdown');}catch(e){}});

const es=new EventSource('/events');
es.onmessage=function(e){
  if(!e.data)return;var ev;try{ev=JSON.parse(e.data);}catch(_){return;}
  if(ev.type==='log')logLine(ev.tone,ev.msg);
  else if(ev.type==='progress')setProgress(ev.value);
  else if(ev.type==='step')setStep(ev.i,ev.state,ev.meta);
  else if(ev.type==='status'){if(ev.title)$('#lead').textContent=ev.title;if(ev.desc)$('#leadsub').textContent=ev.desc;}
  else if(ev.type==='done')onDone();
  else if(ev.type==='fail')onFail(ev.msg);
};
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence console noise
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, _page())
        elif self.path == "/events":
            self._sse()
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path == "/install":
            if not _STATE["started"]:
                _STATE["started"] = True
                threading.Thread(target=_work, daemon=True).start()
            self._send(200, "ok", "text/plain")
        elif self.path == "/launch":
            _launch_app()
            self._send(200, "ok", "text/plain")
            threading.Thread(target=self._delayed_shutdown, args=(0.8,), daemon=True).start()
        elif self.path == "/shutdown":
            self._send(200, "ok", "text/plain")
            # Only shut down once the install is finished, so an accidental
            # navigation mid-install doesn't kill the process.
            if _STATE["finished"]:
                threading.Thread(target=self._delayed_shutdown, args=(0.3,), daemon=True).start()
        else:
            self._send(404, "not found", "text/plain")

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        idx = 0
        try:
            while True:
                with _COND:
                    while idx >= len(_EVENTS):
                        if not _COND.wait(timeout=15):
                            break
                    pending = _EVENTS[idx:]
                    idx = len(_EVENTS)
                if pending:
                    for ev in pending:
                        self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                else:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except Exception:
            return

    def _delayed_shutdown(self, delay):
        time.sleep(delay)
        try:
            self.server.shutdown()
        except Exception:
            pass
        os._exit(0)


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


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


def _open_app_window(url):
    """Open the installer in a chromeless app window (Edge/Chrome --app mode) so
    it looks like a native installer instead of a browser tab. Falls back to the
    default browser if no Chromium browser is found."""
    W, H = 648, 720
    candidates = []
    if os.name == "nt":
        pf = os.environ.get("ProgramFiles", r"C:\\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")
        local = os.environ.get("LocalAppData", "")
        candidates = [
            os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        candidates = ["/usr/bin/google-chrome", "/usr/bin/microsoft-edge",
                      "/usr/bin/chromium", "/usr/bin/chromium-browser"]

    exe = next((c for c in candidates if os.path.exists(c)), None)
    if exe:
        try:
            import tempfile
            prof = os.path.join(tempfile.gettempdir(), "qastudio_installer_profile")
            flags = 0x08000000 if os.name == "nt" else 0
            subprocess.Popen(
                [exe, f"--app={url}", f"--window-size={W},{H}",
                 f"--user-data-dir={prof}", "--no-first-run", "--no-default-browser-check"],
                creationflags=flags)
            return
        except Exception:
            pass
    # Fallback: default browser tab
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main():
    lock = _acquire_single_instance()
    if lock is None:
        return
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    threading.Thread(target=lambda: _open_app_window(url), daemon=True).start()
    print(f"{APP_NAME} installer running at {url}")
    print("If a window didn't open, paste that address into your browser.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
