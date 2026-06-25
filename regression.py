"""regression.py — Regression Plan tab for QA Studio.

Builds a regression plan from an existing sprint + selected user stories,
estimates effort from the test cases that ALREADY exist in the chosen test plan
(reference-only — nothing is generated), balances work across named resources,
and exports to Word / Excel / JSON / PDF.

Integration hooks live in main.py (import, T.NAV registration, rail clickable,
render routing). Shared UI helpers and the theme are reused from main.py via a
deferred import so there is no circular import at load time.
"""
import os, re, json, threading
from datetime import datetime

import flet as ft
import theme as T
import engine as E

# ── Effort model (HARDCODED — change here if your team's numbers differ) ───────
AVG_MINUTES_PER_CASE = 8          # manual execution time per existing test case
DEFAULT_PRIORITY     = 3          # used when a story has no ADO priority set
PRIORITY_BOOST = {1: 1.30, 2: 1.15, 3: 1.00, 4: 0.90}
_PRI_FULL = {1: "P1 (highest)", 2: "P2", 3: "P3", 4: "P4 (lowest)"}


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA GATHERING  (Azure DevOps — reference existing only)
# ═══════════════════════════════════════════════════════════════════════════════
def _fetch_meta(app, ids):
    meta = {}
    if not ids:
        return meta
    org, proj = E.AZURE_ORG, app.project
    for i in range(0, len(ids), 200):
        batch = ids[i:i + 200]
        url = (f"https://dev.azure.com/{org}/{proj}/_apis/wit/workitems"
               f"?ids={','.join(map(str, batch))}"
               f"&fields=System.Id,System.Title,System.State,"
               f"Microsoft.VSTS.Common.Priority&api-version=7.0")
        try:
            data = E._azure_get(url)
        except Exception:
            data = {}
        for w in data.get("value", []):
            f = w.get("fields", {})
            try:
                pri = int(f.get("Microsoft.VSTS.Common.Priority", DEFAULT_PRIORITY)
                          or DEFAULT_PRIORITY)
            except Exception:
                pri = DEFAULT_PRIORITY
            meta[int(w["id"])] = {"title": f.get("System.Title", ""),
                                  "state": f.get("System.State", "Unknown"),
                                  "priority": pri}
    return meta


def _loading_field(text):
    """A bordered field that shows a small spinner + message — used while plans /
    stories are being fetched in the background."""
    return ft.Container(
        ft.Row([ft.ProgressRing(width=16, height=16, stroke_width=2, color=T.VIOLET),
                ft.Text(text, size=13, color=T.INK_3, expand=True)],
               spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.Padding.symmetric(vertical=12, horizontal=12),
        bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER), border_radius=T.R)


def _count_cases(app, selected):
    """selected = [{"id","title","plan_id"}]. Count existing test cases for each
    story in ITS OWN plan. The per-suite case lookups are run concurrently so a
    large selection (hundreds of stories) finishes in seconds, not minutes."""
    import concurrent.futures as _cf
    counts = {int(s["id"]): 0 for s in selected}
    by_plan = {}
    for s in selected:
        by_plan.setdefault(s.get("plan_id"), []).append(int(s["id"]))
    tasks = []                       # (sid, plan_id, suite_id)

    # Parallelize suite-list fetches: one HTTP call per plan → run concurrently.
    # Results are also cached on app._reg_suite_cache so re-clicks skip the round-trip.
    def _discover_plan(pid_sids):
        pid, sids = pid_sids
        if not pid:
            return pid, sids, {}
        cache = getattr(app, "_reg_suite_cache", {})
        if pid in cache:
            return pid, sids, cache[pid]
        try:
            smap = E.discover_suites_for_stories(app.project, pid, set(sids),
                                                 create_missing=False)
        except Exception:
            smap = {}
        try:
            app._reg_suite_cache[pid] = smap
        except Exception:
            pass
        return pid, sids, smap

    plan_items = [(pid, sids) for pid, sids in by_plan.items() if pid]
    with _cf.ThreadPoolExecutor(max_workers=min(8, len(plan_items) or 1)) as ex:
        for pid, sids, smap in ex.map(_discover_plan, plan_items):
            for sid in sids:
                suite_id = smap.get(sid)
                if suite_id:
                    tasks.append((sid, pid, suite_id))

    def _one(t):
        sid, pid, suite_id = t
        try:
            return sid, len(E.fetch_test_cases_for_suite(app.project, pid, suite_id))
        except Exception:
            return sid, 0

    if tasks:
        with _cf.ThreadPoolExecutor(max_workers=min(16, len(tasks))) as ex:
            for sid, n in ex.map(_one, tasks):
                counts[sid] = n
    return counts


def build_rows(app, selected):
    ids = [int(s["id"]) for s in selected]
    meta = _fetch_meta(app, ids)
    counts = _count_cases(app, selected)
    rows = []
    for s in selected:
        sid = int(s["id"])
        m = meta.get(sid, {})
        pri = m.get("priority", DEFAULT_PRIORITY)
        cases = counts.get(sid, 0)
        boost = PRIORITY_BOOST.get(pri, 1.0)
        hours = round(cases * (AVG_MINUTES_PER_CASE / 60.0) * boost, 2)
        rows.append({"id": sid, "title": m.get("title", "") or s.get("title", ""),
                     "state": m.get("state", "Unknown"), "priority": pri,
                     "cases": cases, "boost": boost, "hours": hours,
                     "plan_id": s.get("plan_id"), "assignee": ""})
    return rows


def assign_resources(rows, names):
    """Greedy balance: largest story → least-loaded resource. Sets r['assignee'].
    Returns {name: total_hours}."""
    if not names:
        for r in rows:
            r["assignee"] = ""
        return {}
    load = {n: 0.0 for n in names}
    for r in sorted(rows, key=lambda x: -x["hours"]):
        n = min(load, key=lambda k: load[k])
        r["assignee"] = n
        load[n] = round(load[n] + r["hours"], 2)
    return load


def plan_payload(app):
    if getattr(app, "_reg_mode", "existing") == "create":
        return _cp_payload(app)
    rows = [dict(r) for r in (app._reg_selected_rows or [])]
    names = list(app._reg_res_names or [])
    load = assign_resources(rows, names)
    count = app._reg_res_count or len(names) or 1
    total_cases = sum(r["cases"] for r in rows)
    total_hours = round(sum(r["hours"] for r in rows), 2)
    per_person = round(total_hours / count, 2) if count else total_hours
    workload = []
    if names:
        cnt = {n: 0 for n in names}
        ccases = {n: 0 for n in names}
        for r in rows:
            a = r.get("assignee")
            if a in cnt:
                cnt[a] += 1
                ccases[a] += r["cases"]
        workload = [{"name": n, "stories": cnt[n], "cases": ccases[n],
                     "hours": round(load.get(n, 0.0), 2)} for n in names]
    plans = list(app._reg_plans_selected or [])
    plan_names = ", ".join(p["name"] for p in plans) or (getattr(app, "plan_name", "") or "")
    plan_ids = ", ".join(str(p["id"]) for p in plans)
    return {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "project": app.project, "plan_id": plan_ids,
            "plan_name": plan_names, "plans": plans,
            "report_title": "Regression Test Plan", "mode": "existing",
            "avg_minutes_per_case": AVG_MINUTES_PER_CASE,
            "priority_boost": PRIORITY_BOOST, "resources_count": count,
            "resource_names": names, "stories": rows, "workload": workload,
            "total_stories": len(rows), "total_cases": total_cases,
            "total_hours": total_hours, "hours_per_person": per_person}


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPORTERS
# ═══════════════════════════════════════════════════════════════════════════════
# ── email visual helpers ───────────────────────────────────────────────────────
_AV_COLORS = ["#3A57D6", "#1F9D57", "#6A52F0", "#1C80E0", "#C2860C", "#6A33A8"]

def _av(name):
    """Return (initial, color) for an assignee avatar."""
    nm = (name or "").strip()
    if not nm:
        return "—", "#9FA2B2"
    return nm[0].upper(), _AV_COLORS[sum(ord(c) for c in nm) % len(_AV_COLORS)]

def _email_pri(p):
    """Return (bg, fg, label) for a priority chip in the email."""
    return {1: ("#FCEBEC", "#E0474D"), 2: ("#FAF1DD", "#C2860C"),
            4: ("#E5F6EC", "#1F9D57")}.get(p, ("#F4F6FB", "#6E7180")) + (f"P{p}",)


def _plan_html(d):
    """Polished, Outlook-safe HTML summary of the plan for the email body.

    Table-based with inline styles so it renders across Outlook / Gmail / Apple
    Mail. Colours track theme.py. The external-sender warning some inboxes show
    is injected by the mail server, not here.
    """
    # KPI tiles
    def _kpi(label, val, unit, fg, bg, bd, width="25%"):
        u = (f"<span style='font-size:12px;color:#9aa4b8;font-weight:600'> {unit}</span>"
             if unit else "")
        return (
            f"<td width='{width}' valign='top' style='padding:0 5px'>"
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='background:{bg};border:1px solid {bd};border-radius:12px'><tr>"
            f"<td style='padding:13px 14px'>"
            f"<div style='font-size:10px;letter-spacing:.6px;text-transform:uppercase;"
            f"color:{fg};font-weight:700'>{label}</div>"
            f"<div style='font-family:Consolas,monospace;font-size:23px;font-weight:700;"
            f"color:{fg};margin-top:4px'>{val}{u}</div></td></tr></table></td>")

    # Sprint Plan estimates from an hours range (no existing test cases), so the
    # test-case KPI + column are hidden whenever the plan has zero cases.
    show_cases = (d.get("total_cases") or 0) > 0
    kw = "25%" if show_cases else "33.33%"
    kpi_cells = [_kpi("Stories", d["total_stories"], "", "#181A24", "#F6F8FC", "#EBEFF7", kw)]
    if show_cases:
        kpi_cells.append(_kpi("Test cases", d["total_cases"], "", "#181A24", "#F6F8FC", "#EBEFF7", kw))
    kpi_cells.append(_kpi("Total effort", d["total_hours"], "h", "#2940C2", "#E7ECFF", "#D6DEFF", kw))
    kpi_cells.append(_kpi("Per person", d["hours_per_person"], "h", "#1F9D57", "#E5F6EC", "#D2EEDF", kw))
    kpis = (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
        + "".join(kpi_cells) + "</tr></table>")

    # story rows
    srows = []
    for r in d["stories"]:
        bg, fg, lab = _email_pri(r["priority"])
        init, acol = _av(r.get("assignee", ""))
        who = (r.get("assignee") or "").strip()
        asg = (
            f"<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
            f"<td width='24' style='vertical-align:middle'>"
            f"<div style='width:24px;height:24px;line-height:24px;border-radius:50%;"
            f"background:{acol};color:#fff;text-align:center;font-size:10px;font-weight:700;"
            f"font-family:Segoe UI,Arial,sans-serif'>{init}</div></td>"
            f"<td style='padding-left:9px;font-size:13px;font-weight:600;color:#39435c;"
            f"white-space:nowrap;vertical-align:middle'>{who or '—'}</td></tr></table>")
        cases_cell = (
            f"<td style='padding:12px 8px;text-align:right;font-family:Consolas,monospace;"
            f"font-size:13.5px;color:#46506a'>{r['cases']}</td>") if show_cases else ""
        srows.append(
            f"<tr style='border-top:1px solid #f0f3f9'>"
            f"<td style='padding:12px 14px;font-family:Consolas,monospace;font-size:13px;"
            f"font-weight:600;color:#3A57D6;white-space:nowrap'>{r['id']}</td>"
            f"<td style='padding:12px 8px;font-size:13.5px;font-weight:600;color:#1f2940'>"
            f"{(r['title'] or '—')}</td>"
            f"<td style='padding:12px 8px;text-align:center'>"
            f"<span style='font-family:Consolas,monospace;font-size:11px;font-weight:700;"
            f"padding:3px 8px;border-radius:6px;background:{bg};color:{fg}'>{lab}</span></td>"
            + cases_cell +
            f"<td style='padding:12px 8px;text-align:right;font-family:Consolas,monospace;"
            f"font-size:13.5px;font-weight:700;color:#1f2940'>{r['hours']}</td>"
            f"<td style='padding:12px 14px'>{asg}</td></tr>")
    _cols = [("Story", "14px", ""), ("Title", "8px", ""),
             ("Pri", "8px", "text-align:center")]
    if show_cases:
        _cols.append(("Cases", "8px", "text-align:right"))
    _cols += [("Hours", "8px", "text-align:right"), ("Assignee", "14px", "")]
    story_tbl = (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        f"style='border:1px solid #EBEFF7;border-radius:12px'>"
        f"<tr style='background:#F6F8FC'>"
        + "".join(f"<td style='padding:10px {p};font-size:10.5px;letter-spacing:.5px;"
                  f"text-transform:uppercase;color:#98a1b5;font-weight:700;{a}'>{h}</td>"
                  for h, p, a in _cols)
        + "</tr>" + "".join(srows) + "</table>")

    # workload bars
    wl_block = ""
    wl = d.get("workload", [])
    if wl:
        maxw = max((w["hours"] for w in wl), default=0) or 1
        wrows = []
        for w in wl:
            init, acol = _av(w["name"])
            pct = max(4, int(round(w["hours"] / maxw * 100)))
            wrows.append(
                f"<tr><td width='118' style='padding:7px 0'>"
                f"<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
                f"<td width='22' style='vertical-align:middle'>"
                f"<div style='width:22px;height:22px;line-height:22px;border-radius:6px;"
                f"background:{acol};color:#fff;text-align:center;font-size:10px;"
                f"font-weight:700;font-family:Segoe UI,Arial,sans-serif'>{init}</div></td>"
                f"<td style='padding-left:9px;font-size:13px;font-weight:700;color:#1f2940;"
                f"vertical-align:middle'>"
                f"{w['name']}</td></tr></table></td>"
                f"<td style='padding:7px 14px'>"
                f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
                f"style='background:#eef1f7;border-radius:99px'><tr>"
                f"<td height='8' style='background:{acol};border-radius:99px;width:{pct}%;"
                f"font-size:0;line-height:0'>&nbsp;</td>"
                f"<td style='font-size:0;line-height:0'>&nbsp;</td></tr></table></td>"
                f"<td width='118' align='right' style='padding:7px 0;white-space:nowrap'>"
                f"<span style='font-size:11.5px;color:#8a93a8'>{w['stories']} stories</span>"
                f"<span style='font-family:Consolas,monospace;font-size:14px;font-weight:700;"
                f"color:#1f2940;padding-left:8px'>{w['hours']} h</span></td></tr>")
        wl_block = (
            f"<tr><td style='padding:22px 32px 4px'>"
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
            f"<td style='font-size:11px;letter-spacing:.7px;text-transform:uppercase;"
            f"color:#8a93a8;font-weight:700'>Resource workload</td>"
            f"<td align='right'><span style='font-size:11.5px;font-weight:600;color:#1F9D57;"
            f"background:#E5F6EC;padding:5px 11px;border-radius:999px'>"
            f"&#8776; {d['hours_per_person']} h / person</span></td></tr></table>"
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='margin-top:12px'>" + "".join(wrows) + "</table></td></tr>")

    scope = d["plan_name"] or d["project"]
    return (
        f"<div style='background:#e9edf4;padding:28px 12px;"
        f"font-family:Segoe UI,Arial,sans-serif'>"
        f"<table role='presentation' align='center' width='680' cellpadding='0' "
        f"cellspacing='0' style='max-width:680px;width:100%;margin:0 auto;background:#fff;"
        f"border-radius:16px;overflow:hidden;border:1px solid #e4e9f2'>"
        # header band
        f"<tr><td style='padding:26px 32px 22px;background:#3A57D6;"
        f"background-image:linear-gradient(125deg,#1C80E0 0%,#3A57D6 55%,#6A33A8 100%)'>"
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
        f"<td style='color:#fff;font-weight:800;font-size:15px;letter-spacing:.2px'>"
        f"QA&nbsp;Studio</td>"
        f"<td align='right'><span style='font-family:Consolas,monospace;font-size:11px;"
        f"color:#d6ddf6;background:rgba(255,255,255,.14);padding:6px 11px;"
        f"border-radius:8px'>generated {d['generated']}</span></td></tr></table>"
        f"<div style='margin-top:20px;color:#fff;font-size:25px;font-weight:800;"
        f"letter-spacing:-.5px'>{d.get('report_title', 'Regression Test Plan')}</div>"
        f"<div style='margin-top:6px;color:#cdd5f0;font-size:13.5px;font-weight:500'>"
        f"{scope}</div></td></tr>"
        # KPI strip
        f"<tr><td style='padding:22px 27px 6px'>{kpis}</td></tr>"
        # story table
        f"<tr><td style='padding:16px 32px 6px'>"
        f"<div style='font-size:11px;letter-spacing:.7px;text-transform:uppercase;"
        f"color:#8a93a8;font-weight:700;margin-bottom:12px'>Stories in scope</div>"
        f"{story_tbl}</td></tr>"
        + wl_block +
        # footer
        f"<tr><td style='padding:22px 32px 26px'>"
        f"<div style='border-top:1px solid #eef1f7;padding-top:18px;font-size:12px;"
        f"color:#9aa4b8;line-height:1.6'>Sent automatically from "
        f"<b style='color:#6b7790'>QA Studio</b>. The full plan is attached as a Word "
        f"document." + (f"<br>Estimates use {d['avg_minutes_per_case']}&nbsp;min / test "
        f"case weighted by Azure DevOps priority." if show_cases else
        f"<br>Effort is estimated per story and balanced across the team.")
        + f"</div></td></tr>"
        f"</table></div>")



def _out_dir():
    d = os.path.join(os.path.expanduser("~"), "QA Studio", "Regression Plans")
    os.makedirs(d, exist_ok=True)
    return d


def _stamp(app):
    if getattr(app, "_reg_mode", "existing") == "create":
        base = getattr(app, "_cp_sprint_name", "") or "sprint"
    else:
        base = ((getattr(app, "plan_name", "") or "")
                or (", ".join(p["name"] for p in (app._reg_plans_selected or [])) or "plan"))
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_") or "plan"
    prefix = "SprintPlan" if getattr(app, "_reg_mode", "existing") == "create" else "RegressionPlan"
    return f"{prefix}_{base}_{datetime.now():%Y%m%d-%H%M}"


def _ask_save_path(fmt, default_name):
    """Open a native OS 'Save As' dialog (tkinter) and return the chosen path.
        str   -> the path the user picked
        None  -> the user cancelled
        False -> no native dialog available (caller should fall back)
    Must be called OFF the UI thread — it spins up its own hidden Tk root."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return False
    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        path = filedialog.asksaveasfilename(
            parent=root, title="Save regression plan",
            initialfile=default_name, defaultextension="." + fmt,
            filetypes=[(f"{fmt.upper()} file", f"*.{fmt}"), ("All files", "*.*")])
        try:
            root.update(); root.destroy()
        except Exception:
            pass
        return path or None
    except Exception:
        return False


def export_json(app):
    p = os.path.join(_out_dir(), _stamp(app) + ".json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(plan_payload(app), f, ensure_ascii=False, indent=2)
    return p


def export_xlsx(app):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Border, Side
    d = plan_payload(app)
    wb = Workbook()
    ws = wb.active
    ws.title = d.get("report_title", "Regression Plan")[:31]
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="6A4DFF")
    thin = Border(*[Side(style="thin", color="E3E0EC")] * 4)
    r = 1
    for k, v in (("Project", d["project"]), ("Test plan", d["plan_name"]),
                 ("Plan ID", d["plan_id"]),
                 ("Generated", d["generated"]),
                 ("Avg min / case", d["avg_minutes_per_case"]),
                 ("Resources", d["resources_count"]),
                 ("Resource names", ", ".join(d["resource_names"]) or "—")):
        ws.cell(r, 1, k).font = Font(bold=True)
        ws.cell(r, 2, v)
        r += 1
    r += 1
    for c, name in enumerate(["Story ID", "Title", "State", "Priority",
                              "Test cases", "Est. hours", "Assignee"], 1):
        cell = ws.cell(r, c, name)
        cell.font = head
        cell.fill = fill
    r += 1
    for s in d["stories"]:
        vals = [s["id"], s["title"], s["state"],
                _PRI_FULL.get(s["priority"], s["priority"]), s["cases"],
                s["hours"], s.get("assignee") or "—"]
        for c, v in enumerate(vals, 1):
            ws.cell(r, c, v).border = thin
        r += 1
    ws.cell(r, 4, "TOTAL").font = Font(bold=True)
    ws.cell(r, 5, d["total_cases"]).font = Font(bold=True)
    ws.cell(r, 6, d["total_hours"]).font = Font(bold=True)
    r += 2
    if d["workload"]:
        ws.cell(r, 1, "Resource workload").font = Font(bold=True, size=12)
        r += 1
        for c, name in enumerate(["Resource", "Stories", "Test cases", "Hours"], 1):
            cell = ws.cell(r, c, name)
            cell.font = head
            cell.fill = fill
        r += 1
        for w in d["workload"]:
            ws.cell(r, 1, w["name"])
            ws.cell(r, 2, w["stories"])
            ws.cell(r, 3, w.get("cases", 0))
            ws.cell(r, 4, w["hours"])
            r += 1
    for c, wdt in zip("ABCDEFG", [12, 52, 14, 16, 12, 12, 18]):
        ws.column_dimensions[c].width = wdt
    p = os.path.join(_out_dir(), _stamp(app) + ".xlsx")
    wb.save(p)
    return p


def export_docx(app):
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    d = plan_payload(app)
    doc = Document()
    h = doc.add_heading(d.get("report_title", "Regression Test Plan"), level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rn = sub.add_run(f"{d['plan_name'] or d['project']}")
    rn.font.size = Pt(11)
    rn.font.color.rgb = RGBColor(0x6A, 0x4D, 0xFF)
    meta = doc.add_table(rows=0, cols=2)
    meta.style = "Light List Accent 1"
    for k, v in (("Project", d["project"]), ("Test plan", d["plan_name"]),
                 ("Plan ID", str(d["plan_id"])),
                 ("Generated", d["generated"]),
                 ("Resources", str(d["resources_count"])),
                 ("Resource names", ", ".join(d["resource_names"]) or "—"),
                 ("Effort model", f"{d['avg_minutes_per_case']} min/case "
                                   f"+ priority weighting")):
        cells = meta.add_row().cells
        cells[0].text = k
        cells[1].text = str(v)
        for p in cells[0].paragraphs:
            for x in p.runs:
                x.font.bold = True
    doc.add_heading("Stories", level=1)
    tbl = doc.add_table(rows=1, cols=7)
    tbl.style = "Medium Shading 1 Accent 1"
    for i, hd in enumerate(["Story", "Title", "State", "Priority",
                            "Test cases", "Est. hours", "Assignee"]):
        tbl.rows[0].cells[i].text = hd
    for s in d["stories"]:
        c = tbl.add_row().cells
        c[0].text = str(s["id"])
        c[1].text = s["title"]
        c[2].text = s["state"]
        c[3].text = _PRI_FULL.get(s["priority"], str(s["priority"]))
        c[4].text = str(s["cases"])
        c[5].text = str(s["hours"])
        c[6].text = s.get("assignee") or "—"
    doc.add_paragraph()
    tot = doc.add_table(rows=0, cols=2)
    tot.style = "Light List Accent 1"
    for k, v in (("Total stories", d["total_stories"]),
                 ("Total test cases", d["total_cases"]),
                 ("Total estimated hours", d["total_hours"]),
                 ("Hours per resource (target)", d["hours_per_person"])):
        cells = tot.add_row().cells
        cells[0].text = k
        cells[1].text = str(v)
        for p in cells[0].paragraphs:
            for x in p.runs:
                x.font.bold = True
    if d["workload"]:
        doc.add_heading("Resource workload", level=1)
        wt = doc.add_table(rows=1, cols=4)
        wt.style = "Medium Shading 1 Accent 1"
        for i, hd in enumerate(["Resource", "Stories", "Test cases", "Hours"]):
            wt.rows[0].cells[i].text = hd
        for w in d["workload"]:
            c = wt.add_row().cells
            c[0].text = w["name"]
            c[1].text = str(w["stories"])
            c[2].text = str(w.get("cases", 0))
            c[3].text = str(w["hours"])
    foot = doc.add_paragraph()
    fr = foot.add_run("Generated by QA Studio · effort references existing "
                      "test cases only.")
    fr.font.size = Pt(8)
    fr.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
    p = os.path.join(_out_dir(), _stamp(app) + ".docx")
    doc.save(p)
    return p


def export_pdf(app):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                    Paragraph, Spacer)
    from reportlab.lib.styles import getSampleStyleSheet
    d = plan_payload(app)
    p = os.path.join(_out_dir(), _stamp(app) + ".pdf")
    doc = SimpleDocTemplate(p, pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm)
    styles = getSampleStyleSheet()
    elems = [Paragraph("Regression Test Plan", styles["Title"]),
             Paragraph(f"{d['plan_name'] or d['project']}",
                       styles["Normal"]),
             Spacer(1, 8 * mm)]
    data = [["Story", "Title", "State", "Pri", "Cases", "Hours", "Assignee"]]
    for s in d["stories"]:
        data.append([str(s["id"]), (s["title"] or "")[:38], s["state"],
                     str(s["priority"]), str(s["cases"]), str(s["hours"]),
                     s.get("assignee") or "—"])
    data.append(["", "", "", "TOT", str(d["total_cases"]),
                 str(d["total_hours"]), ""])
    tbl = Table(data, colWidths=[18*mm, 54*mm, 22*mm, 10*mm, 14*mm, 14*mm, 28*mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6A4DFF")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2),
         [colors.white, colors.HexColor("#F7F6FF")]),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F2F0FF")),
    ]))
    elems += [tbl, Spacer(1, 6 * mm)]
    if d["workload"]:
        wd = [["Resource", "Stories", "Test cases", "Hours"]] + \
             [[w["name"], str(w["stories"]), str(w.get("cases", 0)), str(w["hours"])]
              for w in d["workload"]]
        wt = Table(wd, colWidths=[55*mm, 25*mm, 30*mm, 25*mm])
        wt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6A4DFF")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
        ]))
        elems += [Paragraph("Resource workload", styles["Heading2"]), wt]
    doc.build(elems)
    return p


EXPORTERS = {"docx": export_docx, "xlsx": export_xlsx,
             "json": export_json, "pdf": export_pdf}
_MISSING_DEP = {"xlsx": "openpyxl  ·  pip install openpyxl",
                "docx": "python-docx  ·  pip install python-docx",
                "pdf":  "reportlab  ·  pip install reportlab"}


def _export_row(app, set_status=None):
    """Export buttons for the sprint report. Each saves via the shared exporter
    (which now reads the mode-aware payload) into ~/QA Studio/Regression Plans.

    set_status(kind, text) -- if provided, called in-place instead of render().
    """
    from main import green_btn, ghost_btn

    def _notify(kind, text):
        app._cp_msg = (kind, text)
        try:
            app.ui_safe(lambda k=kind, t=text: (app._toast(t) if k == "ok"
                                                else app._err(t)))
            return
        except Exception:
            pass
        if set_status is not None:
            try:
                app.ui_safe(lambda k=kind, t=text: set_status(k, t))
                return
            except Exception:
                pass
        app.ui_safe(app.render)

    def _go(fmt):
        def _do(e):
            if not app._cp_rows:
                _notify("err", "Pick a sprint first.")
                return

            def work():
                try:
                    dest = _ask_save_path(fmt, _stamp(app) + "." + fmt)
                    if dest is None:
                        return                       # user cancelled the dialog
                    path = EXPORTERS[fmt](app)
                    if dest and dest is not False:   # a path was chosen → move there
                        if not dest.lower().endswith("." + fmt):
                            dest += "." + fmt
                        import shutil
                        if os.path.abspath(dest) != os.path.abspath(path):
                            shutil.move(path, dest)
                        path = dest
                    try:
                        os.startfile(os.path.dirname(path))
                    except Exception:
                        pass
                    _notify("ok", f"Saved: {path}")
                except ModuleNotFoundError:
                    _notify("err", f"Missing dependency: {_MISSING_DEP.get(fmt, fmt)}")
                except Exception as ex:
                    _notify("err", f"Export failed: {str(ex)[:160]}")
            threading.Thread(target=work, daemon=True).start()
        return _do

    def _exp_btn(label, icon, color, fmt):
        return ft.OutlinedButton(
            content=ft.Row([ft.Icon(icon, size=17, color=color),
                            ft.Text(label, size=13.5, weight=ft.FontWeight.W_600,
                                    color=T.INK)],
                           spacing=8, tight=True),
            on_click=_go(fmt), height=44,
            style=ft.ButtonStyle(
                bgcolor={"": "#FFFFFF"},
                side=ft.BorderSide(1, T.BORDER),
                shape=ft.RoundedRectangleBorder(radius=T.R),
                padding=ft.Padding.symmetric(horizontal=15, vertical=0)))

    btns = ft.Row([
        _exp_btn("Word", ft.Icons.DESCRIPTION, T.BRAND_GRAD_1, "docx"),
        _exp_btn("Excel", ft.Icons.TABLE_CHART, T.GREEN, "xlsx"),
        _exp_btn("PDF", ft.Icons.PICTURE_AS_PDF, T.RED, "pdf"),
        _exp_btn("JSON", ft.Icons.DATA_OBJECT, T.STORY, "json"),
    ], spacing=8, wrap=True)

    _m_kind = app._cp_msg[0] if app._cp_msg else "ok"
    _m_text = app._cp_msg[1] if app._cp_msg else ""
    _m_col = T.GREEN if _m_kind == "ok" else T.RED
    _status_icon = ft.Icon(
        ft.Icons.CHECK_CIRCLE if _m_kind == "ok" else ft.Icons.ERROR_OUTLINE,
        size=15, color=_m_col)
    _status_text = _txt(_m_text, color=_m_col, size=12, expand=True)
    status = ft.Container(
        ft.Row([_status_icon, _status_text], spacing=8),
        padding=10, border_radius=T.R, margin=ft.Margin.only(top=10),
        bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER_2),
        visible=bool(app._cp_msg))

    if set_status is None:
        # legacy: caller didn't provide a callback, nothing to wire
        pass
    # Note: set_status wiring happens externally after this returns

    return ft.Column([btns, status], spacing=0), status


# ═══════════════════════════════════════════════════════════════════════════════
#  UI helpers
# ═══════════════════════════════════════════════════════════════════════════════
def _pill(text, fg, bg):
    return ft.Container(ft.Text(text, size=11, weight=ft.FontWeight.BOLD, color=fg),
                        padding=ft.Padding.symmetric(vertical=2, horizontal=8),
                        bgcolor=bg, border_radius=20)


def _state_pill(state):
    s = (state or "").lower()
    if s in ("closed", "done", "resolved", "completed"):
        return _pill(state, T.GREEN, T.GREEN_SOFT)
    if s in ("active", "in progress", "committed"):
        return _pill(state, T.VIOLET_INK, T.VIOLET_SOFT)
    return _pill(state or "—", T.INK_2, T.CARD_2)


def _pri_pill(pri):
    if pri == 1:
        return _pill("P1", T.RED, T.RED_SOFT)
    if pri == 2:
        return _pill("P2", T.AMBER, T.AMBER_SOFT)
    if pri == 4:
        return _pill("P4", T.GREEN, T.GREEN_SOFT)
    return _pill(f"P{pri}", T.INK_2, T.CARD_2)


def _txt(s, **kw):
    kw.setdefault("size", 12)
    return ft.Text(s, **kw)


def _avatar(name, size=26):
    """Round initial-avatar; colour is stable per name."""
    init, col = _av(name)
    return ft.Container(
        ft.Text(init, size=int(size * 0.42), weight=ft.FontWeight.BOLD, color="#FFFFFF"),
        width=size, height=size, bgcolor=col, border_radius=size,
        alignment=ft.Alignment.CENTER)


def _bar(frac, color=T.VIOLET, h=7):
    """Proportional fill bar (animates smoothly when its weight changes)."""
    f = max(1, int(round((frac or 0) * 100)))
    e = max(0, 100 - f)
    fill = ft.Container(height=h, bgcolor=color, border_radius=4, expand=f,
                        animate=700)
    inner = [fill] + ([ft.Container(expand=e)] if e > 0 else [])
    return ft.Container(ft.Row(inner, spacing=0), bgcolor=T.BORDER_2,
                        border_radius=4, height=h, clip_behavior=ft.ClipBehavior.HARD_EDGE)


def _kpi_tile(label, value, accent=T.INK):
    return ft.Container(
        ft.Column([
            ft.Text(label, size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Text(value, size=23, weight=ft.FontWeight.BOLD, color=accent,
                    font_family=T.F_MONO),
        ], spacing=4),
        expand=True, padding=14, bgcolor=T.CARD_2, border_radius=T.R,
        border=ft.Border.all(1, T.BORDER_2))


def locked_state(app, title, sub, msg, icon=None, steps=None):
    """Shared centered 'connect / select first' screen (also used by Automation).

    `steps` is an optional list of (icon, label) tuples shown as a 3-step path.
    """
    from main import primary_btn
    icon = icon or ft.Icons.LINK_OFF
    steps = steps or [(ft.Icons.TUNE, "Connect"),
                      (ft.Icons.CHECKLIST, "Select"),
                      (ft.Icons.AUTO_AWESOME, "Generate")]

    # "scanning for a connection" card — ft.ProgressBar(value=None) animates natively in Flet
    scan_card = ft.Container(
        ft.Column([
            ft.Container(ft.Icon(icon, size=28, color=T.VIOLET),
                         width=60, height=60, bgcolor=T.VIOLET_SOFT, border_radius=18,
                         alignment=ft.Alignment.CENTER),
            ft.Container(height=16),
            ft.ProgressBar(value=None, color=T.VIOLET, bgcolor=T.BORDER_2,
                           bar_height=6, border_radius=99, width=224),
            ft.Container(height=10),
            ft.Text("Scanning for a connection…", size=11, color=T.INK_3,
                    weight=ft.FontWeight.W_500, font_family=T.F_MONO),
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
        width=296, padding=ft.Padding.symmetric(vertical=20, horizontal=26),
        bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER_2), border_radius=22)

    pill = ft.Container(
        ft.Row([ft.Container(width=7, height=7, border_radius=7, bgcolor=T.STORY),
                ft.Text("Awaiting connection", size=11, weight=ft.FontWeight.BOLD,
                        color=T.STORY)], spacing=7, tight=True),
        padding=ft.Padding.symmetric(vertical=6, horizontal=12),
        bgcolor=T.VIOLET_SOFT, border_radius=999,
        border=ft.Border.all(1, "#E0E5FF"))

    def _step(ic, label):
        return ft.Container(ft.Column([
            ft.Container(ft.Icon(ic, size=18, color=T.VIOLET), width=44, height=44,
                         bgcolor=T.VIOLET_SOFT, border_radius=13,
                         alignment=ft.Alignment.CENTER,
                         border=ft.Border.all(1, "#E0E5FF")),
            ft.Text(label, size=12, weight=ft.FontWeight.BOLD, color=T.INK_2),
        ], spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER), width=110)

    path = ft.Row([_step(ic, lab) for ic, lab in steps],
                  alignment=ft.MainAxisAlignment.CENTER, spacing=0)

    body = ft.Container(
        ft.Column([
            scan_card,
            ft.Container(height=12), pill,
            ft.Container(height=10),
            ft.Text("A few things first", size=20, weight=ft.FontWeight.BOLD,
                    color=T.INK),
            ft.Container(height=8),
            ft.Container(ft.Text(msg, size=13.5, color=T.INK_2,
                                 text_align=ft.TextAlign.CENTER), width=470),
            ft.Container(height=16), path,
            ft.Container(height=18),
            primary_btn("Go to Setup", icon=ft.Icons.ARROW_FORWARD,
                        on_click=lambda e: app.goto("setup")),
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
           alignment=ft.MainAxisAlignment.CENTER, tight=True),
        alignment=ft.Alignment.CENTER, expand=True,
        padding=ft.Padding.symmetric(vertical=18, horizontal=20))
    return app.shell(title, sub, body)


# ═══════════════════════════════════════════════════════════════════════════════
#  SCREEN
# ═══════════════════════════════════════════════════════════════════════════════
def _init(app):
    for k, v in (("_reg_plans_selected", []), ("_reg_plan_stories", []),
                 ("_reg_stories_loading", False), ("_reg_selected", []),
                 ("_reg_plan_open", False), ("_reg_story_open", False),
                 ("_cp_sprint_open", False),
                 ("_reg_selected_rows", []), ("_reg_res_names", []),
                 ("_reg_res_count", None), ("_reg_busy", False),
                 ("_reg_export_msg", None), ("_reg_calc_msg", None),
                 ("_reg_email_to", ""), ("_reg_emailing", False),
                 ("_reg_plans_loading", False),
                 ("_reg_mode", "existing"),
                 ("_cp_iterations", []), ("_cp_iter_loading", False),
                 ("_cp_sprint_paths", []), ("_cp_sprint_name", ""),
                 ("_cp_stories_loading", False),
                 ("_cp_rows", []), ("_cp_res_names", []),
                 ("_cp_est_min", 1.0), ("_cp_est_max", 8.0),
                 ("_cp_res_count", None), ("_cp_calculated", False),
                 ("_cp_calc_msg", None),
                 ("_cp_msg", None), ("_cp_assigning", False),
                 ("_cp_email_to", ""), ("_cp_emailing", False),
                 ("_reg_suite_cache", {})):
        if not hasattr(app, k):
            setattr(app, k, v)


def _sprint_num(text):
    """Pull a clean 'Sprint N' out of an iteration path or plan name; '' if none."""
    m = re.search(r"[Ss]print\s*\d+", text or "")
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else ""


def _reload_plan_stories(app):
    """Aggregate the user stories that live in the currently-selected test plans
    (their requirement suites). Runs off the UI thread."""
    plans = list(app._reg_plans_selected or [])
    if not plans:
        app._reg_plan_stories = []
        app.ui_safe(app.render)
        return
    app._reg_stories_loading = True
    app.ui_safe(app.render)

    def _work():
        import concurrent.futures as _cf_reload

        def _fetch_one_plan(p):
            # Fetch the plan's sprint/iteration label and its requirement-based stories
            # concurrently for each selected plan instead of one-by-one.
            try:
                pj = E._azure_get(f"https://dev.azure.com/{E.AZURE_ORG}/{app.project}"
                                  f"/_apis/testplan/plans/{p['id']}?api-version=7.0")
                itr = pj.get("iteration") or ""
                sprint = _sprint_num(itr) or _sprint_num(p.get("name", "")) \
                    or itr.split("\\")[-1]
            except Exception:
                sprint = p.get("sprint", "")
            try:
                stories = E.fetch_stories_in_plan(app.project, p["id"])
            except Exception:
                stories = []
            return p["id"], sprint, stories

        agg, seen = [], set()
        with _cf_reload.ThreadPoolExecutor(max_workers=min(8, len(plans))) as ex:
            plan_results = list(ex.map(_fetch_one_plan, plans))

        # Write sprint back to the shared plan dicts (maintain plan order for dedup)
        plan_sprint = {}
        for pid, sprint, _ in plan_results:
            plan_sprint[pid] = sprint
        for p in plans:
            p["sprint"] = plan_sprint.get(p["id"], p.get("sprint", ""))

        # Aggregate stories in original plan order so sprint grouping is stable
        for pid, sprint, stories in plan_results:
            for s in stories:
                key = s["id"]   # dedupe by story id (a story in 2 plans = one entry)
                if key in seen:
                    continue
                seen.add(key)
                # group under the PLAN's sprint (what the user picked), not the
                # story's own iteration — so only selected sprints appear
                agg.append({"id": s["id"], "title": s.get("title", ""),
                            "sprint": sprint or _sprint_num(s.get("sprint", "")),
                            "plan_id": pid})
        # group by sprint then id so the picker lists them clustered by sprint
        agg.sort(key=lambda s: (s.get("sprint", "") or "~", s["id"]))
        app._reg_plan_stories = agg
        app._reg_stories_loading = False
        app.ui_safe(app.render)
    threading.Thread(target=_work, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════════════
#  SPRINT REPORT (mode = "create") — a regression-style report built from a sprint
#  and its user stories, with a random (editable) per-story estimate and a random
#  (editable) assignment. Nothing is written to Azure here.
# ═══════════════════════════════════════════════════════════════════════════════
def _cp_is_sprint(it):
    """Keep only iteration nodes that look like a sprint (have 'Sprint N')."""
    return bool(_sprint_num(it.get("name", "")) or _sprint_num(it.get("path", "")))


def _cp_load_iterations(app):
    if app._cp_iterations or app._cp_iter_loading:
        return
    app._cp_iter_loading = True
    app.ui_safe(app.render)

    def _work():
        try:
            its = E.fetch_iterations(app.project) or []
        except Exception:
            its = []
        sprints = [it for it in its if _cp_is_sprint(it)] or its
        # newest sprint number first
        sprints.sort(key=lambda it: _sprint_sort_key(it), reverse=True)
        app._cp_iterations = sprints
        app._cp_iter_loading = False
        app.ui_safe(app.render)
    threading.Thread(target=_work, daemon=True).start()


def _sprint_sort_key(it):
    m = re.search(r"\d+", _sprint_num(it.get("name", "")) or _sprint_num(it.get("path", "")))
    return int(m.group(0)) if m else -1


def _checkbox_multiselect(options, selected, on_toggle, on_all, *, is_open, on_open,
                          placeholder="Select…", height=240, empty="No options.",
                          page=None, app=None):
    """Collapsible checkbox multiselect.

    When page= is supplied every interaction (open/close AND checkbox tick) is
    handled fully IN-PLACE via control mutation + page.update().  render() is
    never called from inside this component so the scroll position never jumps.

    Callers must NOT call render() inside on_toggle / on_all / on_open when
    page= is provided -- they should only mutate app state.  The component keeps
    its own mutable sel set and syncs all visible refs itself.

    When app= is also supplied the component registers a close() callable in
    app._dd_closers so _close_dropdowns can close it in-place on click-away.
    """
    # mutable state owned by this widget instance
    sel = set(selected or [])
    keys = [k for k, _ in options]

    def _all_on():
        return bool(keys) and all(k in sel for k in keys)

    # refs we need to mutate on tick / open
    field_label_ref = ft.Text(
        (f"{len(sel)} selected" if sel else placeholder),
        size=13, color=(T.INK if sel else T.INK_3), expand=True)
    arrow_icon = ft.Icon(
        ft.Icons.KEYBOARD_ARROW_UP if is_open else ft.Icons.KEYBOARD_ARROW_DOWN,
        size=20, color=T.INK_3)
    select_all_cb = ft.Checkbox(value=_all_on())
    count_text = ft.Text(f"{len(sel)} selected", size=11.5, color=T.INK_3,
                         weight=ft.FontWeight.BOLD)

    # per-row checkbox refs keyed by option key
    row_cbs = {}

    def _refresh_header():
        n = len(sel)
        field_label_ref.value = f"{n} selected" if sel else placeholder
        field_label_ref.color = T.INK if sel else T.INK_3
        count_text.value = f"{n} selected"
        select_all_cb.value = _all_on()

    def _do_toggle(kk, checked):
        if checked:
            sel.add(kk)
        else:
            sel.discard(kk)
        for k2, cb in row_cbs.items():
            if k2 != kk:
                cb.value = (k2 in sel)
        _refresh_header()
        try:
            on_toggle(kk, checked)
        except Exception:
            pass
        if page is not None:
            try:
                page.update()
            except Exception:
                pass

    def _do_all(checked):
        if checked:
            sel.update(keys)
        else:
            sel.clear()
        for k2, cb in row_cbs.items():
            cb.value = (k2 in sel)
        _refresh_header()
        try:
            on_all(checked)
        except Exception:
            pass
        if page is not None:
            try:
                page.update()
            except Exception:
                pass

    select_all_cb.on_change = lambda e: _do_all(e.control.value)

    # build panel rows
    rows = []
    for k, label in options:
        cb = ft.Checkbox(value=(k in sel),
                         on_change=(lambda e, kk=k: _do_toggle(kk, e.control.value)))
        row_cbs[k] = cb
        rows.append(ft.Container(
            ft.Row([cb, ft.Text(label, size=12.5, color=T.INK, expand=True, no_wrap=False)],
                   spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.only(left=10, right=10, top=2, bottom=2)))

    body = ft.Column(rows, spacing=0, scroll=ft.ScrollMode.AUTO) if rows else \
        ft.Container(ft.Text(empty, size=12, color=T.INK_3),
                     padding=14, alignment=ft.Alignment.CENTER)
    panel_h = min(height, max(40, len(rows) * 34 + 8)) if rows else 64

    head = ft.Container(
        ft.Row([select_all_cb,
                ft.Text("Select all", size=12.5, weight=ft.FontWeight.BOLD, color=T.INK),
                ft.Container(expand=True),
                count_text],
               spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.Padding.symmetric(vertical=8, horizontal=10), bgcolor=T.CARD_2,
        border_radius=ft.BorderRadius.only(top_left=T.R, top_right=T.R))

    panel_body = ft.Column([
        head,
        ft.Container(body, height=panel_h, padding=ft.Padding.symmetric(vertical=4),
                     border=ft.Border.all(1, T.BORDER),
                     border_radius=ft.BorderRadius.only(bottom_left=T.R, bottom_right=T.R)),
    ], spacing=0)
    panel_wrap = ft.Container(panel_body, padding=ft.Padding.only(top=6),
                              visible=is_open)

    def _do_open(e):
        new_open = not panel_wrap.visible
        panel_wrap.visible = new_open
        arrow_icon.name = (ft.Icons.KEYBOARD_ARROW_UP if new_open
                           else ft.Icons.KEYBOARD_ARROW_DOWN)
        field_container.border = ft.Border.all(1, T.VIOLET if new_open else T.BORDER)
        try:
            on_open()
        except Exception:
            pass
        if page is not None:
            try:
                page.update()
            except Exception:
                pass

    # Register a close() callable so _close_dropdowns can close this in-place.
    def _close_this():
        if panel_wrap.visible:
            panel_wrap.visible = False
            arrow_icon.name = ft.Icons.KEYBOARD_ARROW_DOWN
            field_container.border = ft.Border.all(1, T.BORDER)
            try:
                on_open()   # sync the flag
            except Exception:
                pass
            return True
        return False

    if app is not None:
        try:
            app._dd_closers.append(_close_this)
        except Exception:
            pass

    field_container = ft.Container(
        ft.Row([field_label_ref, arrow_icon],
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        on_click=_do_open if page is not None else (lambda e: on_open()),
        padding=ft.Padding.symmetric(vertical=12, horizontal=12),
        bgcolor=T.CARD,
        border=ft.Border.all(1, T.VIOLET if is_open else T.BORDER),
        border_radius=T.R)

    # legacy path (no page=)
    if page is None:
        if not is_open:
            return field_container
        return ft.Column([field_container,
                          ft.Container(panel_body, padding=ft.Padding.only(top=6))],
                         spacing=0)

    # in-place path
    return ft.Column([field_container, panel_wrap], spacing=0)

def _cp_load_stories(app):
    paths = list(app._cp_sprint_paths or [])
    if not paths:
        app._cp_rows = []
        app.ui_safe(app.render)
        return
    app._cp_stories_loading = True
    app._cp_rows = []
    app.ui_safe(app.render)

    def _work():
        agg, seen = [], set()
        for path in paths:
            try:
                stories = E.fetch_stories_in_iteration(app.project, path) or []
            except Exception:
                stories = []
            for s in stories:
                if s["id"] in seen:
                    continue
                seen.add(s["id"])
                agg.append({"id": s["id"], "title": s.get("title", ""),
                            "hours": 0.0, "assignee": ""})
        app._cp_rows = agg
        # Pull real Azure DevOps priority (+ state) for these stories so the plan
        # table and email show P1–P4 like the Regression Plan report (not a bare "P").
        try:
            meta = _fetch_meta(app, [int(r["id"]) for r in agg])
            for r in agg:
                m = meta.get(int(r["id"]), {})
                r["priority"] = m.get("priority", DEFAULT_PRIORITY)
                if m.get("state"):
                    r["state"] = m["state"]
        except Exception:
            pass
        _cp_estimate_and_assign(app)
        app._cp_stories_loading = False
        app.ui_safe(app.render)
    threading.Thread(target=_work, daemon=True).start()


def _cp_estimate_and_assign(app):
    """Give every story a stable random estimate (seeded by its id so it doesn't
    jump around on every render) and a random — but balanced — assignee. Both stay
    editable afterwards."""
    import random
    names = list(app._cp_res_names or [])
    lo, hi = float(app._cp_est_min or 1.0), float(app._cp_est_max or 8.0)
    if hi < lo:
        lo, hi = hi, lo
    rows = app._cp_rows or []
    # estimate: deterministic per story id, in 0.5h steps within [lo, hi]
    steps = max(1, int(round((hi - lo) / 0.5)))
    for r in rows:
        rnd = random.Random(r["id"])               # seeded → stable per story
        r["hours"] = round(lo + 0.5 * rnd.randint(0, steps), 2)
    # assignment: shuffle (seeded by the set of ids so it's stable) then round-robin
    if names:
        order = list(range(len(rows)))
        random.Random(sum(r["id"] for r in rows)).shuffle(order)
        for k, idx in enumerate(order):
            rows[idx]["assignee"] = names[k % len(names)]
    else:
        for r in rows:
            r["assignee"] = ""


def _cp_payload(app):
    """Build the same payload shape plan_payload() returns, from the sprint rows —
    so every exporter and the email path work unchanged."""
    names = list(app._cp_res_names or [])
    rows = [{"id": r["id"], "title": r.get("title", ""), "state": r.get("state", ""),
             "priority": r.get("priority", DEFAULT_PRIORITY), "cases": 0, "boost": 1.0,
             "hours": round(float(r.get("hours", 0) or 0), 2),
             "assignee": r.get("assignee", "")} for r in (app._cp_rows or [])]
    total_hours = round(sum(r["hours"] for r in rows), 2)
    count = len(names) or 1
    per_person = round(total_hours / count, 2)
    workload = []
    if names:
        st = {n: 0 for n in names}
        hr = {n: 0.0 for n in names}
        for r in rows:
            a = r.get("assignee")
            if a in st:
                st[a] += 1
                hr[a] += r["hours"]
        workload = [{"name": n, "stories": st[n], "cases": 0,
                     "hours": round(hr[n], 2)} for n in names]
    return {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "project": app.project, "plan_id": "",
            "plan_name": app._cp_sprint_name or ", ".join(app._cp_sprint_paths or []),
            "report_title": "Sprint Plan", "mode": "create",
            "plans": [], "avg_minutes_per_case": 0,
            "priority_boost": {}, "resources_count": len(names),
            "resource_names": names, "stories": rows, "workload": workload,
            "total_stories": len(rows), "total_cases": 0,
            "total_hours": total_hours, "hours_per_person": per_person}


def _mode_toggle(app):
    """Segmented switch between the existing 'from test plans' report and the new
    'from a sprint' report."""
    def _seg(label, mode_id):
        on = (getattr(app, "_reg_mode", "existing") == mode_id)
        def _go(e, m=mode_id):
            app._reg_mode = m
            app.ui_safe(app.render)
        return ft.Container(
            ft.Text(label, size=12.5, weight=ft.FontWeight.BOLD,
                    color=("#FFFFFF" if on else T.INK_3)),
            on_click=_go, padding=ft.Padding.symmetric(vertical=8, horizontal=16),
            bgcolor=(T.VIOLET if on else T.CARD), border_radius=T.R,
            border=ft.Border.all(1, T.VIOLET if on else T.BORDER_2))
    return ft.Row([_seg("From test plans", "existing"),
                   _seg("From a sprint", "create")], spacing=8)


def test_plan_screen(app):
    """Entry point for the 'Test Plan' nav tab — a sprint-based effort report."""
    _init(app)
    from main import card, sec_head, field_label, green_btn, ghost_btn  # noqa: F401
    if not (app.connected and app.project):
        return locked_state(
            app, "Sprint Plan",
            "Plan & estimate test effort across a sprint’s stories",
            "Connect your Azure DevOps account on the Setup screen, then pick a "
            "sprint here.")
    app._reg_mode = "create"
    return _create_screen(app)


def _create_screen(app):
    from main import (card, sec_head, field_label, green_btn, ghost_btn,
                      primary_btn, searchable_dropdown)
    _cp_load_iterations(app)
    _flush_toasts(app)

    names = list(app._cp_res_names or [])
    count = app._cp_res_count
    mismatch = bool(count) and bool(names) and count != len(names)

    # ── Card 1: sprint(s) — checkbox multiselect with Select all ──
    def _sprint_names():
        names = []
        for p in app._cp_sprint_paths:
            it = next((x for x in app._cp_iterations if x["path"] == p), None)
            if it:
                names.append(_sprint_num(it["name"]) or it["name"])
        return names

    def _after_sprint_change():
        app._cp_sprint_name = ", ".join(_sprint_names())
        app._cp_calculated = False
        app._cp_calc_msg = None
        _cp_load_stories(app)

    def _toggle_sprint(key, checked):
        s = set(app._cp_sprint_paths)
        s.add(key) if checked else s.discard(key)
        app._cp_sprint_paths = [p for p in (x["path"] for x in app._cp_iterations) if p in s]
        _after_sprint_change()

    def _all_sprints(checked):
        app._cp_sprint_paths = [x["path"] for x in app._cp_iterations] if checked else []
        _after_sprint_change()

    def _open_sprints():
        app._cp_sprint_open = not app._cp_sprint_open
        # flag synced; in-place toggle handled by the component itself

    sprint_picker = (
        ft.Container(_txt("Loading sprints…", color=T.INK_3, size=12), padding=10)
        if app._cp_iter_loading else
        _checkbox_multiselect(
            [(it["path"], (_sprint_num(it["name"]) or it["name"]) + f"   ·   {it['path']}")
             for it in app._cp_iterations],
            app._cp_sprint_paths, _toggle_sprint, _all_sprints,
            is_open=app._cp_sprint_open, on_open=_open_sprints,
            placeholder="Select sprint(s)",
            empty="No sprints found for this project.",
            page=app.page, app=app))

    picked = ft.Container()
    if app._cp_sprint_paths:
        snames = _sprint_names()
        sprint_chips = ft.Row(
            [ft.Container(
                ft.Row([
                    ft.Text(n, size=12, weight=ft.FontWeight.BOLD, color=T.VIOLET_INK,
                            font_family=T.F_MONO),
                ], spacing=4, tight=True),
                padding=ft.Padding.symmetric(vertical=4, horizontal=10),
                bgcolor=T.VIOLET_SOFT, border_radius=T.R_SM,
                border=ft.Border.all(1, "#D9D2FF"))
             for n in snames],
            wrap=True, spacing=6, run_spacing=6)
        picked = ft.Container(
            ft.Column([
                ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE, size=15, color=T.GREEN),
                        ft.Column([sprint_chips], expand=True),
                        _txt(("Loading stories…" if app._cp_stories_loading
                              else f"· {len(app._cp_rows)} stories"), color=T.INK_3)],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.START),
            ], spacing=0),
            padding=ft.Padding.only(top=10))

    card1 = card(ft.Column([
        sec_head("1", "Sprint"),
        ft.Container(height=10),
        ft.Column([field_label("Sprints", req=True), sprint_picker], spacing=6),
        picked,
    ], spacing=0))

    # ── Card 2: resources (count + names + chips), like the Regression screen ──
    # mutable cell so _refresh_chips_inplace can enable calc_btn after it's built
    _cp_calc_btn_cell = [None]

    def _refresh_chips_inplace():
        name_chips.controls = [_res_chip(n) for n in app._cp_res_names]
        has = bool(app._cp_res_names)
        name_chips_wrap.visible = has
        res_count_text.visible = has
        res_count_text.value = f"{len(app._cp_res_names)} resource(s)"
        # enable/disable calc_btn in-place based on current state
        cb = _cp_calc_btn_cell[0]
        if cb is not None:
            should_enable = bool(app._cp_rows and app._cp_res_names)
            try:
                cb.opacity = 1.0 if should_enable else 0.45
                cb.on_click = _calculate if should_enable else None
                cb.update()
            except Exception:
                pass
        try:
            name_chips_wrap.update()
            res_count_text.update()
            name_field.update()
        except Exception:
            pass

    def _add_name(e):
        for piece in re.split(r"[,\n]+", name_field.value or ""):
            nm = piece.strip()
            if nm and nm not in app._cp_res_names:
                app._cp_res_names.append(nm)
        name_field.value = ""
        app._cp_calculated = False
        app._cp_msg = None
        _refresh_chips_inplace()

    def _remove_name(nm):
        app._cp_res_names = [n for n in app._cp_res_names if n != nm]
        app._cp_calculated = False
        _refresh_chips_inplace()

    def _on_count(e):
        v = (count_field.value or "").strip()
        app._cp_res_count = int(v) if v.isdigit() and int(v) > 0 else None
        # in-place: just update the count field border color; no layout change needed
        new_mismatch = bool(app._cp_res_count is not None and app._cp_res_names
                            and app._cp_res_count != len(app._cp_res_names))
        count_field.border_color = T.RED if new_mismatch else T.BORDER
        try:
            count_field.update()
        except Exception:
            pass

    def _on_min(e):
        try:
            app._cp_est_min = float(e.control.value or 1)
        except Exception:
            pass

    def _on_max(e):
        try:
            app._cp_est_max = float(e.control.value or 8)
        except Exception:
            pass

    count_field = ft.TextField(
        value=("" if count is None else str(count)), hint_text="e.g. 3",
        keyboard_type=ft.KeyboardType.NUMBER, on_blur=_on_count, on_submit=_on_count,
        width=92, text_size=13,
        border_color=(T.RED if mismatch else T.BORDER), focused_border_color=T.VIOLET,
        border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=10))
    name_field = ft.TextField(
        hint_text="Type a tester's name, press Enter (or paste comma-separated)",
        on_submit=_add_name, on_blur=_add_name, expand=True, text_size=13,
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=10))

    def _res_chip(nm):
        init, col = _av(nm)
        return ft.Container(
            ft.Row([
                ft.Container(ft.Text(init, size=10, weight=ft.FontWeight.BOLD,
                                     color="#FFFFFF"),
                             width=20, height=20, bgcolor=col, border_radius=20,
                             alignment=ft.Alignment.CENTER),
                ft.Text(nm, size=12.5, weight=ft.FontWeight.BOLD, color=T.INK),
                ft.GestureDetector(
                    content=ft.Icon(ft.Icons.CLOSE, size=12, color=T.INK_3),
                    on_tap=(lambda e, x=nm: _remove_name(x)),
                    mouse_cursor=ft.MouseCursor.CLICK)],
               spacing=7, tight=True),
            padding=ft.Padding.only(left=5, right=9, top=4, bottom=4),
            bgcolor=T.CARD_2, border_radius=999, border=ft.Border.all(1, T.BORDER_2))

    name_chips = ft.Row([_res_chip(n) for n in app._cp_res_names],
                        wrap=True, spacing=8, run_spacing=8)
    name_chips_wrap = ft.Container(name_chips, padding=ft.Padding.only(top=10),
                                   visible=bool(app._cp_res_names))
    res_count_text = ft.Text(f"{len(app._cp_res_names)} resource(s)", size=11, color=T.INK_3,
                             weight=ft.FontWeight.BOLD, visible=bool(app._cp_res_names))

    warn = ft.Container()
    if mismatch:
        more = "more names than the number" if len(names) > count else \
               "fewer names than the number"
        warn = ft.Container(
            ft.Row([ft.Icon(ft.Icons.WARNING_AMBER, size=15, color=T.AMBER),
                    ft.Text(f"You set {count} resource(s) but added {len(names)} "
                            f"name(s) — {more}.", size=12, color=T.AMBER,
                            weight=ft.FontWeight.W_500, expand=True)], spacing=8),
            padding=10, bgcolor=T.AMBER_SOFT, border_radius=T.R,
            border=ft.Border.all(1, "#EAD9A8"), margin=ft.Margin.only(top=10))

    def _num(v, on_change):
        return ft.TextField(value=str(v), on_change=on_change, width=92, text_size=13,
                            border_color=T.BORDER, focused_border_color=T.VIOLET,
                            border_radius=T.R, keyboard_type=ft.KeyboardType.NUMBER,
                            content_padding=ft.Padding.symmetric(vertical=12, horizontal=10))

    card2 = card(ft.Column([
        sec_head("2", "Resources & estimate"),
        ft.Container(height=10),
        ft.Row([
            ft.Column([field_label("Count"), count_field], spacing=6, tight=True),
            ft.Column([field_label("Add a name"),
                       ft.Row([name_field,
                               green_btn("Add", icon=ft.Icons.ADD, on_click=_add_name)],
                              spacing=8)], spacing=6, expand=True),
        ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.START),
        name_chips_wrap,
        res_count_text,
        warn,
        ft.Container(height=14),
        ft.Row([
            ft.Column([field_label("Min h / story"), _num(app._cp_est_min, _on_min)],
                      spacing=6),
            ft.Column([field_label("Max h / story"), _num(app._cp_est_max, _on_max)],
                      spacing=6),
            ft.Container(
                _txt("Each story gets a random estimate in this range (stable per "
                     "story). Hours & assignees stay editable in the plan below.",
                     color=T.INK_3, size=11.5), expand=True,
                padding=ft.Padding.only(left=6, top=18)),
        ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.START),
    ], spacing=0))

    # ── Assign & Estimate button ──
    # Mutable refs so _calculate can update these in-place.
    cp_calc_note_text = ft.Text("", size=12, color=T.AMBER, weight=ft.FontWeight.W_500,
                                expand=True)
    cp_calc_note_wrap = ft.Container(
        ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=15, color=T.AMBER), cp_calc_note_text],
               spacing=8),
        padding=10, bgcolor=T.AMBER_SOFT, border_radius=T.R,
        border=ft.Border.all(1, "#EAD9A8"), margin=ft.Margin.only(top=10),
        visible=bool(app._cp_calc_msg))
    if app._cp_calc_msg:
        cp_calc_note_text.value = app._cp_calc_msg

    def _calculate(e):
        if not app._cp_rows:
            app._cp_calc_msg = "Pick a sprint with stories first."
            cp_calc_note_text.value = app._cp_calc_msg
            cp_calc_note_wrap.visible = True
            try: cp_calc_note_wrap.update()
            except Exception: app.render()
            return
        if not app._cp_res_names:
            app._cp_calc_msg = "Add at least one resource name first."
            cp_calc_note_text.value = app._cp_calc_msg
            cp_calc_note_wrap.visible = True
            try: cp_calc_note_wrap.update()
            except Exception: app.render()
            return
        app._cp_calc_msg = None
        cp_calc_note_wrap.visible = False
        try: cp_calc_note_wrap.update()
        except Exception: pass
        _cp_estimate_and_assign(app)
        app._cp_calculated = True
        app.render()   # result table must appear — full render unavoidable here

    calc_btn = primary_btn("Generate Sprint Plan", icon=ft.Icons.CALCULATE,
                           on_click=_calculate,
                           disabled=not (app._cp_rows and app._cp_res_names))
    _cp_calc_btn_cell[0] = calc_btn   # wire so _refresh_chips_inplace can enable it

    # ── results / plan (after Assign & Estimate) ──
    results = None
    if app._cp_calculated and app._cp_rows and app._cp_res_names:

        # --- live, in-place builders (no full re-render → scroll is preserved) ---
        def _kpis():
            d2 = plan_payload(app)
            return [
                _kpi_tile("STORIES", str(d2["total_stories"])),
                _kpi_tile("TOTAL EFFORT", f"{d2['total_hours']} h"),
                _kpi_tile("PER PERSON", f"{d2['hours_per_person']} h", T.GREEN),
            ]

        def _workload():
            d2 = plan_payload(app)
            if not d2["workload"]:
                return ft.Container()
            maxw = max((w["hours"] for w in d2["workload"]), default=0) or 1
            cards_wl = [ft.Container(ft.Column([
                ft.Row([_avatar(w["name"], 32),
                        ft.Column([_txt(w["name"], color=T.INK, weight=ft.FontWeight.BOLD, size=14),
                                   _txt(f"{w['stories']} stories", color=T.INK_3, size=11)],
                                  spacing=1, tight=True, expand=True),
                        _txt(f"{w['hours']} h", color=T.INK, weight=ft.FontWeight.BOLD,
                             size=16, no_wrap=True)],
                       spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=12), _bar(w["hours"] / maxw, T.VIOLET, 8),
            ], spacing=0), width=300, padding=14, bgcolor=T.CARD,
                border=ft.Border.all(1, T.BORDER_2), border_radius=T.R)
                for w in d2["workload"]]
            return ft.Column([
                ft.Container(height=16),
                ft.Text("RESOURCE WORKLOAD", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
                ft.Container(height=10),
                ft.Row(cards_wl, spacing=14, wrap=True, run_spacing=14,
                       vertical_alignment=ft.CrossAxisAlignment.START)], spacing=0)

        def _refresh_totals():
            kpi_strip.controls = _kpis()
            workload_holder.content = _workload()
            kpi_strip.update(); workload_holder.update()

        def _refresh_all():
            plan_col.controls = [hdr] + _trows()
            plan_col.update(); _refresh_totals()

        def _edit_hours(sid):
            def _h(e):
                try:
                    v = float(e.control.value or 0)
                except Exception:
                    return
                for r in app._cp_rows:
                    if r["id"] == sid:
                        r["hours"] = round(v, 2); break
                _refresh_totals()          # update KPIs + workload, keep field focused
            return _h

        def _edit_assignee(sid):
            def _a(e):
                for r in app._cp_rows:
                    if r["id"] == sid:
                        r["assignee"] = e.control.value or ""; break
                _refresh_totals()
            return _a

        def _delete_story(sid):
            def _d(e):
                app._cp_rows = [r for r in app._cp_rows if r["id"] != sid]
                _refresh_all()             # rebuild table + recalc, no scroll jump
            return _d

        hdr = ft.Container(
            ft.Row([ft.Container(width=34),
                    _txt("STORY", color=T.INK_2, size=10.5, weight=ft.FontWeight.BOLD, width=84),
                    _txt("TITLE", color=T.INK_2, size=10.5, weight=ft.FontWeight.BOLD, expand=True),
                    _txt("HOURS", color=T.INK_2, size=10.5, weight=ft.FontWeight.BOLD, width=110),
                    _txt("ASSIGNEE", color=T.INK_2, size=10.5, weight=ft.FontWeight.BOLD, width=180)],
                   spacing=10),
            padding=ft.Padding.symmetric(vertical=11, horizontal=12),
            bgcolor=T.CARD_2,
            border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER)))

        def _trows():
            out = []
            for i, r in enumerate(app._cp_rows):
                hours_f = ft.TextField(
                    value=str(r["hours"]), on_change=_edit_hours(r["id"]),
                    width=92, text_size=13, border_color=T.BORDER,
                    focused_border_color=T.VIOLET, border_radius=T.R,
                    keyboard_type=ft.KeyboardType.NUMBER,
                    content_padding=ft.Padding.symmetric(vertical=8, horizontal=8))
                assignee_dd = ft.Dropdown(
                    value=r["assignee"] or None, width=168, text_size=13,
                    options=[ft.DropdownOption(key=n, text=n) for n in app._cp_res_names],
                    on_select=_edit_assignee(r["id"]), border_color=T.BORDER,
                    border_radius=T.R, content_padding=ft.Padding.symmetric(vertical=6, horizontal=8))
                del_btn = ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE, icon_size=18, icon_color=T.RED,
                    tooltip="Remove story & recalculate",
                    on_click=_delete_story(r["id"]),
                    width=34, height=34,
                    style=ft.ButtonStyle(padding=ft.Padding.all(0),
                                         shape=ft.RoundedRectangleBorder(radius=8)))
                out.append(ft.Container(
                    ft.Row([ft.Container(del_btn, width=34),
                            _txt(str(r["id"]), color=T.VIOLET_INK, weight=ft.FontWeight.BOLD,
                                 width=84, font_family=T.F_MONO),
                            _txt(r["title"] or "—", color=T.INK, expand=True),
                            ft.Container(hours_f, width=110),
                            ft.Container(assignee_dd, width=180)],
                           spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.Padding.symmetric(vertical=6, horizontal=12),
                    bgcolor=("#FFFFFF" if i % 2 == 0 else T.CARD_2),
                    border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER_2))))
            return out

        kpi_strip = ft.Row(_kpis(), spacing=14)
        plan_col = ft.Column([hdr] + _trows(), spacing=0)
        table = ft.Container(plan_col, border=ft.Border.all(1, T.BORDER),
                             border_radius=T.R, clip_behavior=ft.ClipBehavior.HARD_EDGE)
        workload_holder = ft.Container(content=_workload())
        workload_ui = workload_holder

        _cp_status_cell = [None]

        def _set_cp_status(kind, text):
            app._cp_msg = (kind, text)
            sc = _cp_status_cell[0]
            if sc is None:
                app.ui_safe(app.render); return
            ok = (kind == "ok")
            sc.bgcolor = T.CARD
            sc.visible = True
            try:
                row = sc.content
                row.controls[0].name = (ft.Icons.CHECK_CIRCLE if ok
                                        else ft.Icons.ERROR_OUTLINE)
                row.controls[0].color = T.GREEN if ok else T.RED
                row.controls[1].value = text
                row.controls[1].color = T.GREEN if ok else T.RED
                sc.update()
            except Exception:
                app.ui_safe(app.render)

        exports_col, _cp_export_status = _export_row(app, _set_cp_status)
        _cp_status_cell[0] = _cp_export_status

        def _email(e):
            to = [a.strip() for a in re.split(r"[,\s;]+", (email_field.value or ""))
                  if a.strip()]
            if not to:
                app._err("Enter at least one recipient email.")
                return
            if not getattr(E, "GMAIL_APP_PASS", ""):
                app._err("Set the Gmail App Password on the Setup screen first.")
                return
            app._cp_email_to = ", ".join(to)
            app._cp_emailing = True
            app._cp_msg = None
            app._toast("Sending the sprint plan…")

            def work():
                try:
                    d = plan_payload(app)
                    try:
                        attach = [export_docx(app)]
                    except Exception:
                        attach = []
                    subj = f"Sprint Plan — {d['plan_name'] or d['project']}"
                    ok, err = E.send_report(to, subj, _plan_html(d), attachments=attach)
                    kind = "ok" if ok else "err"
                    text = f"Emailed to {', '.join(to)}" if ok else (err or "Email failed.")
                except Exception as ex:
                    kind, text = "err", f"Email failed: {str(ex)[:160]}"
                app._cp_emailing = False
                app.ui_safe(lambda k=kind, t=text: (app._toast(t) if k == "ok"
                                                    else app._err(t)))
            threading.Thread(target=work, daemon=True).start()

        email_field = ft.TextField(
            value=app._cp_email_to, hint_text="name@company.com, another@company.com",
            on_change=lambda e: setattr(app, "_cp_email_to", e.control.value or ""),
            expand=True, text_size=13, border_color=T.BORDER,
            focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=10))
        email_row = ft.Column([
            ft.Text("EMAIL", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=8),
            ft.Row([email_field,
                    green_btn("Sending…" if app._cp_emailing else "Email plan",
                              icon=ft.Icons.SEND,
                              on_click=(None if app._cp_emailing else _email))],
                   spacing=8),
        ], spacing=0)

        def _assign_testers(e):
            rows = [{"id": r["id"], "name": r.get("assignee", "")}
                    for r in app._cp_rows if r.get("assignee")]
            if not rows:
                _set_cp_status("err", "Assign resources to stories first.")
                return
            app._cp_assigning = True
            app._cp_msg = None

            def work():
                try:
                    res = E.assign_testers(app.project, rows)
                except Exception as ex:
                    res = {"ok": 0, "errors": [str(ex)[:160]]}
                errs = res.get("errors", [])
                n = res.get("ok", 0)
                if n and not errs:
                    kind, text = "ok", (f"Assigned {n} stories to the "
                                        f"Assigned To Tester field in Azure.")
                elif n:
                    kind, text = "err", (f"Assigned {n}; {len(errs)} failed — "
                                         + "  ·  ".join(errs[:4])
                                         + ("  …" if len(errs) > 4 else ""))
                else:
                    kind, text = "err", ("  ·  ".join(errs[:5]) or "Nothing assigned.")
                app._cp_assigning = False
                app.ui_safe(lambda k=kind, t=text: _set_cp_status(k, t))
            threading.Thread(target=work, daemon=True).start()

        assign_note = ft.Container(
            ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=15, color=T.INK_3),
                    _txt("Writes each story's assignee into the Azure “Assigned To "
                         "Tester” field. Names are matched to that field's list — "
                         "you'll get a readable error for any that don't match.",
                         color=T.INK_3, size=11.5, expand=True)], spacing=8),
            padding=10, bgcolor=T.CARD, border_radius=T.R,
            border=ft.Border.all(1, T.BORDER_2), margin=ft.Margin.only(top=12))

        results = card(ft.Column([
            sec_head("3", "Plan"), ft.Container(height=12), kpi_strip,
            ft.Container(height=14), table, workload_ui,
            ft.Divider(height=22, color=T.BORDER),
            ft.Text("EXPORT", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=8), exports_col,
            ft.Divider(height=22, color=T.BORDER),
            email_row,
            ft.Container(height=14),
            ft.Row([green_btn("Assigning…" if app._cp_assigning
                              else "Assign to tester in Azure",
                              icon=ft.Icons.PERSON_ADD,
                              on_click=(None if app._cp_assigning else _assign_testers))]),
            assign_note,
        ], spacing=0))

    body_children = [card1, ft.Container(height=14), card2,
                     ft.Container(height=16), calc_btn, cp_calc_note_wrap]
    if results is not None:
        body_children += [ft.Container(height=16), results]
    body = ft.Column(body_children, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    return app.shell("Sprint Plan",
                     "Plan & estimate test effort across a sprint’s stories", body,
                     badge="STEP T")


def _flush_toasts(app):
    """Surface any newly-set status message (export / email / assign / calc) as a
    floating toast, in addition to the inline banner. Guards by remembered text so
    it fires once per new message, not on every render."""
    seen = getattr(app, "_toast_seen", None)
    if seen is None:
        seen = app._toast_seen = {}
    for slot in ("_reg_export_msg", "_reg_calc_msg", "_cp_msg", "_cp_calc_msg"):
        val = getattr(app, slot, None)
        if isinstance(val, (tuple, list)) and len(val) == 2:
            kind, text = val[0], val[1]
        elif isinstance(val, str) and val:
            kind, text = "err", val
        else:
            seen[slot] = None
            continue
        if not text or seen.get(slot) == text:
            continue
        seen[slot] = text
        try:
            if kind == "ok":
                app.ui_safe(lambda t=text: app._toast(t))
            else:
                app.ui_safe(lambda t=text: app._err(t))
        except Exception:
            pass


def screen(app):
    _init(app)
    _flush_toasts(app)
    from main import (card, sec_head, field_label, green_btn, ghost_btn,
                      primary_btn, searchable_dropdown)

    # ── gate: only needs a connection + a project ──
    if not (app.connected and app.project):
        return locked_state(
            app, "Regression Plan",
            "Build a regression plan from your test plans & their stories",
            "Connect your Azure DevOps account on the Setup screen. You can pick "
            "the test plans right here once connected.")

    app._reg_mode = "existing"

    # lazy-load test plans
    if not app._plans and not app._reg_plans_loading:
        app._reg_plans_loading = True

        def _lp():
            try:
                app._load_plans()
            except Exception:
                pass
            app._reg_plans_loading = False
            app.ui_safe(app.render)
        threading.Thread(target=_lp, daemon=True).start()

    selected_ids = [s["id"] for s in app._reg_selected]
    selected_plan_ids = [p["id"] for p in app._reg_plans_selected]

    # ── handlers ──
    def _add_plan(e):
        v = plan_dd.value
        if not v or not str(v).strip().isdigit():
            return
        pid = int(v)
        if pid not in selected_plan_ids:
            name = next((p["name"] for p in (app._plans or []) if p["id"] == pid), str(pid))
            app._reg_plans_selected.append({"id": pid, "name": name})
            # keep app.plan_id pointing at the first selected plan (Setup/back-compat)
            if not app.plan_id:
                app.plan_id = pid
                app.plan_name = name
            app._reg_selected_rows = []
            app._reg_export_msg = app._reg_calc_msg = None
            _reload_plan_stories(app)
        app.render()

    def _remove_plan(pid):
        app._reg_plans_selected = [p for p in app._reg_plans_selected if p["id"] != pid]
        # drop any selected stories that belonged to the removed plan
        app._reg_selected = [s for s in app._reg_selected if s.get("plan_id") != pid]
        # invalidate cached suite map for this plan
        try:
            app._reg_suite_cache.pop(pid, None)
        except Exception:
            pass
        if app.plan_id == pid:
            app.plan_id = (app._reg_plans_selected[0]["id"] if app._reg_plans_selected else None)
            app.plan_name = (app._reg_plans_selected[0]["name"] if app._reg_plans_selected else "")
        app._reg_selected_rows = []
        app._reg_export_msg = app._reg_calc_msg = None
        _reload_plan_stories(app)
        app.render()

    def _add_story(e):
        v = story_dd.value
        if not v or not str(v).strip().isdigit():
            return
        sid = int(v)
        if sid not in selected_ids:
            src = next((s for s in app._reg_plan_stories if s["id"] == sid), None)
            if src:
                app._reg_selected.append({"id": sid, "title": src.get("title", ""),
                                          "plan_id": src.get("plan_id")})
                app._reg_selected_rows = []
                app._reg_export_msg = app._reg_calc_msg = None
        app.render()

    def _remove_story(sid):
        app._reg_selected = [s for s in app._reg_selected if s["id"] != sid]
        app._reg_selected_rows = []
        app._reg_export_msg = app._reg_calc_msg = None
        app.render()

    def _delete_row(sid):
        # inline-delete from the calculated plan table + recalculate
        def _d(e):
            app._reg_selected_rows = [r for r in (app._reg_selected_rows or [])
                                      if r.get("id") != sid]
            app._reg_selected = [s for s in app._reg_selected if s["id"] != sid]
            app._reg_export_msg = app._reg_calc_msg = None
            app.render()
        return _d

    def _use_setup_selection(e):
        # ensure the Setup plan is among the selected plans so its cases count
        if app.plan_id and app.plan_id not in selected_plan_ids:
            app._reg_plans_selected.append(
                {"id": app.plan_id, "name": getattr(app, "plan_name", "") or str(app.plan_id)})
            _reload_plan_stories(app)
        for sid in (app.story_ids or []):
            if int(sid) not in [s["id"] for s in app._reg_selected]:
                app._reg_selected.append({"id": int(sid), "title": "", "plan_id": app.plan_id})
        app._reg_selected_rows = []
        app._reg_export_msg = app._reg_calc_msg = None
        app.render()

    def _add_name(e):
        raw = (name_field.value or "")
        # accept several names at once, separated by commas (or newlines)
        for piece in re.split(r"[,\n]+", raw):
            nm = piece.strip()
            if nm and nm not in app._reg_res_names:
                app._reg_res_names.append(nm)
        name_field.value = ""
        app._reg_export_msg = None
        try:   # in-place update keeps the scroll position (full render snaps to top)
            name_chips.controls = [_res_chip(n, (lambda e, x=n: _remove_name(x)))
                                   for n in app._reg_res_names]
            name_chips_wrap.visible = bool(app._reg_res_names)
            name_chips_wrap.update(); name_field.update()
        except Exception:
            app.render()

    def _remove_name(nm):
        app._reg_res_names = [n for n in app._reg_res_names if n != nm]
        app._reg_export_msg = None
        try:
            name_chips.controls = [_res_chip(n, (lambda e, x=n: _remove_name(x)))
                                   for n in app._reg_res_names]
            name_chips_wrap.visible = bool(app._reg_res_names)
            name_chips_wrap.update()
        except Exception:
            app.render()

    def _on_count(e):
        v = (count_field.value or "").strip()
        app._reg_res_count = int(v) if v.isdigit() and int(v) > 0 else None
        app._reg_export_msg = None
        new_mismatch = bool(app._reg_res_count is not None and app._reg_res_names
                            and app._reg_res_count != len(app._reg_res_names))
        count_field.border_color = T.RED if new_mismatch else T.BORDER
        try:
            count_field.update()
        except Exception:
            pass

    # Mutable refs so _calculate can update these widgets in-place.
    # They are populated after the layout code below — Python closures capture
    # by reference so mutations made later are visible here.
    calc_note_text = ft.Text("", size=12, color=T.AMBER, weight=ft.FontWeight.W_500,
                             expand=True)
    calc_note_wrap = ft.Container(
        ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=15, color=T.AMBER), calc_note_text],
               spacing=8),
        padding=10, bgcolor=T.AMBER_SOFT, border_radius=T.R,
        border=ft.Border.all(1, "#EAD9A8"), margin=ft.Margin.only(top=10),
        visible=bool(app._reg_calc_msg))
    if app._reg_calc_msg:
        calc_note_text.value = app._reg_calc_msg
    # calc_btn_ref is set after primary_btn() is called below; use a list cell
    # as a mutable reference so the closure can reach the button.
    _calc_btn_cell = [None]
    _gen_spinner_cell = [None]

    def _get_calc_btn():
        return _calc_btn_cell[0]

    def _calculate(e):
        if not app._reg_plans_selected:
            app._reg_calc_msg = "Add at least one test plan first so effort can be " \
                                "read from its existing test cases."
            calc_note_text.value = app._reg_calc_msg
            calc_note_wrap.visible = True
            try: calc_note_wrap.update()
            except Exception: app.render()
            return
        if not app._reg_selected:
            app._reg_calc_msg = "Add at least one story."
            calc_note_text.value = app._reg_calc_msg
            calc_note_wrap.visible = True
            try: calc_note_wrap.update()
            except Exception: app.render()
            return
        app._reg_busy = True
        app._reg_calc_msg = app._reg_export_msg = None
        calc_note_wrap.visible = False
        btn = _get_calc_btn()
        if btn is not None:
            # primary_btn returns a Container (_grad_button); .disabled doesn't work on it.
            # Update opacity + remove click handler to signal busy state visually.
            try:
                btn.opacity = 0.55
                btn.on_click = None
                # also update the label text inside the Row>Text
                inner = btn.content.controls if hasattr(btn, "content") else []
                for ctrl in (inner if inner else []):
                    if hasattr(ctrl, "controls"):
                        for c in ctrl.controls:
                            if hasattr(c, "value"):
                                c.value = "Generating…"
                                break
                btn.update()
            except Exception:
                pass
        try:
            calc_note_wrap.update()
        except Exception:
            pass
        sp = _gen_spinner_cell[0]
        if sp is not None:
            try:
                sp.visible = True
                sp.update()
            except Exception:
                pass

        def _work():
            try:
                rows = build_rows(app, app._reg_selected)
            except Exception as ex:
                rows = []
                app._reg_calc_msg = f"Couldn't read plan: {ex}"
            app._reg_selected_rows = rows
            app._reg_busy = False
            app.ui_safe(app.render)
        threading.Thread(target=_work, daemon=True).start()

    def _do_export_to(fmt, dest=None):
        try:
            path = EXPORTERS[fmt](app)
        except ImportError:
            app._reg_export_msg = ("err", f"{fmt.upper()} needs {_MISSING_DEP.get(fmt, fmt)}")
            return None
        except Exception as ex:
            app._reg_export_msg = ("err", f"Export failed: {ex}")
            return None
        if dest:
            if not dest.lower().endswith("." + fmt):
                dest += "." + fmt
            try:
                import shutil
                if os.path.abspath(dest) != os.path.abspath(path):
                    shutil.move(path, dest)
                path = dest
            except Exception as ex:
                app._reg_export_msg = ("err", f"Couldn't save there: {ex}")
                return None
        app._reg_export_msg = ("ok", f"Saved {fmt.upper()}: {path}")
        try:
            os.startfile(os.path.dirname(path))
        except Exception:
            pass
        return path

    # Mutable status ref for in-place export/email banner updates.
    _status_cell = [None]   # [0] = ft.Container ref once results card is built

    def _set_status(kind, text):
        app._reg_export_msg = (kind, text)
        sc = _status_cell[0]
        if sc is None:
            app.ui_safe(app.render); return
        ok = (kind == "ok")
        sc.bgcolor = T.GREEN_SOFT if ok else T.RED_SOFT
        sc.visible = True
        try:
            row = sc.content
            row.controls[0].name = (ft.Icons.CHECK_CIRCLE if ok
                                    else ft.Icons.ERROR_OUTLINE)
            row.controls[0].color = T.GREEN if ok else T.RED
            row.controls[1].value = text
            row.controls[1].color = T.GREEN if ok else T.RED
            sc.update()
        except Exception:
            app.ui_safe(app.render)

    def _export(fmt):
        def _do(e):
            if not app._reg_selected_rows:
                app._err("Calculate the plan first.")
                return

            def work():
                dest = _ask_save_path(fmt, _stamp(app) + "." + fmt)
                if dest is False:
                    _do_export_to(fmt)
                elif dest:
                    _do_export_to(fmt, dest)
                else:
                    return
                msg = app._reg_export_msg
                if msg:
                    app.ui_safe(lambda m=msg: (app._toast(m[1]) if m[0] == "ok"
                                               else app._err(m[1])))
            threading.Thread(target=work, daemon=True).start()
        return _do

    def _on_email_to(e):
        app._reg_email_to = (email_field.value or "").strip()

    def _email(e):
        if not app._reg_selected_rows:
            app._err("Calculate the plan first.")
            return
        to = [a.strip() for a in re.split(r"[,\s;]+", (email_field.value or ""))
              if a.strip()]
        if not to:
            app._err("Enter at least one recipient email.")
            return
        if not E.GMAIL_APP_PASS:
            app._err("Set the Gmail App Password on the Setup screen first.")
            return
        app._reg_email_to = ", ".join(to)
        app._reg_emailing = True
        app._reg_export_msg = None
        app._toast("Sending the regression plan…")

        def work():
            try:
                d = plan_payload(app)
                # The mail report shows only the sprint number, not the long
                # "<Project>_Sprint N" test-plan name.
                trimmed = ", ".join(
                    (_sprint_num(p.get("name") or p.get("sprint") or "")
                     or (p.get("name") or "").strip())
                    for p in (app._reg_plans_selected or [])).strip(", ")
                if trimmed:
                    d["plan_name"] = trimmed
                try:
                    attach = [export_docx(app)]
                except Exception:
                    attach = []
                subj = f"Regression Test Plan — {d['plan_name'] or d['project']}"
                ok, err = E.send_report(to, subj, _plan_html(d), attachments=attach)
                kind = "ok" if ok else "err"
                text = f"Emailed to {', '.join(to)}" if ok else (err or "Email failed.")
            except Exception as ex:
                kind, text = "err", f"Email failed: {ex}"
            app._reg_emailing = False
            app.ui_safe(lambda: (app._toast(text) if kind == "ok" else app._err(text)))
        threading.Thread(target=work, daemon=True).start()

    # ── validation ──
    names = app._reg_res_names
    count = app._reg_res_count
    mismatch = bool(count is not None and names and count != len(names))

    def _txt(s, **kw):
        kw.setdefault("size", 12)
        return ft.Text(s, **kw)

    # ── Card 1: source +