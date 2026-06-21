"""regression.py — Regression Plan tab for QA Studio.

Self-contained: builds a regression plan from an existing sprint + selected user
stories, estimates effort from the test cases that ALREADY exist in the plan
(reference-only — nothing is generated), divides effort across named resources,
and exports to Word / Excel / JSON / PDF.

Integration (see the 4 hooks added to main.py):
    import regression                 # top of main.py
    T.NAV registration                # __init__
    rail() clickable                  # __init__/rail
    render():  elif active=="regression": view = regression.screen(self)

All UI helpers (card, sec_head, field_label, buttons) and the theme are reused
from main.py via a deferred import so there is no circular-import at load time.
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


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA GATHERING  (Azure DevOps — reference existing only)
# ═══════════════════════════════════════════════════════════════════════════════
def _fetch_meta(app, ids):
    """{story_id: {'title','state','priority'}} via a batched work-items GET."""
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
            meta[int(w["id"])] = {
                "title": f.get("System.Title", ""),
                "state": f.get("System.State", "Unknown"),
                "priority": pri,
            }
    return meta


def _count_cases(app, ids):
    """{story_id: existing_test_case_count} using suites already in the plan."""
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
    """Per-story rows with effort estimate. ids = list[int]."""
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
                     "cases": cases, "boost": boost, "hours": hours})
    return rows


def plan_payload(app):
    rows = app._reg_selected_rows or []
    names = list(app._reg_res_names or [])
    count = app._reg_res_count or len(names) or 1
    total_cases = sum(r["cases"] for r in rows)
    total_hours = round(sum(r["hours"] for r in rows), 2)
    per_person = round(total_hours / count, 2) if count else total_hours
    sprint = (app._reg_sprint_path or "").split("\\")[-1]
    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "project": app.project, "plan_id": app.plan_id,
        "plan_name": getattr(app, "plan_name", "") or "",
        "sprint": sprint,
        "avg_minutes_per_case": AVG_MINUTES_PER_CASE,
        "priority_boost": PRIORITY_BOOST,
        "resources_count": count, "resource_names": names,
        "stories": rows, "total_stories": len(rows),
        "total_cases": total_cases, "total_hours": total_hours,
        "hours_per_person": per_person,
    }


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


_PRI_LABEL = {1: "P1 (highest)", 2: "P2", 3: "P3", 4: "P4 (lowest)"}


def export_json(app):
    p = os.path.join(_out_dir(), _stamp(app) + ".json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(plan_payload(app), f, ensure_ascii=False, indent=2)
    return p


def export_xlsx(app):
    from openpyxl import Workbook                       # lazy (optional dep)
    from openpyxl.styles import Font, PatternFill, Alignment
    d = plan_payload(app)
    wb = Workbook()
    ws = wb.active
    ws.title = "Regression Plan"
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="6A4DFF")
    meta = [("Project", d["project"]), ("Test plan", d["plan_name"]),
            ("Plan ID", d["plan_id"]), ("Sprint", d["sprint"]),
            ("Generated", d["generated"]),
            ("Avg min / case", d["avg_minutes_per_case"]),
            ("Resources", d["resources_count"]),
            ("Resource names", ", ".join(d["resource_names"]) or "—")]
    r = 1
    for k, v in meta:
        ws.cell(r, 1, k).font = Font(bold=True)
        ws.cell(r, 2, v)
        r += 1
    r += 1
    cols = ["Story ID", "Title", "State", "Priority", "Test cases", "Est. hours"]
    for c, name in enumerate(cols, 1):
        cell = ws.cell(r, c, name)
        cell.font = head
        cell.fill = fill
    r += 1
    for s in d["stories"]:
        ws.cell(r, 1, s["id"])
        ws.cell(r, 2, s["title"])
        ws.cell(r, 3, s["state"])
        ws.cell(r, 4, _PRI_LABEL.get(s["priority"], s["priority"]))
        ws.cell(r, 5, s["cases"])
        ws.cell(r, 6, s["hours"])
        r += 1
    ws.cell(r, 4, "TOTAL").font = Font(bold=True)
    ws.cell(r, 5, d["total_cases"]).font = Font(bold=True)
    ws.cell(r, 6, d["total_hours"]).font = Font(bold=True)
    r += 1
    ws.cell(r, 4, "Hours / person").font = Font(bold=True)
    ws.cell(r, 6, d["hours_per_person"]).font = Font(bold=True)
    widths = [12, 52, 14, 16, 12, 12]
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + c)].width = w
    ws.cell(7, 2).alignment = Alignment(wrap_text=True)
    p = os.path.join(_out_dir(), _stamp(app) + ".xlsx")
    wb.save(p)
    return p


def export_docx(app):
    from docx import Document                            # lazy (optional dep)
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    d = plan_payload(app)
    doc = Document()
    title = doc.add_heading("Regression Test Plan", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = sub.add_run(f"{d['plan_name'] or d['project']}  ·  {d['sprint'] or '—'}")
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0x6A, 0x4D, 0xFF)

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
            for rn in p.runs:
                rn.font.bold = True

    doc.add_heading("Stories", level=1)
    tbl = doc.add_table(rows=1, cols=6)
    tbl.style = "Medium Shading 1 Accent 1"
    hdr = tbl.rows[0].cells
    for i, h in enumerate(["Story", "Title", "State", "Priority",
                           "Test cases", "Est. hours"]):
        hdr[i].text = h
    for s in d["stories"]:
        c = tbl.add_row().cells
        c[0].text = str(s["id"])
        c[1].text = s["title"]
        c[2].text = s["state"]
        c[3].text = _PRI_LABEL.get(s["priority"], str(s["priority"]))
        c[4].text = str(s["cases"])
        c[5].text = str(s["hours"])

    doc.add_paragraph()
    tot = doc.add_table(rows=0, cols=2)
    tot.style = "Light List Accent 1"
    for k, v in (("Total stories", d["total_stories"]),
                 ("Total test cases", d["total_cases"]),
                 ("Total estimated hours", d["total_hours"]),
                 ("Hours per resource", d["hours_per_person"])):
        cells = tot.add_row().cells
        cells[0].text = k
        cells[1].text = str(v)
        for p in cells[0].paragraphs:
            for rn in p.runs:
                rn.font.bold = True

    foot = doc.add_paragraph()
    fr = foot.add_run("Generated by QA Studio · effort references existing "
                      "test cases only.")
    fr.font.size = Pt(8)
    fr.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    p = os.path.join(_out_dir(), _stamp(app) + ".docx")
    doc.save(p)
    return p


def export_pdf(app):
    from reportlab.lib.pagesizes import A4               # lazy (optional dep)
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                    Paragraph, Spacer)
    from reportlab.lib.styles import getSampleStyleSheet
    d = plan_payload(app)
    p = os.path.join(_out_dir(), _stamp(app) + ".pdf")
    doc = SimpleDocTemplate(p, pagesize=A4, topMargin=18 * mm,
                            bottomMargin=18 * mm)
    styles = getSampleStyleSheet()
    elems = [Paragraph("Regression Test Plan", styles["Title"]),
             Paragraph(f"{d['plan_name'] or d['project']} &middot; "
                       f"{d['sprint'] or '—'}", styles["Normal"]),
             Spacer(1, 8 * mm)]
    data = [["Story", "Title", "State", "Pri", "Cases", "Hours"]]
    for s in d["stories"]:
        data.append([str(s["id"]), (s["title"] or "")[:48], s["state"],
                     str(s["priority"]), str(s["cases"]), str(s["hours"])])
    data.append(["", "", "", "TOTAL", str(d["total_cases"]),
                 str(d["total_hours"])])
    tbl = Table(data, colWidths=[20 * mm, 70 * mm, 24 * mm, 12 * mm,
                                 18 * mm, 18 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6A4DFF")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F2F0FF")),
    ]))
    elems += [tbl, Spacer(1, 6 * mm),
              Paragraph(f"Resources: {d['resources_count']} "
                        f"({', '.join(d['resource_names']) or '—'}) &middot; "
                        f"Hours per resource: <b>{d['hours_per_person']}</b>",
                        styles["Normal"])]
    doc.build(elems)
    return p


EXPORTERS = {"docx": export_docx, "xlsx": export_xlsx,
             "json": export_json, "pdf": export_pdf}
_MISSING_DEP = {
    "xlsx": "openpyxl  (pip install openpyxl)",
    "docx": "python-docx  (pip install python-docx)",
    "pdf":  "reportlab  (pip install reportlab)",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  SCREEN
# ═══════════════════════════════════════════════════════════════════════════════
def _init(app):
    for k, v in (("_reg_sprints", None), ("_reg_sprint_path", None),
                 ("_reg_sprint_stories", []), ("_reg_selected", []),
                 ("_reg_selected_rows", []), ("_reg_res_names", []),
                 ("_reg_res_count", None), ("_reg_busy", False)):
        if not hasattr(app, k):
            setattr(app, k, v)


def screen(app):
    _init(app)
    from main import card, sec_head, field_label, green_btn, ghost_btn, primary_btn

    ready = bool(app.connected and app.project and app.plan_id)
    if not ready:
        hint = ft.Container(
            ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=T.AMBER),
                    ft.Text("Connect and select a project + test plan on the Setup "
                            "screen first — the regression plan reads existing test "
                            "cases from that plan.",
                            size=12, color=T.AMBER, weight=ft.FontWeight.W_500,
                            expand=True)], spacing=8),
            padding=12, bgcolor=T.AMBER_SOFT, border_radius=T.R,
            border=ft.Border.all(1, "#EAD9A8"))
        return app.shell("Regression Plan",
                         "Build a regression plan from existing sprints & stories",
                         hint)

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
        path = sprint_dd.value
        app._reg_sprint_path = path
        app._reg_sprint_stories = []
        app._reg_busy = True
        app.render()

        def _load():
            try:
                st = E.fetch_stories_in_iteration(app.project, path)
            except Exception:
                st = []
            app._reg_sprint_stories = st
            app._reg_busy = False
            app.ui_safe(app.render)
        threading.Thread(target=_load, daemon=True).start()

    def _add_story(e):
        v = story_dd.value
        if not v:
            return
        sid = int(v)
        if sid not in selected_ids:
            title = next((s["title"] for s in app._reg_sprint_stories
                          if s["id"] == sid), "")
            app._reg_selected.append({"id": sid, "title": title})
            app._reg_selected_rows = []      # stale → force recalculate
        app.render()

    def _remove_story(sid):
        app._reg_selected = [s for s in app._reg_selected if s["id"] != sid]
        app._reg_selected_rows = []
        app.render()

    def _use_setup_selection(e):
        for sid in (app.story_ids or []):
            if int(sid) not in [s["id"] for s in app._reg_selected]:
                app._reg_selected.append({"id": int(sid), "title": ""})
        app._reg_selected_rows = []
        app.render()

    def _add_name(e):
        name = (name_field.value or "").strip()
        if name and name not in app._reg_res_names:
            app._reg_res_names.append(name)
        name_field.value = ""
        app.render()

    def _remove_name(nm):
        app._reg_res_names = [n for n in app._reg_res_names if n != nm]
        app.render()

    def _on_count(e):
        v = (count_field.value or "").strip()
        app._reg_res_count = int(v) if v.isdigit() and int(v) > 0 else None

    def _calculate(e):
        if not app._reg_selected:
            app._toast("Select at least one story first.")
            return
        app._reg_busy = True
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
                app._toast(f"{fmt.upper()} needs {_MISSING_DEP.get(fmt, fmt)}")
                return
            except Exception as ex:
                app._toast(f"Export failed: {ex}")
                return
            app._toast(f"Saved: {path}")
            try:
                os.startfile(os.path.dirname(path))   # Windows: open the folder
            except Exception:
                pass
        return _do

    # ── Card A: stories ──
    sprint_opts = [ft.DropdownOption(key=s["path"], text=s["name"])
                   for s in (app._reg_sprints or [])]
    sprint_dd = ft.Dropdown(
        value=app._reg_sprint_path, hint_text="Select sprint / iteration",
        options=sprint_opts, on_select=_on_sprint,
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=8),
        text_size=13, filled=True, bgcolor=T.CARD, expand=True)

    story_opts = [ft.DropdownOption(key=str(s["id"]),
                                    text=f"[{s['id']}] {(s['title'] or '')[:48]}")
                  for s in app._reg_sprint_stories if s["id"] not in selected_ids]
    story_dd = ft.Dropdown(
        hint_text=("Loading…" if app._reg_busy and app._reg_sprint_path
                   else "Pick a story to add"),
        options=story_opts, on_select=_add_story,
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=8),
        text_size=13, filled=True, bgcolor=T.CARD, expand=True,
        disabled=not app._reg_sprint_stories)

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

    story_chips = ft.Row(
        [_chip(str(s["id"]), (lambda e, x=s["id"]: _remove_story(x)))
         for s in app._reg_selected],
        wrap=True, spacing=6, run_spacing=6)

    card_a = card(ft.Column([
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

    # ── Card B: resources ──
    count_field = ft.TextField(
        value=("" if app._reg_res_count is None else str(app._reg_res_count)),
        hint_text="e.g. 3", keyboard_type=ft.KeyboardType.NUMBER,
        on_change=_on_count, width=120, text_size=13,
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=10))
    name_field = ft.TextField(
        hint_text="Type a name, press Enter", on_submit=_add_name, expand=True,
        text_size=13, border_color=T.BORDER, focused_border_color=T.VIOLET,
        border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=12, horizontal=10))
    name_chips = ft.Row(
        [_chip(n, (lambda e, x=n: _remove_name(x))) for n in app._reg_res_names],
        wrap=True, spacing=6, run_spacing=6)

    card_b = card(ft.Column([
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
    ], spacing=0))

    # ── Card C: effort model (hardcoded, shown read-only) ──
    boost_txt = "  ".join(f"{_PRI_LABEL[k].split()[0]}×{v}"
                          for k, v in sorted(PRIORITY_BOOST.items()))
    card_c = card(ft.Column([
        sec_head("3", "Effort model"),
        ft.Container(height=8),
        ft.Text(f"{AVG_MINUTES_PER_CASE} min / existing test case, weighted by "
                f"story priority:", size=12, color=T.INK_2,
                weight=ft.FontWeight.W_500),
        ft.Text(boost_txt, size=12, color=T.VIOLET_INK,
                weight=ft.FontWeight.BOLD, font_family=T.F_MONO),
    ], spacing=2))

    # ── results ──
    results = None
    if app._reg_selected_rows:
        d = plan_payload(app)
        head = ft.Row([
            ft.Container(ft.Text("Story", size=11, weight=ft.FontWeight.BOLD,
                                 color=T.INK_3), width=70),
            ft.Container(ft.Text("Title", size=11, weight=ft.FontWeight.BOLD,
                                 color=T.INK_3), expand=True),
            ft.Container(ft.Text("Pri", size=11, weight=ft.FontWeight.BOLD,
                                 color=T.INK_3), width=44),
            ft.Container(ft.Text("Cases", size=11, weight=ft.FontWeight.BOLD,
                                 color=T.INK_3), width=54),
            ft.Container(ft.Text("Hours", size=11, weight=ft.FontWeight.BOLD,
                                 color=T.INK_3), width=54),
        ], spacing=8)
        body = [head, ft.Divider(height=1, color=T.BORDER)]
        for s in d["stories"]:
            body.append(ft.Row([
                ft.Container(ft.Text(str(s["id"]), size=12, font_family=T.F_MONO,
                                     color=T.VIOLET_INK,
                                     weight=ft.FontWeight.BOLD), width=70),
                ft.Container(ft.Text(s["title"] or "—", size=12, color=T.INK,
                                     no_wrap=False), expand=True),
                ft.Container(ft.Text(f"P{s['priority']}", size=12, color=T.INK_2),
                             width=44),
                ft.Container(ft.Text(str(s["cases"]), size=12, color=T.INK_2),
                             width=54),
                ft.Container(ft.Text(str(s["hours"]), size=12, color=T.INK,
                                     weight=ft.FontWeight.BOLD), width=54),
            ], spacing=8))
        totals = ft.Row([
            ft.Container(ft.Text(f"{d['total_stories']} stories", size=12,
                                 weight=ft.FontWeight.BOLD, color=T.INK), expand=True),
            ft.Text(f"{d['total_cases']} cases", size=12, color=T.INK_2,
                    weight=ft.FontWeight.BOLD),
            ft.Container(width=16),
            ft.Text(f"{d['total_hours']} h total", size=12, color=T.INK,
                    weight=ft.FontWeight.BOLD),
            ft.Container(width=16),
            ft.Container(
                ft.Text(f"{d['hours_per_person']} h / person", size=12,
                        color=T.GREEN, weight=ft.FontWeight.BOLD),
                padding=ft.Padding.symmetric(vertical=4, horizontal=10),
                bgcolor=T.GREEN_SOFT, border_radius=T.R_SM),
        ], spacing=4)
        exports = ft.Row([
            green_btn("Word", icon=ft.Icons.DESCRIPTION, on_click=_export("docx")),
            ghost_btn("Excel", icon=ft.Icons.GRID_ON, on_click=_export("xlsx")),
            ghost_btn("JSON", icon=ft.Icons.DATA_OBJECT, on_click=_export("json")),
            ghost_btn("PDF", icon=ft.Icons.PICTURE_AS_PDF, on_click=_export("pdf")),
        ], spacing=10, wrap=True)
        results = card(ft.Column([
            sec_head("4", "Plan", right=ft.Text("export →", size=11,
                                                color=T.INK_3,
                                                weight=ft.FontWeight.BOLD)),
            ft.Container(height=10),
            ft.Column(body, spacing=8),
            ft.Divider(height=1, color=T.BORDER),
            ft.Container(totals, padding=ft.Padding.only(top=6, bottom=10)),
            exports,
        ], spacing=0))

    calc_label = "Calculating…" if app._reg_busy else "Calculate plan"
    calc_btn = primary_btn(calc_label, icon=ft.Icons.CALCULATE,
                           on_click=_calculate,
                           disabled=app._reg_busy or not app._reg_selected)

    body_children = [card_a, ft.Container(height=14), card_b,
                     ft.Container(height=14), card_c,
                     ft.Container(height=16), calc_btn]
    if results is not None:
        body_children += [ft.Container(height=16), results]

    body = ft.Column(body_children, spacing=0, scroll=ft.ScrollMode.AUTO,
                     expand=True)
    return app.shell("Regression Plan",
                     "Build a regression plan from existing sprints & stories",
                     body)
