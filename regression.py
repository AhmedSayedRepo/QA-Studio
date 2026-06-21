"""regression.py — Regression Plan tab for QA Studio.

Self-contained: builds a regression plan from an existing sprint + selected user
stories, estimates effort from the test cases that ALREADY exist in the plan
(reference-only — nothing is generated), balances the work across named
resources, and exports to Word / Excel / JSON / PDF.

Integration hooks live in main.py (import, T.NAV registration, rail clickable,
render routing). All shared UI helpers and the theme are reused from main.py via
a deferred import so there is no circular import at load time.
"""
import os, re, json, threading
from datetime import datetime

import flet as ft
import theme as T
import engine as E

# ── Effort model (HARDCODED — change here if your team's numbers differ) ───────
AVG_MINUTES_PER_CASE = 8          # manual execution time per existing test case
DEFAULT_PRIORITY     = 3          # used when a story has no ADO priority set
# ADO User-Story priority is 1 (highest) … 4 (lowest). Higher priority → more
# careful regression → small effort boost.
PRIORITY_BOOST = {1: 1.30, 2: 1.15, 3: 1.00, 4: 0.90}
_PRI_LABEL = {1: "P1", 2: "P2", 3: "P3", 4: "P4"}
_PRI_FULL  = {1: "P1 (highest)", 2: "P2", 3: "P3", 4: "P4 (lowest)"}


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


def _count_cases(app, ids):
    counts = {int(s): 0 for s in ids}
    try:
        smap = E.discover_suites_for_stories(app.project, app.plan_id,
                                             set(int(s) for s in ids),
                                             create_missing=False)
    except Exception:
        smap = {}
    for sid, suite_id in smap.items():
        try:
            counts[int(sid)] = len(E.fetch_test_cases_for_suite(
                app.project, app.plan_id, suite_id))
        except Exception:
            counts[int(sid)] = 0
    return counts


def build_rows(app, ids):
    meta = _fetch_meta(app, ids)
    counts = _count_cases(app, ids)
    rows = []
    for sid in ids:
        m = meta.get(int(sid), {})
        pri = m.get("priority", DEFAULT_PRIORITY)
        cases = counts.get(int(sid), 0)
        boost = PRIORITY_BOOST.get(pri, 1.0)
        hours = round(cases * (AVG_MINUTES_PER_CASE / 60.0) * boost, 2)
        rows.append({"id": int(sid), "title": m.get("title", ""),
                     "state": m.get("state", "Unknown"), "priority": pri,
                     "cases": cases, "boost": boost, "hours": hours,
                     "assignee": ""})
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
        for r in rows:
            if r.get("assignee") in cnt:
                cnt[r["assignee"]] += 1
        workload = [{"name": n, "stories": cnt[n], "hours": round(load.get(n, 0.0), 2)}
                    for n in names]
    sprint = (app._reg_sprint_path or "").split("\\")[-1]
    return {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "project": app.project, "plan_id": app.plan_id,
            "plan_name": getattr(app, "plan_name", "") or "",
            "sprint": sprint, "avg_minutes_per_case": AVG_MINUTES_PER_CASE,
            "priority_boost": PRIORITY_BOOST, "resources_count": count,
            "resource_names": names, "stories": rows, "workload": workload,
            "total_stories": len(rows), "total_cases": total_cases,
            "total_hours": total_hours, "hours_per_person": per_person}


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPORTERS
# ═══════════════════════════════════════════════════════════════════════════════
def _out_dir():
    d = os.path.join(os.path.expanduser("~"), "QA Studio", "Regression Plans")
    os.makedirs(d, exist_ok=True)
    return d


def _stamp(app):
    base = ((getattr(app, "plan_name", "") or "")
            or (app._reg_sprint_path or "plan").split("\\")[-1])
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_") or "plan"
    return f"RegressionPlan_{base}_{datetime.now():%Y%m%d-%H%M}"


def export_json(app):
    p = os.path.join(_out_dir(), _stamp(app) + ".json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(plan_payload(app), f, ensure_ascii=False, indent=2)
    return p


def export_xlsx(app):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    d = plan_payload(app)
    wb = Workbook()
    ws = wb.active
    ws.title = "Regression Plan"
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="6A4DFF")
    thin = Border(*[Side(style="thin", color="E3E0EC")] * 4)
    r = 1
    for k, v in (("Project", d["project"]), ("Test plan", d["plan_name"]),
                 ("Plan ID", d["plan_id"]), ("Sprint", d["sprint"]),
                 ("Generated", d["generated"]),
                 ("Avg min / case", d["avg_minutes_per_case"]),
                 ("Resources", d["resources_count"]),
                 ("Resource names", ", ".join(d["resource_names"]) or "—")):
        ws.cell(r, 1, k).font = Font(bold=True)
        ws.cell(r, 2, v)
        r += 1
    r += 1
    cols = ["Story ID", "Title", "State", "Priority", "Test cases",
            "Est. hours", "Assignee"]
    for c, name in enumerate(cols, 1):
        cell = ws.cell(r, c, name)
        cell.font = head
        cell.fill = fill
    r += 1
    for s in d["stories"]:
        vals = [s["id"], s["title"], s["state"],
                _PRI_FULL.get(s["priority"], s["priority"]), s["cases"],
                s["hours"], s.get("assignee") or "—"]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(r, c, v)
            cell.border = thin
        r += 1
    ws.cell(r, 4, "TOTAL").font = Font(bold=True)
    ws.cell(r, 5, d["total_cases"]).font = Font(bold=True)
    ws.cell(r, 6, d["total_hours"]).font = Font(bold=True)
    r += 2
    if d["workload"]:
        ws.cell(r, 1, "Resource workload").font = Font(bold=True, size=12)
        r += 1
        for c, name in enumerate(["Resource", "Stories", "Hours"], 1):
            cell = ws.cell(r, c, name)
            cell.font = head
            cell.fill = fill
        r += 1
        for w in d["workload"]:
            ws.cell(r, 1, w["name"])
            ws.cell(r, 2, w["stories"])
            ws.cell(r, 3, w["hours"])
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
    rn = sub.add_run(f"{d['plan_name'] or d['project']}  ·  {d['sprint'] or '—'}")
    rn.font.size = Pt(11)
    rn.font.color.rgb = RGBColor(0x6A, 0x4D, 0xFF)

    meta = doc.add_table(rows=0, cols=2)
    meta.style = "Light List Accent 1"
    for k, v in (("Project", d["project"]), ("Test plan", d["plan_name"]),
                 ("Plan ID", str(d["plan_id"])), ("Sprint", d["sprint"]),
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
        wt = doc.add_table(rows=1, cols=3)
        wt.style = "Medium Shading 1 Accent 1"
        for i, hd in enumerate(["Resource", "Stories", "Hours"]):
            wt.rows[0].cells[i].text = hd
        for w in d["workload"]:
            c = wt.add_row().cells
            c[0].text = w["name"]
            c[1].text = str(w["stories"])
            c[2].text = str(w["hours"])

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
             Paragraph(f"{d['plan_name'] or d['project']} &middot; "
                       f"{d['sprint'] or '—'}", styles["Normal"]),
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
        wd = [["Resource", "Stories", "Hours"]] + \
             [[w["name"], str(w["stories"]), str(w["hours"])] for w in d["workload"]]
        wt = Table(wd, colWidths=[60*mm, 30*mm, 30*mm])
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


# ═══════════════════════════════════════════════════════════════════════════════
#  SCREEN
# ═══════════════════════════════════════════════════════════════════════════════
def _init(app):
    for k, v in (("_reg_sprints", None), ("_reg_sprint_path", None),
                 ("_reg_sprint_stories", []), ("_reg_selected", []),
                 ("_reg_selected_rows", []), ("_reg_res_names", []),
                 ("_reg_res_count", None), ("_reg_busy", False),
                 ("_reg_export_msg", None)):
        if not hasattr(app, k):
            setattr(app, k, v)


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


def screen(app):
    _init(app)
    from main import card, sec_head, field_label, green_btn, ghost_btn, primary_btn

    # ── empty state: not connected / no plan ──
    if not (app.connected and app.project and app.plan_id):
        msg = ("Connect your Azure DevOps account on the Setup screen — the "
               "regression plan reads the test cases that already exist in your "
               "selected test plan.")
        empty = ft.Container(
            ft.Column([
                ft.Container(ft.Icon(ft.Icons.LINK_OFF, size=30, color=T.VIOLET),
                             width=72, height=72, bgcolor=T.VIOLET_SOFT,
                             border_radius=20, alignment=ft.Alignment.CENTER),
                ft.Container(height=18),
                ft.Text("Connect to get started", size=17,
                        weight=ft.FontWeight.BOLD, color=T.INK),
                ft.Container(height=6),
                ft.Container(ft.Text(msg, size=13, color=T.INK_2,
                                     text_align=ft.TextAlign.CENTER),
                             width=460),
                ft.Container(height=20),
                primary_btn("Go to Setup", icon=ft.Icons.ARROW_FORWARD,
                            on_click=lambda e: app.goto("setup")),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
               alignment=ft.MainAxisAlignment.CENTER),
            alignment=ft.Alignment.CENTER, expand=True,
            padding=ft.Padding.symmetric(vertical=60, horizontal=20))
        return app.shell("Regression Plan",
                         "Build a regression plan from existing sprints & stories",
                         empty)

    # lazy-load sprints once
    if app._reg_sprints is None and not app._reg_busy:
        app._reg_busy = True

        def _load():
            try:
                sp = E.fetch_iterations(app.project)
            except Exception:
                sp = []
            app._reg_sprints = sp
            app._reg_busy = False
            app.ui_safe(app.render)
        threading.Thread(target=_load, daemon=True).start()

    selected_ids = [s["id"] for s in app._reg_selected]

    # ── handlers ──
    def _on_sprint(e):
        app._reg_sprint_path = sprint_dd.value
        app._reg_sprint_stories = []
        app._reg_busy = True
        app.render()

        def _load():
            try:
                st = E.fetch_stories_in_iteration(app.project, app._reg_sprint_path)
            except Exception:
                st = []
            app._reg_sprint_stories = st
            app._reg_busy = False
            app.ui_safe(app.render)
        threading.Thread(target=_load, daemon=True).start()

    def _add_story(e):
        v = story_dd.value
        if not v or not str(v).strip().isdigit():
            return
        sid = int(v)
        if sid not in selected_ids:
            title = next((s["title"] for s in app._reg_sprint_stories
                          if s["id"] == sid), "")
            app._reg_selected.append({"id": sid, "title": title})
            app._reg_selected_rows = []
            app._reg_export_msg = None
        app.render()

    def _remove_story(sid):
        app._reg_selected = [s for s in app._reg_selected if s["id"] != sid]
        app._reg_selected_rows = []
        app._reg_export_msg = None
        app.render()

    def _use_setup_selection(e):
        for sid in (app.story_ids or []):
            if int(sid) not in [s["id"] for s in app._reg_selected]:
                app._reg_selected.append({"id": int(sid), "title": ""})
        app._reg_selected_rows = []
        app._reg_export_msg = None
        app.render()

    def _add_name(e):
        name = (name_field.value or "").strip()
        if name and name not in app._reg_res_names:
            app._reg_res_names.append(name)
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
        if not app._reg_selected:
            app._toast("Select at least one story first.")
            return
        app._reg_busy = True
        app._reg_export_msg = None
        app.render()

        def _work():
            ids = [s["id"] for s in app._reg_selected]
            try:
                rows = build_rows(app, ids)
            except Exception as ex:
                rows = []
                app.ui_safe(lambda: app._toast(f"Couldn't read plan: {ex}"))
            app._reg_selected_rows = rows
            app._reg_busy = False
            app.ui_safe(app.render)
        threading.Thread(target=_work, daemon=True).start()

    def _export(fmt):
        def _do(e):
            try:
                path = EXPORTERS[fmt](app)
            except ImportError:
                app._reg_export_msg = ("err",
                    f"{fmt.upper()} needs {_MISSING_DEP.get(fmt, fmt)}")
                app.render()
                return
            except Exception as ex:
                app._reg_export_msg = ("err", f"Export failed: {ex}")
                app.render()
                return
            app._reg_export_msg = ("ok", f"Saved {fmt.upper()}: {path}")
            try:
                os.startfile(os.path.dirname(path))   # Windows: open the folder
            except Exception:
                pass
            app.render()
        return _do

    # ── resource validation ──
    names = app._reg_res_names
    count = app._reg_res_count
    mismatch = bool(count is not None and names and count != len(names))

    # ── Card 1: stories ──
    sprint_opts = [ft.DropdownOption(key=s["path"], text=s["name"])
                   for s in (app._reg_sprints or [])]
    sprint_dd = ft.Dropdown(
        value=app._reg_sprint_path,
        hint_text=("Loading sprints…" if app._reg_sprints is None
                   else "Select sprint / iteration"),
        options=sprint_opts, on_select=_on_sprint,
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=8),
        text_size=13, filled=True, bgcolor=T.CARD, expand=True)

    story_opts = [ft.DropdownOption(key=str(s["id"]),
                                    text=f"[{s['id']}] {(s['title'] or '')[:48]}")
                  for s in app._reg_sprint_stories if s["id"] not in selected_ids]
    _sd_kwargs = dict(
        hint_text=("Loading…" if (app._reg_busy and app._reg_sprint_path)
                   else "Search & pick a story"),
        options=story_opts, on_select=_add_story,
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=8),
        text_size=13, filled=True, bgcolor=T.CARD, expand=True,
        disabled=not app._reg_sprint_stories)
    try:                                   # searchable on newer Flet; degrade gracefully
        story_dd = ft.Dropdown(editable=True, enable_filter=True,
                               menu_height=320, **_sd_kwargs)
    except TypeError:
        try:
            story_dd = ft.Dropdown(menu_height=320, **_sd_kwargs)
        except TypeError:
            story_dd = ft.Dropdown(**_sd_kwargs)

    def _chip(label, on_close, fg=None, bg=None):
        fg = fg or T.VIOLET_INK
        bg = bg or T.VIOLET_SOFT
        return ft.Container(
            ft.Row([ft.Text(label, size=12, weight=ft.FontWeight.BOLD, color=fg,
                            font_family=T.F_MONO),
                    ft.GestureDetector(
                        content=ft.Icon(ft.Icons.CLOSE, size=12, color=fg),
                        on_tap=on_close, mouse_cursor=ft.MouseCursor.CLICK)],
                   spacing=5, tight=True),
            padding=ft.Padding.only(left=10, right=7, top=5, bottom=5),
            bgcolor=bg, border_radius=T.R_SM, border=ft.Border.all(1, "#D9D2FF"))

    story_chips = ft.Row(
        [_chip(str(s["id"]), (lambda e, x=s["id"]: _remove_story(x)))
         for s in app._reg_selected], wrap=True, spacing=6, run_spacing=6)

    card1 = card(ft.Column([
        sec_head("1", "Select stories",
                 right=ghost_btn("Use Setup selection", icon=ft.Icons.DOWNLOAD,
                                 on_click=_use_setup_selection)),
        ft.Container(height=10),
        ft.Row([sprint_dd, story_dd], spacing=12,
               vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(story_chips, padding=ft.Padding.only(top=10),
                     visible=bool(app._reg_selected)),
        ft.Text(f"{len(app._reg_selected)} stories selected", size=11,
                color=T.INK_3, weight=ft.FontWeight.BOLD),
    ], spacing=0))

    # ── Card 2: resources ──
    count_field = ft.TextField(
        value=("" if count is None else str(count)), hint_text="e.g. 3",
        keyboard_type=ft.KeyboardType.NUMBER, on_blur=_on_count,
        on_submit=_on_count, width=120, text_size=13,
        border_color=(T.RED if mismatch else T.BORDER), focused_border_color=T.VIOLET,
        border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=10))
    name_field = ft.TextField(
        hint_text="Type a name, press Enter", on_submit=_add_name, expand=True,
        text_size=13, border_color=T.BORDER, focused_border_color=T.VIOLET,
        border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=10))
    name_chips = ft.Row(
        [_chip(n, (lambda e, x=n: _remove_name(x))) for n in app._reg_res_names],
        wrap=True, spacing=6, run_spacing=6)

    warn = None
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
            border=ft.Border.all(1, "#EAD9A8"),
            margin=ft.Margin.only(top=10))

    card2 = card(ft.Column([
        sec_head("2", "Resources"),
        ft.Container(height=10),
        ft.Row([
            ft.Column([field_label("Number of resources"), count_field],
                      spacing=6, tight=True),
            ft.Column([field_label("Resource names"),
                       ft.Row([name_field,
                               green_btn("Add", icon=ft.Icons.ADD,
                                         on_click=_add_name)], spacing=8)],
                      spacing=6, expand=True),
        ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(name_chips, padding=ft.Padding.only(top=10),
                     visible=bool(app._reg_res_names)),
        warn if warn else ft.Container(),
    ], spacing=0))

    # ── Card 3: effort model (readable) ──
    pri_pills = ft.Row([
        _pill("P1 ×1.30", T.RED, T.RED_SOFT),
        _pill("P2 ×1.15", T.AMBER, T.AMBER_SOFT),
        _pill("P3 ×1.00", T.INK_2, T.CARD_2),
        _pill("P4 ×0.90", T.GREEN, T.GREEN_SOFT),
    ], spacing=8, wrap=True)
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
            ], spacing=8, wrap=True),
            padding=ft.Padding.only(bottom=12)),
        ft.Text("Priority weight (from each story's Azure DevOps priority):",
                size=12, color=T.INK_2, weight=ft.FontWeight.W_500),
        ft.Container(height=8),
        pri_pills,
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
            return ft.Container(content, width=(None if expand else w),
                                expand=expand,
                                padding=ft.Padding.symmetric(vertical=0, horizontal=6),
                                alignment=ft.Alignment.CENTER_LEFT)

        def _txt(s, **kw):
            return ft.Text(s, size=12, **kw)

        header = ft.Container(
            ft.Row([
                _cell(64, _txt("STORY", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3)),
                _cell(0, _txt("TITLE", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3), expand=True),
                _cell(72, _txt("STATE", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3)),
                _cell(44, _txt("PRI", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3)),
                _cell(50, _txt("CASES", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3)),
                _cell(56, _txt("HOURS", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3)),
                _cell(110, _txt("ASSIGNEE", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3)),
            ], spacing=4),
            padding=ft.Padding.symmetric(vertical=9, horizontal=8),
            bgcolor=T.CARD_2,
            border=ft.Border.only(bottom=ft.BorderSide(1, T.BORDER)))

        body_rows = []
        for i, s in enumerate(d["stories"]):
            bg = "#FFFFFF" if i % 2 == 0 else ft.Colors.with_opacity(0.5, T.BG)
            asg = s.get("assignee")
            asg_ctl = (_pill(asg, T.VIOLET_INK, T.VIOLET_SOFT) if asg
                       else _txt("—", color=T.INK_3))
            body_rows.append(ft.Container(
                ft.Row([
                    _cell(64, _txt(str(s["id"]), font_family=T.F_MONO,
                                   color=T.VIOLET_INK, weight=ft.FontWeight.BOLD)),
                    _cell(0, _txt(s["title"] or "—", color=T.INK, no_wrap=False),
                          expand=True),
                    _cell(72, _state_pill(s["state"])),
                    _cell(44, _pri_pill(s["priority"])),
                    _cell(50, _txt(str(s["cases"]), color=T.INK_2)),
                    _cell(56, _txt(str(s["hours"]), color=T.INK, weight=ft.FontWeight.BOLD)),
                    _cell(110, asg_ctl),
                ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(vertical=9, horizontal=8), bgcolor=bg))

        table = ft.Container(
            ft.Column([header] + body_rows, spacing=0),
            border=ft.Border.all(1, T.BORDER), border_radius=T.R, clip_behavior=ft.ClipBehavior.HARD_EDGE)

        totals = ft.Row([
            ft.Container(_txt(f"{d['total_stories']} stories",
                              weight=ft.FontWeight.BOLD, color=T.INK), expand=True),
            _txt(f"{d['total_cases']} cases", color=T.INK_2, weight=ft.FontWeight.BOLD),
            ft.Container(width=14),
            _txt(f"{d['total_hours']} h total", color=T.INK, weight=ft.FontWeight.BOLD),
            ft.Container(width=14),
            ft.Container(_txt(f"≈ {d['hours_per_person']} h / person", color=T.GREEN,
                              weight=ft.FontWeight.BOLD),
                         padding=ft.Padding.symmetric(vertical=4, horizontal=10),
                         bgcolor=T.GREEN_SOFT, border_radius=T.R_SM),
        ], spacing=4)

        # per-resource workload
        workload_ui = ft.Container()
        if d["workload"]:
            maxh = max((w["hours"] for w in d["workload"]), default=0) or 1
            rows_wl = []
            for w in d["workload"]:
                rows_wl.append(ft.Row([
                    ft.Container(_pill(w["name"], T.VIOLET_INK, T.VIOLET_SOFT), width=140),
                    ft.Container(
                        ft.Container(width=max(6, int(160 * w["hours"] / maxh)),
                                     height=8, bgcolor=T.VIOLET, border_radius=4),
                        expand=True, alignment=ft.Alignment.CENTER_LEFT),
                    _txt(f"{w['stories']} stories", color=T.INK_3),
                    ft.Container(width=12),
                    _txt(f"{w['hours']} h", color=T.INK, weight=ft.FontWeight.BOLD),
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER))
            workload_ui = ft.Column([
                ft.Container(height=14),
                ft.Text("RESOURCE WORKLOAD", size=10.5, weight=ft.FontWeight.BOLD,
                        color=T.INK_3),
                ft.Container(height=8),
                ft.Column(rows_wl, spacing=8),
            ], spacing=0)

        # export bar + inline status (replaces invisible toast)
        export_disabled = mismatch
        exports = ft.Row([
            green_btn("Word", icon=ft.Icons.DESCRIPTION, on_click=_export("docx")),
            ghost_btn("Excel", icon=ft.Icons.GRID_ON, on_click=_export("xlsx")),
            ghost_btn("JSON", icon=ft.Icons.DATA_OBJECT, on_click=_export("json")),
            ghost_btn("PDF", icon=ft.Icons.PICTURE_AS_PDF, on_click=_export("pdf")),
        ], spacing=10, wrap=True) if not export_disabled else ft.Container(
            ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE, size=15, color=T.INK_3),
                    ft.Text("Resolve the resource mismatch above to export.",
                            size=12, color=T.INK_3, weight=ft.FontWeight.W_500)],
                   spacing=8),
            padding=10, bgcolor=T.CARD_2, border_radius=T.R)

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

        results = card(ft.Column([
            sec_head("4", "Plan"),
            ft.Container(height=10),
            table,
            ft.Container(totals, padding=ft.Padding.only(top=12, bottom=2)),
            workload_ui,
            ft.Divider(height=22, color=T.BORDER),
            ft.Text("EXPORT", size=10.5, weight=ft.FontWeight.BOLD, color=T.INK_3),
            ft.Container(height=8),
            exports,
            status,
        ], spacing=0))

    calc_btn = primary_btn("Calculating…" if app._reg_busy else "Calculate plan",
                           icon=ft.Icons.CALCULATE, on_click=_calculate,
                           disabled=app._reg_busy or not app._reg_selected)

    body_children = [card1, ft.Container(height=14), card2,
                     ft.Container(height=14), card3,
                     ft.Container(height=16), calc_btn]
    if results is not None:
        body_children += [ft.Container(height=16), results]

    body = ft.Column(body_children, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    return app.shell("Regression Plan",
                     "Build a regression plan from existing sprints & stories", body)
