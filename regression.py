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


def _count_cases(app, selected):
    """selected = [{"id","title","plan_id"}]. Count existing test cases for each
    story in ITS OWN plan, so multiple selected plans work correctly."""
    counts = {int(s["id"]): 0 for s in selected}
    by_plan = {}
    for s in selected:
        by_plan.setdefault(s.get("plan_id"), []).append(int(s["id"]))
    for pid, sids in by_plan.items():
        if not pid:
            continue
        try:
            smap = E.discover_suites_for_stories(app.project, pid, set(sids),
                                                 create_missing=False)
        except Exception:
            smap = {}
        for sid in sids:
            suite_id = smap.get(sid)
            if not suite_id:
                continue
            try:
                counts[sid] = len(E.fetch_test_cases_for_suite(app.project, pid, suite_id))
            except Exception:
                counts[sid] = 0
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
    def _kpi(label, val, unit, fg, bg, bd):
        u = (f"<span style='font-size:12px;color:#9aa4b8;font-weight:600'> {unit}</span>"
             if unit else "")
        return (
            f"<td width='25%' valign='top' style='padding:0 5px'>"
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='background:{bg};border:1px solid {bd};border-radius:12px'><tr>"
            f"<td style='padding:13px 14px'>"
            f"<div style='font-size:10px;letter-spacing:.6px;text-transform:uppercase;"
            f"color:{fg};font-weight:700'>{label}</div>"
            f"<div style='font-family:Consolas,monospace;font-size:23px;font-weight:700;"
            f"color:{fg};margin-top:4px'>{val}{u}</div></td></tr></table></td>")

    kpis = (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
        + _kpi("Stories", d["total_stories"], "", "#181A24", "#F6F8FC", "#EBEFF7")
        + _kpi("Test cases", d["total_cases"], "", "#181A24", "#F6F8FC", "#EBEFF7")
        + _kpi("Total effort", d["total_hours"], "h", "#2940C2", "#E7ECFF", "#D6DEFF")
        + _kpi("Per person", d["hours_per_person"], "h", "#1F9D57", "#E5F6EC", "#D2EEDF")
        + "</tr></table>")

    # story rows
    srows = []
    for r in d["stories"]:
        bg, fg, lab = _email_pri(r["priority"])
        init, acol = _av(r.get("assignee", ""))
        who = (r.get("assignee") or "").strip()
        asg = (
            f"<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
            f"<td width='24' height='24' style='border-radius:50%;background:{acol};"
            f"color:#fff;text-align:center;font-size:10px;font-weight:700'>{init}</td>"
            f"<td style='padding-left:9px;font-size:13px;font-weight:600;color:#39435c;"
            f"white-space:nowrap'>{who or '—'}</td></tr></table>")
        srows.append(
            f"<tr style='border-top:1px solid #f0f3f9'>"
            f"<td style='padding:12px 14px;font-family:Consolas,monospace;font-size:13px;"
            f"font-weight:600;color:#3A57D6;white-space:nowrap'>{r['id']}</td>"
            f"<td style='padding:12px 8px;font-size:13.5px;font-weight:600;color:#1f2940'>"
            f"{(r['title'] or '—')}</td>"
            f"<td style='padding:12px 8px;text-align:center'>"
            f"<span style='font-family:Consolas,monospace;font-size:11px;font-weight:700;"
            f"padding:3px 8px;border-radius:6px;background:{bg};color:{fg}'>{lab}</span></td>"
            f"<td style='padding:12px 8px;text-align:right;font-family:Consolas,monospace;"
            f"font-size:13.5px;color:#46506a'>{r['cases']}</td>"
            f"<td style='padding:12px 8px;text-align:right;font-family:Consolas,monospace;"
            f"font-size:13.5px;font-weight:700;color:#1f2940'>{r['hours']}</td>"
            f"<td style='padding:12px 14px'>{asg}</td></tr>")
    story_tbl = (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        f"style='border:1px solid #EBEFF7;border-radius:12px'>"
        f"<tr style='background:#F6F8FC'>"
        + "".join(f"<td style='padding:10px {p};font-size:10.5px;letter-spacing:.5px;"
                  f"text-transform:uppercase;color:#98a1b5;font-weight:700;{a}'>{h}</td>"
                  for h, p, a in (("Story", "14px", ""), ("Title", "8px", ""),
                                  ("Pri", "8px", "text-align:center"),
                                  ("Cases", "8px", "text-align:right"),
                                  ("Hours", "8px", "text-align:right"),
                                  ("Assignee", "14px", "")))
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
                f"<td width='22' height='22' style='border-radius:6px;background:{acol};"
                f"color:#fff;text-align:center;font-size:10px;font-weight:700'>{init}</td>"
                f"<td style='padding-left:9px;font-size:13px;font-weight:700;color:#1f2940'>"
                f"{w['name']}</td></tr></table></td>"
                f"<td style='padding:7px 14px'>"
                f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
                f"style='background:#eef1f7;border-radius:99px'><tr>"
                f"<td height='8' style='background:{acol};border-radius:99px;width:{pct}%;"
                f"font-size:0;line-height:0'>&nbsp;</td>"
                f"<td style='font-size:0;line-height:0'>&nbsp;</td></tr></table></td>"
                f"<td width='118' align='right' style='padding:7px 0;white-space:nowrap'>"
                f"<span style='font-size:11.5px;color:#8a93a8'>{w['stories']} stories · {w.get('cases', 0)} cases</span>"
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
        f"letter-spacing:-.5px'>Regression Test Plan</div>"
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
        f"document.<br>Estimates use {d['avg_minutes_per_case']}&nbsp;min / test case "
        f"weighted by Azure DevOps priority.</div></td></tr>"
        f"</table></div>")



def _out_dir():
    d = os.path.join(os.path.expanduser("~"), "QA Studio", "Regression Plans")
    os.makedirs(d, exist_ok=True)
    return d


def _stamp(app):
    if getattr(app, "_reg_mode", "existing") == "create":
        base = getattr(app, "_cp_sprint_name", "") or getattr(app, "_cp_sprint_path", "") or "sprint"
    else:
        base = ((getattr(app, "plan_name", "") or "")
                or (", ".join(p["name"] for p in (app._reg_plans_selected or [])) or "plan"))
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_") or "plan"
    return f"RegressionPlan_{base}_{datetime.now():%Y%m%d-%H%M}"


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
    ws.title = "Regression Plan"
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
    h = doc.add_heading("Regression Test Plan", level=0)
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


def _export_row(app):
    """Export buttons for the sprint report. Each saves via the shared exporter
    (which now reads the mode-aware payload) into ~/QA Studio/Regression Plans."""
    from main import green_btn, ghost_btn

    def _go(fmt):
        def _do(e):
            if not app._cp_rows:
                app._cp_msg = ("err", "Pick a sprint first.")
                app.ui_safe(app.render); return

            def work():
                try:
                    path = EXPORTERS[fmt](app)
                    app._cp_msg = ("ok", f"Saved: {path}")
                except ModuleNotFoundError:
                    app._cp_msg = ("err", f"Missing dependency: {_MISSING_DEP.get(fmt, fmt)}")
                except Exception as ex:
                    app._cp_msg = ("err", f"Export failed: {str(ex)[:160]}")
                app.ui_safe(app.render)
            threading.Thread(target=work, daemon=True).start()
        return _do

    btns = ft.Row([
        ghost_btn("Word", icon=ft.Icons.DESCRIPTION, on_click=_go("docx")),
        ghost_btn("Excel", icon=ft.Icons.TABLE_CHART, on_click=_go("xlsx")),
        ghost_btn("PDF", icon=ft.Icons.PICTURE_AS_PDF, on_click=_go("pdf")),
        ghost_btn("JSON", icon=ft.Icons.DATA_OBJECT, on_click=_go("json")),
    ], spacing=8, wrap=True)

    status = ft.Container()
    if app._cp_msg:
        kind, text = app._cp_msg
        col = T.GREEN if kind == "ok" else T.RED
        status = ft.Container(
            ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE if kind == "ok"
                            else ft.Icons.ERROR_OUTLINE, size=15, color=col),
                    _txt(text, color=col, size=12, expand=True)], spacing=8),
            padding=10, border_radius=T.R, margin=ft.Margin.only(top=10),
            bgcolor=T.CARD, border=ft.Border.all(1, T.BORDER_2))
    return ft.Column([btns, status], spacing=0)


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
            ft.Container(ft.Icon(icon, size=32, color=T.VIOLET),
                         width=74, height=74, bgcolor=T.VIOLET_SOFT, border_radius=21,
                         alignment=ft.Alignment.CENTER),
            ft.Container(height=22),
            ft.ProgressBar(value=None, color=T.VIOLET, bgcolor=T.BORDER_2,
                           bar_height=6, border_radius=99, width=232),
            ft.Container(height=11),
            ft.Text("Scanning for a connection…", size=11, color=T.INK_3,
                    weight=ft.FontWeight.W_500, font_family=T.F_MONO),
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
        width=300, padding=ft.Padding.symmetric(vertical=28, horizontal=28),
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
            ft.Container(height=14), pill,
            ft.Container(height=12),
            ft.Text("A few things first", size=20, weight=ft.FontWeight.BOLD,
                    color=T.INK),
            ft.Container(height=8),
            ft.Container(ft.Text(msg, size=13.5, color=T.INK_2,
                                 text_align=ft.TextAlign.CENTER), width=470),
            ft.Container(height=20), path,
            ft.Container(height=22),
            primary_btn("Go to Setup", icon=ft.Icons.ARROW_FORWARD,
                        on_click=lambda e: app.goto("setup")),
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
           alignment=ft.MainAxisAlignment.CENTER, tight=True),
        alignment=ft.Alignment.CENTER, expand=True,
        padding=ft.Padding.symmetric(vertical=24, horizontal=20))
    return app.shell(title, sub, body)


# ═══════════════════════════════════════════════════════════════════════════════
#  SCREEN
# ═══════════════════════════════════════════════════════════════════════════════
def _init(app):
    for k, v in (("_reg_plans_selected", []), ("_reg_plan_stories", []),
                 ("_reg_stories_loading", False), ("_reg_selected", []),
                 ("_reg_selected_rows", []), ("_reg_res_names", []),
                 ("_reg_res_count", None), ("_reg_busy", False),
                 ("_reg_export_msg", None), ("_reg_calc_msg", None),
                 ("_reg_email_to", ""), ("_reg_emailing", False),
                 ("_reg_plans_loading", False),
                 ("_reg_mode", "existing"),
                 ("_cp_iterations", []), ("_cp_iter_loading", False),
                 ("_cp_sprint_path", ""), ("_cp_sprint_name", ""),
                 ("_cp_stories_loading", False),
                 ("_cp_rows", []), ("_cp_res_names", []),
                 ("_cp_est_min", 1.0), ("_cp_est_max", 8.0),
                 ("_cp_msg", None), ("_cp_assigning", False)):
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
        agg, seen = [], set()
        for p in plans:
            # the plan's own sprint (its iteration) — shown on the plan chip
            try:
                pj = E._azure_get(f"https://dev.azure.com/{E.AZURE_ORG}/{app.project}"
                                  f"/_apis/testplan/plans/{p['id']}?api-version=7.0")
                itr = pj.get("iteration") or ""
                p["sprint"] = _sprint_num(itr) or _sprint_num(p.get("name", "")) \
                    or itr.split("\\")[-1]
            except Exception:
                p.setdefault("sprint", "")
            try:
                stories = E.fetch_stories_in_plan(app.project, p["id"])
            except Exception:
                stories = []
            for s in stories:
                key = (p["id"], s["id"])
                if key in seen:
                    continue
                seen.add(key)
                # group under the PLAN's sprint (what the user picked), not the
                # story's own iteration — so only selected sprints appear
                agg.append({"id": s["id"], "title": s.get("title", ""),
                            "sprint": p.get("sprint", "") or _sprint_num(s.get("sprint", "")),
                            "plan_id": p["id"]})
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


def _cp_load_stories(app):
    path = app._cp_sprint_path
    if not path:
        app._cp_rows = []
        app.ui_safe(app.render)
        return
    app._cp_stories_loading = True
    app._cp_rows = []
    app.ui_safe(app.render)

    def _work():
        try:
            stories = E.fetch_stories_in_iteration(app.project, path) or []
        except Exception:
            stories = []
        app._cp_rows = [{"id": s["id"], "title": s.get("title", ""),
                         "hours": 0.0, "assignee": ""} for s in stories]
        _cp_estimate_and_assign(app)   # seed an initial random estimate + assignment
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
    rows = [{"id": r["id"], "title": r.get("title", ""), "state": "",
             "priority": "", "cases": 0, "boost": 1.0,
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
            "plan_name": app._cp_sprint_name or app._cp_sprint_path,
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
            app, "Test Plan",
            "Build a test-effort report from a sprint & its stories",
            "Connect your Azure DevOps account on the Setup screen, then pick a "
            "sprint here.")
    app._reg_mode = "create"
    return _create_screen(app)


def _create_screen(app):
    from main import (card, sec_head, field_label, green_btn, ghost_btn,
                      primary_btn, searchable_dropdown)
    _cp_load_iterations(app)

    # ── sprint picker ──
    def _pick_sprint(e):
        v = (sprint_dd.value or "").strip()
        it = next((x for x in app._cp_iterations if x["path"] == v), None)
        if not it:
            return
        app._cp_sprint_path = it["path"]
        app._cp_sprint_name = _sprint_num(it["name"]) or it["name"]
        _cp_load_stories(app)

    sprint_dd = searchable_dropdown(
        hint_text=("Loading sprints…" if app._cp_iter_loading
                   else "Search & pick a sprint"),
        options=[ft.DropdownOption(key=it["path"],
                                   text=(_sprint_num(it["name"]) or it["name"])
                                   + f"  ·  {it['path']}")
                 for it in app._cp_iterations],
        on_select=_pick_sprint, border_color=T.BORDER, focused_border_color=T.VIOLET,
        border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=8),
        text_size=13, filled=True, bgcolor=T.CARD, expand=True)

    picked = ft.Container()
    if app._cp_sprint_name:
        picked = ft.Container(
            ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE, size=15, color=T.GREEN),
                    _txt(f"{app._cp_sprint_name}", color=T.INK, weight=ft.FontWeight.BOLD),
                    _txt(f"· {len(app._cp_rows)} stories", color=T.INK_3)],
                   spacing=8), padding=ft.Padding.only(top=10))

    card1 = card(ft.Column([
        sec_head("1", "Sprint"),
        ft.Container(height=10),
        ft.Column([field_label("Sprint", req=True), sprint_dd], spacing=6),
        picked,
        (ft.Container(_txt("Loading stories…", color=T.INK_3, size=12),
                      padding=ft.Padding.only(top=10))
         if app._cp_stories_loading else ft.Container()),
    ], spacing=0))

    # ── resources + estimate range ──
    def _on_res(e):
        raw = e.control.value or ""
        app._cp_res_names = [n.strip() for n in re.split(r"[,\n]", raw) if n.strip()]

    def _on_min(e):
        try: app._cp_est_min = float(e.control.value or 1)
        except Exception: pass

    def _on_max(e):
        try: app._cp_est_max = float(e.control.value or 8)
        except Exception: pass

    res_field = ft.TextField(
        value=", ".join(app._cp_res_names), on_change=_on_res, on_blur=lambda e: app.ui_safe(app.render),
        hint_text="Ahmed, Nada, Wafaa", multiline=False, text_size=13,
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=10), expand=True)

    def _num(v, on_change):
        return ft.TextField(value=str(v), on_change=on_change, width=92, text_size=13,
                            border_color=T.BORDER, focused_border_color=T.VIOLET,
                            border_radius=T.R, keyboard_type=ft.KeyboardType.NUMBER,
                            content_padding=ft.Padding.symmetric(vertical=12, horizontal=10))

    def _regen(e):
        _cp_estimate_and_assign(app)
        app.ui_safe(app.render)

    card2 = card(ft.Column([
        sec_head("2", "Resources & estimate",
                 right=ghost_btn("Re-roll estimate", icon=ft.Icons.CASINO,
                                 on_click=_regen) if app._cp_rows else None),
        ft.Container(height=10),
        ft.Column([field_label("Resource names", req=True), res_field], spacing=6),
        ft.Container(height=12),
        ft.Row([
            ft.Column([field_label("Min h / story"), _num(app._cp_est_min, _on_min)], spacing=6),
            ft.Column([field_label("Max h / story"), _num(app._cp_est_max, _on_max)], spacing=6),
            ft.Container(
                _txt("Each story gets a random estimate in this range (stable per "
                     "story). Hours and assignees stay editable in the table below.",
                     color=T.INK_3, size=11.5), expand=True,
                padding=ft.Padding.only(left=6, top=18)),
        ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.START),
    ], spacing=0))

    # ── results (only once we have rows + resources) ──
    results = None
    if app._cp_rows and app._cp_res_names:
        d = plan_payload(app)

        def _edit_hours(sid):
            def _h(e):
                try:
                    v = float(e.control.value or 0)
                except Exception:
                    return
                for r in app._cp_rows:
                    if r["id"] == sid:
                        r["hours"] = round(v, 2)
                        break
                app.ui_safe(app.render)
            return _h

        def _edit_assignee(sid):
            def _a(e):
                for r in app._cp_rows:
                    if r["id"] == sid:
                        r["assignee"] = e.control.value or ""
                        break
                app.ui_safe(app.render)
            return _a

        hdr = ft.Container(
            ft.Row([_txt("STORY", color=T.INK_3, size=10.5, weight=ft.FontWeight.BOLD, width=90),
                    _txt("TITLE", color=T.INK_3, size=10.5, weight=ft.FontWeight.BOLD, expand=True),
                    _txt("HOURS", color=T.INK_3, size=10.5, weight=ft.FontWeight.BOLD, width=110),
                    _txt("ASSIGNEE", color=T.INK_3, size=10.5, weight=ft.FontWeight.BOLD, width=180)],
                   spacing=10),
            padding=ft.Padding.symmetric(vertical=10, horizontal=12),
            bgcolor=T.CARD_2 if hasattr(T, "CARD_2") else T.CARD, border_radius=T.R)

        trows = []
        for r in app._cp_rows:
            hours_f = ft.TextField(
                value=str(r["hours"]), on_change=_edit_hours(r["id"]),
                width=92, text_size=13, border_color=T.BORDER,
                focused_border_color=T.VIOLET, border_radius=T.R,
                keyboard_type=ft.KeyboardType.NUMBER,
                content_padding=ft.Padding.symmetric(vertical=8, horizontal=8))
            assignee_dd = ft.Dropdown(
                value=r["assignee"] or None, width=168, text_size=13,
                options=[ft.DropdownOption(key=n, text=n) for n in app._cp_res_names],
                on_change=_edit_assignee(r["id"]), border_color=T.BORDER,
                border_radius=T.R, content_padding=ft.Padding.symmetric(vertical=6, horizontal=8))
            trows.append(ft.Container(
                ft.Row([_txt(str(r["id"]), color=T.VIOLET, weight=ft.FontWeight.BOLD, width=90),
                        _txt(r["title"] or "—", color=T.INK, expand=True),
                        ft.Container(hours_f, width=110),
                        ft.Container(assignee_dd, width=180)],
                       spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(vertical=8, horizontal=12),
                border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER))))
        table = ft.Column([hdr] + trows, spacing=0)

        kpi_strip = ft.Row([
            _kpi_tile("STORIES", str(d["total_stories"])),
            _kpi_tile("TOTAL EFFORT", f"{d['total_hours']} h"),
            _kpi_tile("PER PERSON", f"{d['hours_per_person']} h", T.GREEN),
        ], spacing=14)

        # per-resource workload cards
        workload_ui = ft.Container()
        if d["workload"]:
            maxw = max((w["hours"] for w in d["workload"]), default=0) or 1
            cards_wl = [ft.Container(ft.Column([
                ft.Row([_avatar(w["name"], 32),
                        ft.Column([_txt(w["name"], color=T.INK, weight=ft.FontWeight.BOLD, size=14),
                                   _txt(f"{w['stories']} stories", color=T.INK_3, size=11)],
                                  spacing=1, tight=True),
                        ft.Container(expand=True),
                        _txt(f"{w['hours']} h", color=T.INK, weight=ft.FontWeight.BOLD,
                             size=16, font_family=T.F_MONO)],
                       spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=12), _bar(w["hours"] / maxw, T.VIOLET, 8),
            ], spacing=0), expand=True, padding=14, bgcolor=T.CARD,
                border=ft.Border.all(1, T.BORDER_2), border_radius=T.R)
                for w in d["workload"]]
            workload_ui = ft.Column([
                ft.Container(height=16),
                ft.Text("RESOURCE WORKLOAD", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
                ft.Container(height=10),
                ft.Row(cards_wl, spacing=14)], spacing=0)

        # exports reuse the shared exporter buttons via plan_payload
        exports = _export_row(app)

        def _assign_testers(e):
            rows = [{"id": r["id"], "name": r.get("assignee", "")}
                    for r in app._cp_rows if r.get("assignee")]
            if not rows:
                app._cp_msg = ("err", "Assign resources to stories first.")
                app.ui_safe(app.render); return
            app._cp_assigning = True
            app._cp_msg = None
            app.ui_safe(app.render)

            def work():
                try:
                    res = E.assign_testers(app.project, rows)
                except Exception as ex:
                    res = {"ok": 0, "errors": [str(ex)[:160]]}
                errs = res.get("errors", [])
                n = res.get("ok", 0)
                if n and not errs:
                    app._cp_msg = ("ok", f"Assigned {n} stories to the "
                                         f"Assigned To Tester field in Azure.")
                elif n:
                    app._cp_msg = ("err", f"Assigned {n}; {len(errs)} failed — "
                                   + "  ·  ".join(errs[:4])
                                   + ("  …" if len(errs) > 4 else ""))
                else:
                    app._cp_msg = ("err", "  ·  ".join(errs[:5]) or "Nothing assigned.")
                app._cp_assigning = False
                app.ui_safe(app.render)
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
            sec_head("3", "Report"), ft.Container(height=12), kpi_strip,
            ft.Container(height=14), table, workload_ui,
            ft.Divider(height=22, color=T.BORDER),
            ft.Text("EXPORT", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=8), exports,
            ft.Container(height=14),
            ft.Row([green_btn("Assigning…" if app._cp_assigning
                              else "Assign to tester in Azure",
                              icon=ft.Icons.PERSON_ADD, on_click=_assign_testers,
                              disabled=app._cp_assigning)]),
            assign_note,
        ], spacing=0))

    body_children = [card1, ft.Container(height=14), card2]
    if results is not None:
        body_children += [ft.Container(height=16), results]
    body = ft.Column(body_children, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    return app.shell("Test Plan",
                     "Build a test-effort report from a sprint & its stories", body)


def screen(app):
    _init(app)
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
        app.render()

    def _remove_name(nm):
        app._reg_res_names = [n for n in app._reg_res_names if n != nm]
        app._reg_export_msg = None
        app.render()

    def _on_count(e):
        v = (count_field.value or "").strip()
        app._reg_res_count = int(v) if v.isdigit() and int(v) > 0 else None
        app._reg_export_msg = None
        app.render()

    def _calculate(e):
        if not app._reg_plans_selected:
            app._reg_calc_msg = "Add at least one test plan first so effort can be " \
                                "read from its existing test cases."
            app.render()
            return
        if not app._reg_selected:
            app._reg_calc_msg = "Add at least one story."
            app.render()
            return
        app._reg_busy = True
        app._reg_calc_msg = app._reg_export_msg = None
        app.render()

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

    def _export(fmt):
        def _do(e):
            if not app._reg_selected_rows:
                app._reg_export_msg = ("err", "Calculate the plan first.")
                app.render(); return

            def work():
                dest = _ask_save_path(fmt, _stamp(app) + "." + fmt)
                if dest is False:        # no native dialog available → default folder
                    _do_export_to(fmt)
                elif dest:               # a path was chosen
                    _do_export_to(fmt, dest)
                else:                    # user cancelled
                    return
                app.ui_safe(app.render)
            threading.Thread(target=work, daemon=True).start()
        return _do

    def _on_email_to(e):
        app._reg_email_to = (email_field.value or "").strip()

    def _email(e):
        if not app._reg_selected_rows:
            app._reg_export_msg = ("err", "Calculate the plan first.")
            app.render(); return
        to = [a.strip() for a in re.split(r"[,\s;]+", (email_field.value or ""))
              if a.strip()]
        if not to:
            app._reg_export_msg = ("err", "Enter at least one recipient email.")
            app.render(); return
        if not E.GMAIL_APP_PASS:
            app._reg_export_msg = ("err",
                "Set the Gmail App Password on the Setup screen first.")
            app.render(); return
        app._reg_email_to = ", ".join(to)
        app._reg_emailing = True
        app._reg_export_msg = None
        app.render()

        def work():
            try:
                d = plan_payload(app)
                try:
                    attach = [export_docx(app)]   # attach the Word plan
                except Exception:
                    attach = []
                subj = f"Regression Test Plan — {d['plan_name'] or d['project']}"
                ok, err = E.send_report(to, subj, _plan_html(d), attachments=attach)
                app._reg_export_msg = (("ok", f"Emailed to {', '.join(to)}")
                                       if ok else ("err", err or "Email failed."))
            except Exception as ex:
                app._reg_export_msg = ("err", f"Email failed: {ex}")
            app._reg_emailing = False
            app.ui_safe(app.render)
        threading.Thread(target=work, daemon=True).start()

    # ── validation ──
    names = app._reg_res_names
    count = app._reg_res_count
    mismatch = bool(count is not None and names and count != len(names))

    def _txt(s, **kw):
        kw.setdefault("size", 12)
        return ft.Text(s, **kw)

    # ── Card 1: source + stories ──
    plan_dd = searchable_dropdown(
        hint_text=("Loading test plans…" if app._reg_plans_loading
                   else "Search & add a test plan"),
        options=[ft.DropdownOption(key=str(p["id"]), text=f"[{p['id']}] {p['name']}")
                 for p in (app._plans or []) if p["id"] not in selected_plan_ids],
        on_select=_add_plan, border_color=T.BORDER, focused_border_color=T.VIOLET,
        border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=8),
        text_size=13, filled=True, bgcolor=T.CARD, expand=True)

    _have_plans = bool(app._reg_plans_selected)
    story_dd = searchable_dropdown(
        hint_text=("Loading stories…" if app._reg_stories_loading
                   else ("Search & add a story (grouped by sprint)" if _have_plans
                         else "Add a test plan first")),
        options=[ft.DropdownOption(
                    key=str(s["id"]),
                    text=(f"[{s['sprint']}] " if s.get("sprint") else "")
                         + f"[{s['id']}] {(s['title'] or '')[:44]}")
                 for s in app._reg_plan_stories if s["id"] not in selected_ids],
        on_select=_add_story, border_color=T.BORDER, focused_border_color=T.VIOLET,
        border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=8),
        text_size=13, filled=True, bgcolor=T.CARD, expand=True,
        disabled=not app._reg_plan_stories)

    def _chip(label, on_close):
        return ft.Container(
            ft.Row([ft.Text(label, size=12, weight=ft.FontWeight.BOLD,
                            color=T.VIOLET_INK, font_family=T.F_MONO),
                    ft.GestureDetector(
                        content=ft.Icon(ft.Icons.CLOSE, size=12, color=T.VIOLET_INK),
                        on_tap=on_close, mouse_cursor=ft.MouseCursor.CLICK)],
                   spacing=5, tight=True),
            padding=ft.Padding.only(left=10, right=7, top=5, bottom=5),
            bgcolor=T.VIOLET_SOFT, border_radius=T.R_SM,
            border=ft.Border.all(1, "#D9D2FF"))

    def _plan_chip_label(p):
        # Show ONLY the sprint number; fall back to iteration tail or id so a
        # chip never renders blank.
        return (_sprint_num(p.get("sprint") or "") or _sprint_num(p.get("name") or "")
                or (p.get("sprint") or "").strip() or f"[{p['id']}]")

    plan_chips = ft.Row(
        [_chip(_plan_chip_label(p), (lambda e, x=p["id"]: _remove_plan(x)))
         for p in app._reg_plans_selected], wrap=True, spacing=6, run_spacing=6)

    story_chips = ft.Row(
        [_chip(str(s["id"]), (lambda e, x=s["id"]: _remove_story(x)))
         for s in app._reg_selected], wrap=True, spacing=6, run_spacing=6)

    card1 = card(ft.Column([
        sec_head("1", "Source & stories",
                 right=ghost_btn("Use Setup selection", icon=ft.Icons.DOWNLOAD,
                                 on_click=_use_setup_selection)),
        ft.Container(height=10),
        ft.Column([field_label("Test plans", req=True), plan_dd], spacing=6),
        ft.Container(plan_chips, padding=ft.Padding.only(top=10),
                     visible=bool(app._reg_plans_selected)),
        ft.Text(f"{len(app._reg_plans_selected)} plan(s) selected", size=11,
                color=T.INK_3, weight=ft.FontWeight.BOLD,
                visible=bool(app._reg_plans_selected)),
        ft.Container(height=12),
        ft.Column([field_label("Add story"), story_dd], spacing=6),
        ft.Container(story_chips, padding=ft.Padding.only(top=10),
                     visible=bool(app._reg_selected)),
        ft.Text(f"{len(app._reg_selected)} stories selected", size=11,
                color=T.INK_3, weight=ft.FontWeight.BOLD),
    ], spacing=0))

    # ── Card 2: resources ──
    count_field = ft.TextField(
        value=("" if count is None else str(count)), hint_text="e.g. 3",
        keyboard_type=ft.KeyboardType.NUMBER, on_blur=_on_count, on_submit=_on_count,
        width=92, text_size=13,
        border_color=(T.RED if mismatch else T.BORDER), focused_border_color=T.VIOLET,
        border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=10))
    name_field = ft.TextField(
        hint_text="Type a name, press Enter", on_submit=_add_name, expand=True,
        text_size=13, border_color=T.BORDER, focused_border_color=T.VIOLET,
        border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=10))
    def _res_chip(nm, on_close):
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
                    on_tap=on_close, mouse_cursor=ft.MouseCursor.CLICK)],
               spacing=7, tight=True),
            padding=ft.Padding.only(left=5, right=9, top=4, bottom=4),
            bgcolor=T.CARD_2, border_radius=999,
            border=ft.Border.all(1, T.BORDER_2))

    name_chips = ft.Row(
        [_res_chip(n, (lambda e, x=n: _remove_name(x))) for n in app._reg_res_names],
        wrap=True, spacing=8, run_spacing=8)

    warn = ft.Container()
    if mismatch:
        more = "more names than the number" if len(names) > count else \
               "fewer names than the number"
        warn = ft.Container(
            ft.Row([ft.Icon(ft.Icons.WARNING_AMBER, size=15, color=T.AMBER),
                    ft.Text(f"You set {count} resource(s) but added {len(names)} "
                            f"name(s) — {more}. Match them to export.",
                            size=12, color=T.AMBER, weight=ft.FontWeight.W_500,
                            expand=True)], spacing=8),
            padding=10, bgcolor=T.AMBER_SOFT, border_radius=T.R,
            border=ft.Border.all(1, "#EAD9A8"), margin=ft.Margin.only(top=10))

    card2 = card(ft.Column([
        sec_head("2", "Resources"),
        ft.Container(height=10),
        ft.Row([
            ft.Column([field_label("Count"), count_field],
                      spacing=6, tight=True),
            ft.Column([field_label("Add a name"),
                       ft.Row([name_field,
                               green_btn("Add", icon=ft.Icons.ADD,
                                         on_click=_add_name)], spacing=8)],
                      spacing=6, expand=True),
        ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(name_chips, padding=ft.Padding.only(top=10),
                     visible=bool(app._reg_res_names)),
        warn,
    ], spacing=0))

    # ── Card 3: effort model ──
    card3 = card(ft.Column([
        sec_head("3", "How effort is estimated"),
        ft.Container(height=10),
        ft.Container(
            ft.Row([
                _pill("test cases", T.INK_2, T.CARD_2),
                ft.Text("×", size=14, color=T.INK_3, weight=ft.FontWeight.BOLD),
                _pill(f"{AVG_MINUTES_PER_CASE} min", T.INK_2, T.CARD_2),
                ft.Text("×", size=14, color=T.INK_3, weight=ft.FontWeight.BOLD),
                _pill("priority weight", T.VIOLET_INK, T.VIOLET_SOFT),
                ft.Text("=", size=14, color=T.INK_3, weight=ft.FontWeight.BOLD),
                _pill("estimated hours", T.GREEN, T.GREEN_SOFT),
            ], spacing=8, wrap=True), padding=ft.Padding.only(bottom=12)),
        ft.Text("Priority weight (from each story's Azure DevOps priority):",
                size=12, color=T.INK_2, weight=ft.FontWeight.W_500),
        ft.Container(height=8),
        ft.Row([_pill("P1 ×1.30", T.RED, T.RED_SOFT),
                _pill("P2 ×1.15", T.AMBER, T.AMBER_SOFT),
                _pill("P3 ×1.00", T.INK_2, T.CARD_2),
                _pill("P4 ×0.90", T.GREEN, T.GREEN_SOFT)], spacing=8, wrap=True),
        ft.Container(height=10),
        ft.Text(f"Example: a P1 story with 33 cases  →  33 × {AVG_MINUTES_PER_CASE} "
                f"min × 1.30 ≈ 5.7 h", size=11.5, color=T.INK_3,
                weight=ft.FontWeight.W_500),
    ], spacing=0))

    # ── results ──
    results = None
    if app._reg_selected_rows:
        d = plan_payload(app)

        def _cell(w, content, expand=False):
            return ft.Container(content, width=(None if expand else w), expand=expand,
                                padding=ft.Padding.symmetric(vertical=0, horizontal=6),
                                alignment=ft.Alignment.CENTER_LEFT)

        def _hd(s, w, expand=False):
            return _cell(w, _txt(s, size=10.5, weight=ft.FontWeight.BOLD,
                                 color=T.INK_3), expand=expand)

        header = ft.Container(
            ft.Row([_hd("STORY", 64), _hd("TITLE", 0, expand=True), _hd("STATE", 84),
                    _hd("PRI", 44), _hd("CASES", 52), _hd("HOURS", 128),
                    _hd("ASSIGNEE", 140)], spacing=4),
            padding=ft.Padding.symmetric(vertical=9, horizontal=8), bgcolor=T.CARD_2,
            border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER)))

        maxh_story = max((x["hours"] for x in d["stories"]), default=0) or 1
        body_rows = []
        for i, s in enumerate(d["stories"]):
            bg = "#FFFFFF" if i % 2 == 0 else ft.Colors.with_opacity(0.5, T.BG)
            asg = s.get("assignee")
            asg_ctl = (ft.Row([_avatar(asg, 24),
                               _txt(asg, color=T.INK, weight=ft.FontWeight.W_500)],
                              spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                       if asg else _txt("—", color=T.INK_3))
            hours_ctl = ft.Row([
                ft.Container(_bar(s["hours"] / maxh_story), width=70),
                _txt(str(s["hours"]), color=T.INK, weight=ft.FontWeight.BOLD),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            body_rows.append(ft.Container(
                ft.Row([
                    _cell(64, _txt(str(s["id"]), font_family=T.F_MONO,
                                   color=T.VIOLET_INK, weight=ft.FontWeight.BOLD)),
                    _cell(0, _txt(s["title"] or "—", color=T.INK, no_wrap=False),
                          expand=True),
                    _cell(84, _state_pill(s["state"])),
                    _cell(44, _pri_pill(s["priority"])),
                    _cell(52, _txt(str(s["cases"]), color=T.INK_2)),
                    _cell(128, hours_ctl),
                    _cell(140, asg_ctl),
                ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(vertical=9, horizontal=8), bgcolor=bg))

        table = ft.Container(ft.Column([header] + body_rows, spacing=0),
                             border=ft.Border.all(1, T.BORDER), border_radius=T.R,
                             clip_behavior=ft.ClipBehavior.HARD_EDGE)

        kpi_strip = ft.Row([
            _kpi_tile("STORIES", str(d["total_stories"])),
            _kpi_tile("TEST CASES", str(d["total_cases"])),
            _kpi_tile("TOTAL EFFORT", f"{d['total_hours']} h", T.VIOLET),
            _kpi_tile("PER PERSON", f"{d['hours_per_person']} h", T.GREEN),
        ], spacing=10)

        workload_ui = ft.Container()
        if d["workload"]:
            maxw = max((w["hours"] for w in d["workload"]), default=0) or 1
            cards_wl = [ft.Container(ft.Column([
                ft.Row([_avatar(w["name"], 32),
                        ft.Column([_txt(w["name"], color=T.INK, weight=ft.FontWeight.BOLD,
                                        size=14),
                                   _txt(f"{w['stories']} stories · {w.get('cases', 0)} cases",
                                        color=T.INK_3, size=11)],
                                  spacing=1, tight=True, expand=True),
                        _txt(f"{w['hours']} h", color=T.INK, weight=ft.FontWeight.BOLD,
                             size=16, font_family=T.F_MONO, no_wrap=True)],
                       spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=12),
                _bar(w["hours"] / maxw, T.VIOLET, 8),
            ], spacing=0), width=300, padding=14, bgcolor=T.CARD,
                border=ft.Border.all(1, T.BORDER_2), border_radius=T.R)
                for w in d["workload"]]
            workload_ui = ft.Column([
                ft.Container(height=16),
                ft.Text("RESOURCE WORKLOAD", size=10.5, weight=ft.FontWeight.BOLD,
                        color=T.INK_3),
                ft.Container(height=10),
                ft.Row(cards_wl, spacing=12, wrap=True, run_spacing=12,
                       vertical_alignment=ft.CrossAxisAlignment.START)], spacing=0)

        if mismatch:
            exports = ft.Container(
                ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE, size=15, color=T.INK_3),
                        ft.Text("Resolve the resource mismatch above to export.",
                                size=12, color=T.INK_3, weight=ft.FontWeight.W_500)],
                       spacing=8),
                padding=10, bgcolor=T.CARD_2, border_radius=T.R)
        else:
            exports = ft.Row([
                green_btn("Word", icon=ft.Icons.DESCRIPTION, on_click=_export("docx")),
                ghost_btn("Excel", icon=ft.Icons.GRID_ON, on_click=_export("xlsx")),
                ghost_btn("JSON", icon=ft.Icons.DATA_OBJECT, on_click=_export("json")),
                ghost_btn("PDF", icon=ft.Icons.PICTURE_AS_PDF, on_click=_export("pdf")),
            ], spacing=10, wrap=True)

        status = ft.Container()
        if app._reg_export_msg:
            kind, text = app._reg_export_msg
            ok = kind == "ok"
            status = ft.Container(
                ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR_OUTLINE,
                                size=16, color=(T.GREEN if ok else T.RED)),
                        ft.Text(text, size=12, color=(T.GREEN if ok else T.RED),
                                weight=ft.FontWeight.W_500, selectable=True, expand=True)],
                       spacing=8),
                padding=10, bgcolor=(T.GREEN_SOFT if ok else T.RED_SOFT),
                border_radius=T.R, margin=ft.Margin.only(top=10))

        email_field = ft.TextField(
            value=app._reg_email_to or "", on_change=_on_email_to,
            hint_text="recipient@company.com (comma-separate for several)",
            expand=True, text_size=13, border_color=T.BORDER,
            focused_border_color=T.VIOLET, border_radius=T.R,
            content_padding=ft.Padding.symmetric(vertical=12, horizontal=10))
        email_row = ft.Column([
            ft.Divider(height=20, color=T.BORDER),
            ft.Text("EMAIL THE PLAN", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=8),
            ft.Row([email_field,
                    green_btn("Sending…" if app._reg_emailing else "Send",
                              icon=ft.Icons.SEND, on_click=_email)],
                   spacing=10),
            ft.Text("Attaches the Word plan and an inline summary. Uses the Gmail "
                    "sender configured on Setup.", size=11, color=T.INK_3,
                    weight=ft.FontWeight.W_500),
        ], spacing=6)

        results = card(ft.Column([
            sec_head("4", "Plan"), ft.Container(height=12), kpi_strip,
            ft.Container(height=14), table,
            workload_ui, ft.Divider(height=22, color=T.BORDER),
            ft.Text("EXPORT", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=8), exports, email_row, status,
        ], spacing=0))

    calc_btn = primary_btn("Calculating…" if app._reg_busy else "Calculate plan",
                           icon=ft.Icons.CALCULATE, on_click=_calculate,
                           disabled=app._reg_busy or not app._reg_selected)
    calc_note = ft.Container()
    if app._reg_calc_msg:
        calc_note = ft.Container(
            ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=15, color=T.AMBER),
                    ft.Text(app._reg_calc_msg, size=12, color=T.AMBER,
                            weight=ft.FontWeight.W_500, expand=True)], spacing=8),
            padding=10, bgcolor=T.AMBER_SOFT, border_radius=T.R,
            border=ft.Border.all(1, "#EAD9A8"), margin=ft.Margin.only(top=10))

    body_children = [card1, ft.Container(height=14),
                     ft.Row([ft.Container(card2, expand=1),
                             ft.Container(card3, expand=1)],
                            spacing=14,
                            vertical_alignment=ft.CrossAxisAlignment.START),
                     ft.Container(height=16), calc_btn, calc_note]
    if results is not None:
        body_children += [ft.Container(height=16), results]

    body = ft.Column(body_children, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    return app.shell("Regression Plan",
                     "Build a regression plan from your test plans & their stories", body)
