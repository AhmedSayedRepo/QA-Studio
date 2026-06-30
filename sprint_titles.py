"""sprint_titles.py — the "Sprint Report" screen.

Pick a sprint and build a Sprint (closure) Report: its User Stories grouped by
status (Completed vs In-progress / carried over), with the titles AI-translated to
the chosen language, plus a Bugs summary (total, regression vs sprint, and a
by-status breakdown). Shown in-app and downloadable as a Word document — RTL for
Arabic, LTR for English.

Self-contained: reuses regression.py's multiselect/helpers and engine.py's Azure
+ AI calls, but keeps its own `_st_*` state so it never collides with Sprint Plan.
main.py imports this and dispatches `screen(app)` for the "titles" nav tab.
"""
import os
import re
import json
import threading
from datetime import datetime

import flet as ft
import theme as T
import engine as E

# Story states that count as "done" for the report.
_DONE = {"done", "closed", "completed", "resolved", "accepted"}

# Localized labels (headings are fixed strings; story titles are AI-translated).
_L = {
    "ar": {
        "title": "تقرير إغلاق السبرنت", "sprint": "السبرنت", "date": "التاريخ",
        "completed": "النقاط المكتملة", "carried": "نقاط قيد التنفيذ / مرحّلة",
        "bugs": "الأخطاء (Bugs)", "total_bugs": "إجمالي الأخطاء",
        "regression_bugs": "أخطاء الـ Regression", "sprint_bugs": "أخطاء السبرنت",
        "by_status": "حسب الحالة", "stories": "قصة", "none": "لا يوجد.",
        "other": "أهداف أخرى", "objectives": "أهداف السبرنت",
    },
    "en": {
        "title": "Sprint Closure Report", "sprint": "Sprint", "date": "Date",
        "completed": "Completed", "carried": "In progress / carried over",
        "bugs": "Bugs", "total_bugs": "Total bugs",
        "regression_bugs": "Regression bugs", "sprint_bugs": "Sprint bugs",
        "by_status": "By status", "stories": "stories", "none": "None.",
        "other": "Other objectives", "objectives": "Sprint objectives",
    },
}

# Ordinal prefixes for epic group headings (mirrors the reference report's
# أولاً / ثانياً / ثالثاً … grouping).
_ORD = {
    "ar": ["أولاً", "ثانياً", "ثالثاً", "رابعاً", "خامساً", "سادساً",
           "سابعاً", "ثامناً", "تاسعاً", "عاشراً"],
    "en": ["First", "Second", "Third", "Fourth", "Fifth", "Sixth",
           "Seventh", "Eighth", "Ninth", "Tenth"],
}


def _group_by_epic(rows):
    """Group stories by their epic, preserving first-seen order; stories with no
    epic ("") are collected into a final group. Returns [(epic_name, [stories])]."""
    groups, order = {}, []
    for s in rows:
        e = (s.get("epic") or "").strip()
        if e not in groups:
            groups[e] = []
            order.append(e)
        groups[e].append(s)
    named = [e for e in order if e]
    rest = [e for e in order if not e]
    return [(e, groups[e]) for e in named + rest]


def _init(app):
    for k, v in (("_st_iterations", []), ("_st_iter_loading", False),
                 ("_st_sprint_paths", []), ("_st_open", False),
                 ("_st_lang", getattr(app, "lang", "ar")),
                 ("_st_busy", False), ("_st_report", None),
                 ("_st_done", False), ("_st_msg", None)):
        if not hasattr(app, k):
            setattr(app, k, v)


def _sprint_num(text):
    m = re.search(r"[Ss]print\s*\d+", text or "")
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else ""


def _sort_key(it):
    m = re.search(r"\d+", _sprint_num(it.get("name", "")) or _sprint_num(it.get("path", "")))
    return int(m.group(0)) if m else -1


def _load_iterations(app):
    if app._st_iterations or app._st_iter_loading:
        return
    app._st_iter_loading = True

    def _work():
        try:
            its = E.fetch_iterations(app.project) or []
        except Exception:
            its = []
        sprints = [it for it in its
                   if (_sprint_num(it.get("name", "")) or _sprint_num(it.get("path", "")))] or its
        sprints.sort(key=lambda it: (_sort_key(it) < 0, _sort_key(it)))
        app._st_iterations = sprints
        app._st_iter_loading = False
        app.ui_safe(app.render)
    threading.Thread(target=_work, daemon=True).start()


# Arabic-Indic (٠-٩) and Persian (۰-۹) digits → ASCII, so a model that numbers
# its Arabic output with native digits still parses.
_AR_DIGITS = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_AR_DIGITS.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
# RTL/LTR marks and the bidi isolate chars a model may prepend to each line.
_BIDI_MARKS = "".join(("‎", "‏", "‪", "‫", "‬",
                       "⁦", "⁧", "⁨", "⁩", "﻿"))


def _clean_line(s):
    return s.translate(_AR_DIGITS).strip().strip(_BIDI_MARKS).strip()


def _parse_json_array(out, n):
    """Pull a JSON array of `n` strings out of a model response; None if it can't."""
    try:
        s = out[out.index("["):out.rindex("]") + 1]
        arr = json.loads(s)
    except Exception:
        return None
    if isinstance(arr, list) and len(arr) == n:
        return [(_clean_line(str(a)) or None) for a in arr]
    return None


def _parse_numbered(out, n):
    """Fallback line parser: numbered (digit-normalized) or positional."""
    by_num, ordered = {}, []
    for raw in out.splitlines():
        line = _clean_line(raw)
        if not line:
            continue
        m = re.match(r"^(\d+)[.)\-:]\s*(.+)$", line)
        if m:
            val = m.group(2).strip()
            by_num[int(m.group(1))] = val
            ordered.append(val)
        else:
            ordered.append(line)
    mapped = [by_num.get(i + 1) for i in range(n)]
    if sum(1 for v in mapped if v) < n and len(ordered) == n:
        mapped = ordered
    return mapped


def _translate_chunk(texts, target):
    """Translate one batch; returns a list aligned 1:1 (per-item fallback to the
    original on any miss). Asks for a JSON array (most robust), falls back to a
    numbered/positional line parse if the model ignores the JSON instruction."""
    payload = json.dumps(texts, ensure_ascii=False)
    prompt = (
        f"Translate each string in this JSON array into {target}. Translate the "
        "meaning naturally and concisely; keep IDs, version numbers and obvious "
        "product names sensible. Return ONLY a JSON array of the translated "
        "strings — same length, same order, no keys, no commentary, no code "
        f"fences.\n\n{payload}")
    # Let credit/error bubble up so the caller can tell the user (don't silently
    # fall back to English on an out-of-credit / failed provider).
    out = E.ai_complete(prompt, max_tokens=4096, want_json=True) or ""
    mapped = _parse_json_array(out, len(texts)) or _parse_numbered(out, len(texts))
    return [(mapped[i] or texts[i]) for i in range(len(texts))]


def _translate(texts, lang):
    """AI-translate `texts` into `lang` ('ar'/'en'). Returns (results, err) where
    `results` is aligned 1:1 with the input (originals kept on any miss) and `err`
    is None on success, "credit" when the AI account is out of credit/quota, or
    "error:<msg>" for any other failure. Normalizes native-digit numbering, falls
    back to positional line alignment, and chunks long lists so nothing truncates."""
    texts = [t or "" for t in texts]
    if not any(t.strip() for t in texts):
        return list(texts), None
    target = "Arabic" if lang == "ar" else "English"
    out, CHUNK, err = [], 20, None
    for i in range(0, len(texts), CHUNK):
        chunk = texts[i:i + CHUNK]
        try:
            out.extend(_translate_chunk(chunk, target))
        except E.CreditBalanceError:
            err = "credit"
            out.extend(chunk)            # keep originals for this + remaining
            out.extend(texts[i + CHUNK:])
            break
        except Exception as ex:
            err = err or ("error:" + str(ex)[:160])
            out.extend(chunk)            # keep originals for this chunk, keep going
    # pad just in case a break left it short
    while len(out) < len(texts):
        out.append(texts[len(out)])
    return out[:len(texts)], err


def _generate(app):
    # Permission gate: read-only users can't generate.
    if getattr(app, "readonly", False):
        app._st_msg = ("err", "Your role doesn’t allow generating sprint reports.")
        app.ui_safe(app.render)
        return
    paths = list(app._st_sprint_paths or [])
    if not paths:
        app._st_msg = ("err", "Pick at least one sprint first.")
        app.ui_safe(app.render)
        return
    lang = app._st_lang
    app._st_busy = True
    app._st_done = False
    app._st_report = None
    app._st_msg = None
    app.ui_safe(app.render)

    def _work():
        try:
            names, seen_s, seen_b = [], set(), set()
            stories, bugs = [], []
            for p in paths:
                it = next((x for x in app._st_iterations if x["path"] == p), None)
                if it:
                    names.append(_sprint_num(it["name"]) or it["name"])
                try:
                    d = E.sprint_report_data(app.project, p)
                except Exception:
                    d = {"stories": [], "bugs": []}
                for s in d.get("stories", []):
                    if s["id"] in seen_s:
                        continue
                    seen_s.add(s["id"])
                    stories.append(s)
                for b in d.get("bugs", []):
                    if b["id"] in seen_b:
                        continue
                    seen_b.add(b["id"])
                    bugs.append(b)

            # translate every title once, then split into sections by state
            originals = [s["title"] for s in stories]
            titles, terr = _translate(originals, lang)
            for s, tr in zip(stories, titles):
                s["t"] = tr
            n_changed = sum(1 for o, t in zip(originals, titles)
                            if (t or "").strip() != (o or "").strip())
            completed = [s for s in stories if (s.get("state", "").lower() in _DONE)]
            carried = [s for s in stories if (s.get("state", "").lower() not in _DONE)]

            from collections import Counter
            reg = sum(1 for b in bugs if "regression" in (b.get("tags", "") or "").lower())
            app._st_report = {
                "sprint_name": ", ".join(names),
                "date": datetime.now().strftime("%d-%m-%Y"),
                "lang": lang,
                "completed": completed, "carried": carried,
                "total_stories": len(stories),
                "bug_by_state": dict(Counter(b.get("state", "Unknown") for b in bugs)),
                "total_bugs": len(bugs), "regression_bugs": reg,
                "sprint_bugs": len(bugs) - reg,
            }
            app._st_done = True
            if terr == "credit":
                app._st_msg = ("err", "Report built, but the AI account is OUT OF CREDIT "
                               "— titles kept in their original language. Top up or switch "
                               "provider in Setup, then Generate again.")
            elif terr and terr.startswith("error:"):
                app._st_msg = ("err", f"Report built, but translation failed: "
                               f"{terr.split(':', 1)[1].strip()} — titles kept as-is.")
            elif stories and n_changed == 0:
                app._st_msg = ("err", "Stories & bugs loaded, but the AI returned no "
                               "translation (0 titles changed) — check the AI provider "
                               "in Setup. Showing the original titles.")
            else:
                app._st_msg = ("ok", f"Report ready — {len(stories)} stories "
                               f"({n_changed} translated), {len(bugs)} bugs.")
        except Exception as ex:
            app._st_msg = ("err", f"Couldn't build the report: {ex}")
        app._st_busy = False
        if getattr(app, "active", None) == "titles":
            app.ui_safe(app.render)
    threading.Thread(target=_work, daemon=True).start()


def _retranslate(app):
    """Re-translate an existing report's titles into the current language (used when
    the language toggle is flipped after a report was generated, so titles update
    live instead of only the static labels)."""
    r = app._st_report
    if not r:
        app.ui_safe(app.render)
        return
    lang = app._st_lang
    if r.get("lang") == lang:
        app.ui_safe(app.render)
        return
    app._st_busy = True
    app._st_msg = None
    app.ui_safe(app.render)

    def _work():
        try:
            rows = list(r.get("completed", [])) + list(r.get("carried", []))
            originals = [s["title"] for s in rows]
            titles, terr = _translate(originals, lang)
            for s, t in zip(rows, titles):
                s["t"] = t
            r["lang"] = lang
            n_changed = sum(1 for o, t in zip(originals, titles)
                            if (t or "").strip() != (o or "").strip())
            if terr == "credit":
                app._st_msg = ("err", "AI account is OUT OF CREDIT — titles kept as-is. "
                               "Top up or switch provider in Setup.")
            elif terr and terr.startswith("error:"):
                app._st_msg = ("err", f"Translation failed: "
                               f"{terr.split(':', 1)[1].strip()} — titles kept as-is.")
            elif rows and n_changed == 0:
                app._st_msg = ("err", "The AI returned no translation (0 titles "
                               "changed) — check the AI provider in Setup.")
            else:
                app._st_msg = ("ok", f"Translated to "
                               f"{'Arabic' if lang == 'ar' else 'English'} "
                               f"({n_changed}/{len(rows)} titles).")
        except Exception as ex:
            app._st_msg = ("err", f"Re-translate failed: {ex}")
        app._st_busy = False
        if getattr(app, "active", None) == "titles":
            app.ui_safe(app.render)
    threading.Thread(target=_work, daemon=True).start()


def _decode_assets():
    """Write the embedded brand images (logo + colour band) to temp PNGs and return
    (logo_path, band_path); ('' , '') if unavailable. Caller cleans up."""
    import base64, tempfile
    out = {}
    try:
        import report_assets as RA
        for key, b64 in (("logo", RA.LOGO_PNG_B64), ("band", RA.BAND_PNG_B64)):
            tf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tf.write(base64.b64decode(b64))
            tf.close()
            out[key] = tf.name
    except Exception:
        return "", ""
    return out.get("logo", ""), out.get("band", "")


def _export_docx(app):
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml.ns import qn, nsdecls
    from docx.oxml import OxmlElement, parse_xml

    r = app._st_report or {}
    lang = r.get("lang", "ar")
    rtl = (lang == "ar")
    L = _L[lang]
    BLUE = RGBColor(0x4C, 0x94, 0xD8)         # brand blue from the reference report
    INK = RGBColor(0x1F, 0x1F, 0x1F)
    LEFT = WD_ALIGN_PARAGRAPH.LEFT
    CENTER = WD_ALIGN_PARAGRAPH.CENTER
    RIGHT = WD_ALIGN_PARAGRAPH.RIGHT

    logo_path, band_path = _decode_assets()

    doc = Document()
    try:
        ns = doc.styles["Normal"]
        ns.font.name = "Segoe UI"
        ns.font.size = Pt(11)
    except Exception:
        pass
    for sec in doc.sections:
        sec.top_margin = Inches(0.9)
        sec.bottom_margin = Inches(0.9)
        sec.left_margin = Inches(0.9)
        sec.right_margin = Inches(0.9)

    def _set_bidi(p):
        # Set the paragraph base direction to RTL. CRITICAL: <w:bidi> must precede
        # <w:spacing>/<w:ind>/<w:jc> in <w:pPr> per the schema, otherwise Word
        # silently ignores it (LibreOffice is lenient). So insert it *before* the
        # first of those rather than appending at the end.
        try:
            p.alignment = RIGHT
            pPr = p._p.get_or_add_pPr()
            if pPr.find(qn("w:bidi")) is None:
                bidi = OxmlElement("w:bidi")
                bidi.set(qn("w:val"), "1")
                anchor = None
                for tag in ("w:spacing", "w:ind", "w:jc", "w:rPr"):
                    found = pPr.find(qn(tag))
                    if found is not None:
                        anchor = found
                        break
                if anchor is not None:
                    anchor.addprevious(bidi)
                else:
                    pPr.append(bidi)
        except Exception:
            pass
        return p

    def _rtl_run(run):
        # Mark the run itself RTL (<w:rtl/> in rPr) — required for Word to lay the
        # Arabic out right-to-left, not just right-aligned.
        try:
            rPr = run._element.get_or_add_rPr()
            el = OxmlElement("w:rtl")
            el.set(qn("w:val"), "1")
            rPr.append(el)
        except Exception:
            pass
        return run

    def _para(text="", size=11, bold=False, underline=False, color=None,
              align=None, before=3, after=3, rtl_p=False):
        p = doc.add_paragraph()
        run = None
        if text:
            run = p.add_run(text)
            run.bold = bold
            run.underline = underline
            run.font.size = Pt(size)
            run.font.name = "Segoe UI"
            if color is not None:
                run.font.color.rgb = color
        pf = p.paragraph_format
        pf.space_before = Pt(before)
        pf.space_after = Pt(after)
        if rtl_p:
            if run is not None:
                _rtl_run(run)
            _set_bidi(p)
        if align is not None:
            p.alignment = align
        return p

    def _bullet(text, size=11, bold=True, color=None):
        # RTL reports: bidi paragraph + RTL run so the bullet sits on the RIGHT
        # (like the reference). LTR reports: normal left bullet. Hanging indent
        # keeps wrapped lines aligned under the text, not the bullet.
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(1)
        if rtl:
            pf.right_indent = Inches(0.25)
            pf.first_line_indent = Inches(-0.25)
        else:
            pf.left_indent = Inches(0.25)
            pf.first_line_indent = Inches(-0.25)
        run = p.add_run("•  " + (text or ""))
        run.bold = bold
        run.font.size = Pt(size)
        run.font.name = "Segoe UI"
        run.font.color.rgb = INK if color is None else color
        if rtl:
            _rtl_run(run)
            _set_bidi(p)
        return p

    def _img(path, width_in, align):
        if not path or not os.path.exists(path):
            return
        try:
            p = doc.add_paragraph()
            p.alignment = align
            p.add_run().add_picture(path, width=Inches(width_in))
        except Exception:
            pass

    def _setcell(cell, runs, align=LEFT):
        cell.text = ""
        p = cell.paragraphs[0]
        p.alignment = align
        for txt, bold, col in runs:
            rr = p.add_run(str(txt))
            rr.bold = bold
            rr.font.size = Pt(10.5)
            rr.font.name = "Segoe UI"
            if col is not None:
                rr.font.color.rgb = col

    def _add_watermark():
        """Faint, centred logo behind the text on every page (via the header)."""
        if not logo_path or not os.path.exists(logo_path):
            return
        try:
            section = doc.sections[0]
            header = section.header
            header.is_linked_to_previous = False
            rId, _image = header.part.get_or_add_image(logo_path)
            cx = int(Inches(5.2))
            cy = int(cx * 52 / 246)          # logo aspect 246×52
            xml = (
                f'<w:r {nsdecls("w", "wp", "a", "pic", "r")}><w:drawing>'
                '<wp:anchor behindDoc="1" distT="0" distB="0" distL="0" distR="0" '
                'simplePos="0" locked="0" layoutInCell="1" allowOverlap="1" '
                'relativeHeight="0">'
                '<wp:simplePos x="0" y="0"/>'
                '<wp:positionH relativeFrom="margin"><wp:align>center</wp:align></wp:positionH>'
                '<wp:positionV relativeFrom="margin"><wp:align>center</wp:align></wp:positionV>'
                f'<wp:extent cx="{cx}" cy="{cy}"/>'
                '<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapNone/>'
                '<wp:docPr id="77" name="Watermark"/><wp:cNvGraphicFramePr/>'
                '<a:graphic><a:graphicData '
                'uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                '<pic:pic><pic:nvPicPr><pic:cNvPr id="77" name="Watermark"/>'
                '<pic:cNvPicPr/></pic:nvPicPr>'
                f'<pic:blipFill><a:blip r:embed="{rId}"><a:alphaModFix amt="18000"/></a:blip>'
                '<a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
                '</pic:pic></a:graphicData></a:graphic></wp:anchor>'
                '</w:drawing></w:r>')
            p = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
            p._p.append(parse_xml(xml))
        except Exception:
            pass

    # ── Header band + logo ──────────────────────────────────────────────────
    _img(logo_path, 1.9, (RIGHT if rtl else LEFT))
    _img(band_path, 6.6, CENTER)

    # ── Title (always English, centred, black) + meta (English, left) ───────
    _para("Sprint Closure Report", size=16, bold=True, color=INK,
          align=CENTER, before=10, after=10)
    _para(f"Sprint: {r.get('sprint_name','')}", size=11, bold=True,
          align=LEFT, before=2, after=2)
    _para(f"Date: {r.get('date','')}", size=11, bold=True,
          align=LEFT, before=2, after=8)

    # ── Objectives + sections grouped by epic with ordinals ─────────────────
    _para(f"{L['objectives']}:", size=13, bold=True, color=BLUE,
          rtl_p=rtl, before=10, after=4)

    def _section(label, rows):
        _para(f"{label}:", size=13, bold=True, underline=True, color=BLUE,
              rtl_p=rtl, before=8, after=4)
        if not rows:
            _para(L["none"], size=11, color=INK, rtl_p=rtl)
            return
        groups = _group_by_epic(rows)
        show_groups = any(e for e, _ in groups)
        for gi, (epic, grp) in enumerate(groups):
            if show_groups:
                ordn = _ORD[lang][gi] if gi < len(_ORD[lang]) else str(gi + 1)
                _para(f"{ordn}: {epic or L['other']}:", size=12, bold=True,
                      underline=True, color=INK, rtl_p=rtl, before=6, after=2)
            for s in grp:
                _bullet(s.get("t") or s.get("title") or "")

    _section(L["completed"], r.get("completed", []))
    _section(L["carried"], r.get("carried", []))

    # ── Bugs summary table (reference layout: merged Total cell, blue labels) ─
    _para("", before=6, after=0)
    statuses = list((r.get("bug_by_state") or {}).items())
    tbl = doc.add_table(rows=2 + len(statuses), cols=2)
    tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    try:
        tbl.style = "Table Grid"
    except Exception:
        pass
    for row in tbl.rows:
        row.cells[0].width = Inches(2.3)
        row.cells[1].width = Inches(4.0)
    try:
        tbl.cell(0, 0).merge(tbl.cell(1, 0))
    except Exception:
        pass
    sprint_name = r.get("sprint_name", "")
    _setcell(tbl.cell(0, 0),
             [(f"Total No of Bugs: {r.get('total_bugs', 0)}", True, BLUE)])
    _setcell(tbl.cell(0, 1),
             [("Regression bugs:  ", True, BLUE), (r.get("regression_bugs", 0), True, INK)])
    _setcell(tbl.cell(1, 1),
             [(f"Sprint {sprint_name} bugs:  ", True, BLUE), (r.get("sprint_bugs", 0), True, INK)])
    for i, (stt, cnt) in enumerate(statuses, start=2):
        _setcell(tbl.cell(i, 0), [(str(stt), True, BLUE)])
        _setcell(tbl.cell(i, 1), [(cnt, True, INK)])

    _add_watermark()

    out_dir = os.path.join(os.path.expanduser("~"), "QA Studio", "Sprint Reports")
    os.makedirs(out_dir, exist_ok=True)
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", r.get("sprint_name", "") or "sprint").strip("_") or "sprint"
    path = os.path.join(out_dir, f"SprintReport_{base}_{datetime.now():%Y%m%d-%H%M}.docx")
    doc.save(path)
    for tmp in (logo_path, band_path):
        try:
            if tmp:
                os.remove(tmp)
        except Exception:
            pass
    return path


def screen(app):
    _init(app)
    import regression as R
    from main import (card, sec_head, field_label, primary_btn, green_btn, ghost_btn)

    if not (app.connected and app.project):
        return R.locked_state(
            app, "Sprint Report",
            "A sprint closure report — stories by status + bug summary, Arabic or English",
            "Connect your Azure DevOps account on the Setup screen, then pick a "
            "sprint here.")

    _load_iterations(app)
    lang = app._st_lang
    L = _L[lang]
    rtl = (lang == "ar")
    _ral = ft.TextAlign.RIGHT if rtl else ft.TextAlign.LEFT

    # in-place enable/disable of the Generate button when sprints change (the
    # picker is in-place, so without this the button stayed disabled until a render)
    _gen_cell = [None]

    def _sync_gen():
        b = _gen_cell[0]
        if b is None:
            return
        ok = bool(app._st_sprint_paths) and not app._st_busy \
            and not getattr(app, "readonly", False)
        try:
            b.opacity = 1.0 if ok else 0.45
            b.on_click = (lambda e: _generate(app)) if ok else None
            b.update()
        except Exception:
            pass

    def _set_lang(k):
        new = "en" if k == "en" else "ar"
        if new == app._st_lang:
            return
        app._st_lang = new
        # If a report is already on screen, re-translate its titles live so the
        # toggle changes the content, not just the static labels.
        if app._st_report and not app._st_busy:
            _retranslate(app)
        else:
            app.ui_safe(app.render)

    def _lang_seg():
        def seg(label, key):
            sel = (app._st_lang == key)
            return ft.Container(
                ft.Text(label, size=12, weight=ft.FontWeight.BOLD,
                        color=(T.VIOLET_INK if sel else T.INK_2)),
                height=32, alignment=ft.Alignment.CENTER,
                padding=ft.Padding.symmetric(horizontal=16),
                bgcolor=(T.VIOLET_SOFT if sel else None), border_radius=T.R_SM,
                border=ft.Border.all(1, T.VIOLET if sel else ft.Colors.TRANSPARENT),
                on_click=lambda e, k=key: _set_lang(k))
        return ft.Container(
            ft.Row([seg("العربية", "ar"), seg("English", "en")], spacing=4, tight=True),
            padding=4, bgcolor=T.CARD_2, border_radius=T.R, border=ft.Border.all(1, T.BORDER))

    def _toggle(key, checked):
        s = set(app._st_sprint_paths)
        s.add(key) if checked else s.discard(key)
        app._st_sprint_paths = [p for p in (x["path"] for x in app._st_iterations) if p in s]
        _sync_gen()

    def _all(checked):
        app._st_sprint_paths = [x["path"] for x in app._st_iterations] if checked else []
        _sync_gen()

    def _open():
        app._st_open = not app._st_open

    picker = (ft.Container(R._txt("Loading sprints…", color=T.INK_3, size=12), padding=10)
              if app._st_iter_loading else
              R._checkbox_multiselect(
                  [(it["path"], (_sprint_num(it["name"]) or it["name"]) + f"   ·   {it['path']}")
                   for it in app._st_iterations],
                  app._st_sprint_paths, _toggle, _all, is_open=app._st_open, on_open=_open,
                  placeholder="Select sprint(s)", empty="No sprints found for this project.",
                  page=app.page, app=app, sync_key="st_sprints"))

    card1 = card(ft.Column([
        sec_head("1", "Sprint & language"),
        ft.Container(height=10),
        ft.Column([field_label("Sprint(s)", req=True), picker], spacing=6),
        ft.Container(height=12),
        ft.Row([field_label("Report language"), ft.Container(expand=True), _lang_seg()],
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
    ], spacing=0))

    _ro = bool(getattr(app, "readonly", False))
    _can_gen = bool(app._st_sprint_paths) and not app._st_busy and not _ro
    gen_btn = primary_btn(
        "Generating…" if app._st_busy else "Generate sprint report",
        icon=ft.Icons.SUMMARIZE,
        on_click=((lambda e: _generate(app)) if _can_gen else None))
    try:
        # opacity must track the SAME condition as clickability (incl. read-only),
        # otherwise the button looks enabled but does nothing.
        gen_btn.opacity = 1.0 if _can_gen else 0.45
    except Exception:
        pass
    _gen_cell[0] = gen_btn

    body_children = [card1, ft.Container(height=16), gen_btn]

    if app._st_msg and not app._st_busy:
        kind, text = app._st_msg
        _ok = (kind == "ok")
        body_children += [ft.Container(
            ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE if _ok else ft.Icons.ERROR_OUTLINE,
                            color=(T.GREEN if _ok else T.RED), size=18),
                    R._txt(text, color=(T.GREEN if _ok else T.RED), size=12.5,
                           no_wrap=False, expand=True)],
                   spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(vertical=11, horizontal=14),
            margin=ft.Margin.only(top=14), border_radius=T.R,
            bgcolor=(T.GREEN_SOFT if _ok else T.RED_SOFT),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.4, T.GREEN if _ok else T.RED)))]

    if app._st_busy:
        body_children += [ft.Container(
            ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=T.VIOLET),
                    R._txt("Fetching stories & bugs, translating titles…",
                           color=T.INK_3, size=12.5)], spacing=10),
            padding=ft.Padding.symmetric(vertical=12, horizontal=14),
            margin=ft.Margin.only(top=14),
            bgcolor=getattr(T, "VIOLET_SOFT", T.CARD_2), border_radius=T.R)]
    elif app._st_done and app._st_report:
        r = app._st_report

        _cross = ft.CrossAxisAlignment.END if rtl else ft.CrossAxisAlignment.START
        _main = ft.MainAxisAlignment.END if rtl else ft.MainAxisAlignment.START

        def _drow(kids, **kw):
            return ft.Row(list(reversed(kids)) if rtl else kids, **kw)

        def _item(text_val, accent, stripe):
            bullet = ft.Container(width=7, height=7, border_radius=999, bgcolor=accent,
                                  margin=ft.Margin.only(top=6))
            txt = R._txt(text_val, color=T.INK, size=13.5, no_wrap=False,
                         expand=True, text_align=_ral)
            return ft.Container(
                _drow([bullet, txt], spacing=12,
                      vertical_alignment=ft.CrossAxisAlignment.START),
                padding=ft.Padding.symmetric(vertical=8, horizontal=12),
                border_radius=T.R_SM,
                bgcolor=(T.CARD_2 if stripe else ft.Colors.TRANSPARENT))

        def _epic_head(text_val, accent):
            return ft.Container(
                _drow([R._txt(text_val, color=accent, weight=ft.FontWeight.W_800,
                              size=12.5, text_align=_ral)], alignment=_main),
                padding=ft.Padding.only(top=12, bottom=2, left=4, right=4),
                border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.with_opacity(0.25, accent))),
                margin=ft.Margin.only(bottom=4))

        def _sec(label, rows, accent, soft):
            header = ft.Container(
                _drow([
                    ft.Container(width=4, height=18, bgcolor=accent, border_radius=2),
                    R._txt(label, color=T.INK, weight=ft.FontWeight.W_900, size=14),
                    ft.Container(R._txt(f"{len(rows)}", size=11, weight=ft.FontWeight.W_800,
                                        color=accent),
                                 padding=ft.Padding.symmetric(vertical=2, horizontal=9),
                                 bgcolor=ft.Colors.with_opacity(0.16, accent),
                                 border_radius=999),
                ], spacing=10, alignment=_main,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(vertical=10, horizontal=12),
                bgcolor=soft, border_radius=T.R_SM)
            if not rows:
                body = [ft.Container(R._txt(L["none"], color=T.INK_3, size=12.5),
                                     padding=ft.Padding.symmetric(vertical=9, horizontal=12))]
            else:
                groups = _group_by_epic(rows)
                show_groups = any(e for e, _ in groups)
                body, stripe = [], 0
                for gi, (epic, grp) in enumerate(groups):
                    if show_groups:
                        ordn = _ORD[lang][gi] if gi < len(_ORD[lang]) else str(gi + 1)
                        body.append(_epic_head(f"{ordn}: {epic or L['other']}", accent))
                    for s in grp:
                        body.append(_item(s.get("t") or s.get("title") or "",
                                          accent, stripe % 2 == 1))
                        stripe += 1
            return ft.Column([header, ft.Container(height=8),
                              ft.Column(body, spacing=2)], spacing=0)

        def _stat(label, val, accent, soft):
            return ft.Container(
                ft.Column([
                    R._txt(str(val), color=accent, weight=ft.FontWeight.W_900, size=24,
                           text_align=ft.TextAlign.CENTER),
                    R._txt(label, color=T.INK_2, size=11.5, no_wrap=False,
                           text_align=ft.TextAlign.CENTER),
                ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                expand=True, padding=ft.Padding.symmetric(vertical=14, horizontal=8),
                bgcolor=soft, border_radius=T.R, border=ft.Border.all(1, T.BORDER))

        def _status_chip(st, n):
            return ft.Container(
                _drow([R._txt(str(st), color=T.INK_2, size=12),
                       ft.Container(R._txt(str(n), color=T.INK, size=12,
                                           weight=ft.FontWeight.W_800),
                                    padding=ft.Padding.symmetric(vertical=1, horizontal=7),
                                    bgcolor=ft.Colors.with_opacity(0.10, T.VIOLET),
                                    border_radius=999)],
                      spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(vertical=6, horizontal=10),
                bgcolor=T.CARD_2, border_radius=999, border=ft.Border.all(1, T.BORDER))

        bug_stats = _drow([
            _stat(L["total_bugs"], r["total_bugs"], T.VIOLET_INK, T.VIOLET_SOFT),
            _stat(L["regression_bugs"], r["regression_bugs"], T.RED, T.RED_SOFT),
            _stat(L["sprint_bugs"], r["sprint_bugs"], T.AMBER, T.AMBER_SOFT),
        ], spacing=10)
        status_items = [_status_chip(st, n)
                        for st, n in (r.get("bug_by_state") or {}).items()]
        bug_section = ft.Column([
            _drow([ft.Container(width=4, height=18, bgcolor=T.VIOLET, border_radius=2),
                   R._txt(L["bugs"], color=T.INK, weight=ft.FontWeight.W_900, size=14)],
                  spacing=10, alignment=_main,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=12), bug_stats, ft.Container(height=14),
            R._txt(L["by_status"], color=T.INK_3, size=11, weight=ft.FontWeight.BOLD,
                   text_align=_ral),
            ft.Container(height=8),
            (ft.Row(list(reversed(status_items)) if rtl else status_items,
                    wrap=True, spacing=8, run_spacing=8, alignment=_main)
             if status_items else R._txt(L["none"], color=T.INK_3, size=12)),
        ], spacing=0, horizontal_alignment=_cross)

        def _copy(e):
            lines = [f"{L['title']} — {L['sprint']} {r['sprint_name']} ({r['date']})", ""]
            for label, rows in ((L["completed"], r["completed"]), (L["carried"], r["carried"])):
                lines.append(f"{label} ({len(rows)}):")
                lines += ["  • " + (s.get("t") or s.get("title") or "") for s in rows] or ["  -"]
                lines.append("")
            lines += [f"{L['bugs']}: {L['total_bugs']} {r['total_bugs']} · "
                      f"{L['regression_bugs']} {r['regression_bugs']} · "
                      f"{L['sprint_bugs']} {r['sprint_bugs']}"]
            try:
                app.page.set_clipboard("\n".join(lines))
                app._toast("Report copied to clipboard.")
            except Exception:
                pass

        def _download(e):
            def _w():
                try:
                    p = _export_docx(app)
                    app.ui_safe(lambda: app._toast(f"Saved Word document: {p}"))
                    try:
                        os.startfile(os.path.dirname(p))
                    except Exception:
                        pass
                except ImportError:
                    app.ui_safe(lambda: app._err("Word export needs python-docx."))
                except Exception as ex:
                    app.ui_safe(lambda e=ex: app._err(f"Export failed: {ex}"))
            app._bg(_w)

        title_band = ft.Container(
            _drow([
                ft.Container(ft.Icon(ft.Icons.SUMMARIZE, color=ft.Colors.WHITE, size=20),
                             width=40, height=40, alignment=ft.Alignment.CENTER,
                             border_radius=T.R_SM,
                             gradient=ft.LinearGradient(
                                 begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT,
                                 colors=[T.VIOLET, T.VIOLET_H])),
                ft.Column([
                    R._txt(L["title"], color=T.INK, weight=ft.FontWeight.W_900, size=16,
                           text_align=_ral),
                    R._txt(f"{L['sprint']} {r['sprint_name']}  ·  {r['date']}  ·  "
                           f"{r['total_stories']} {L['stories']}",
                           color=T.INK_3, size=12, weight=ft.FontWeight.BOLD, text_align=_ral),
                ], spacing=3, expand=True, horizontal_alignment=_cross),
            ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=14, bgcolor=T.CARD_2, border_radius=T.R,
            border=ft.Border.all(1, T.BORDER))

        def _panel(child):
            return ft.Container(child, padding=14, bgcolor=T.CARD,
                                border=ft.Border.all(1, T.BORDER), border_radius=T.R)

        results = card(ft.Column([
            ft.Row([sec_head("2", "Report"), ft.Container(expand=True),
                    ghost_btn("Copy", icon=ft.Icons.CONTENT_COPY, on_click=_copy),
                    green_btn("Download Word", icon=ft.Icons.DESCRIPTION, on_click=_download)],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=12),
            title_band,
            ft.Container(height=14),
            _panel(_sec(L["completed"], r["completed"], T.GREEN, T.GREEN_SOFT)),
            ft.Container(height=12),
            _panel(_sec(L["carried"], r["carried"], T.AMBER, T.AMBER_SOFT)),
            ft.Container(height=12),
            _panel(bug_section),
        ], spacing=0))
        body_children += [ft.Container(height=16), results]

    body = ft.Column(body_children, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    return app.shell("Sprint Report",
                     "Stories by status + bug summary from a sprint, in Arabic or English",
                     body, badge="SR")
