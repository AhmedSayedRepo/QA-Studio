"""perf/report.py - render the performance report as brand-consistent HTML.

Pure/offline (stdlib only). Produces ONE self-contained HTML document that works
both as a saved/exported file AND as an email body: table-based with inline styles
(Outlook/Gmail/Apple-Mail safe), sharing the SAME masthead / metric-strip /
section-head / footer design language and cyan-teal palette as regression.py's
_plan_html and engine.py's build_report_email — so all the app's reports read as
one brand.

Stays pure: the optional QA Studio logo is passed IN via meta['logo_html'] (the
screen, which already imports engine, supplies E._logo_tag(...)); this module
never imports engine/Flet/tracker.

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from __future__ import annotations

import datetime
import html
from typing import Optional

from .models import LoadProfile, PerfResult

# Shared brand palette (mirrors regression._plan_html / engine report emails).
PAPER = "#E9E8EE"; CARD = "#FFFFFF"; TINT = "#FAFAFC"
INK = "#1B1A22"; INK2 = "#6B6975"; INK3 = "#9C9AA6"
LINE = "#E8E7EE"; LINE2 = "#F1F0F5"
VIOLET = "#0E9CC0"; VIOLET_INK = "#0B6E86"; VIOLET_SOFT = "#D6F4FB"
GREEN = "#1F8A52"; GREEN_SOFT = "#E7F4ED"
RED = "#C0362C"; RED_SOFT = "#FBE9E7"; AMBER = "#B7791F"
UI = '"Segoe UI",Roboto,Helvetica,Arial,sans-serif'
MONO = '"SFMono-Regular",Consolas,Menlo,monospace'


from diagnostic_safety import mask_query_secrets as _mask_secrets


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _gate(result: PerfResult):
    return {True: ("PASS", GREEN, GREEN_SOFT),
            False: ("FAIL", RED, RED_SOFT),
            None: ("COMPLETED", INK2, LINE2)}[result.threshold_pass]


def _sec_head(dot, title, count):
    return (f"<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
            f"<td valign='middle' style='padding-right:10px'><span style='display:inline-block;"
            f"width:9px;height:9px;border-radius:50%;background:{dot}'></span></td>"
            f"<td valign='middle' style='font-size:14.5px;font-weight:700;color:{INK};"
            f"letter-spacing:-.2px'>{_esc(title)}</td>"
            f"<td valign='middle' style='padding-left:9px'><span style='font-family:{MONO};"
            f"font-size:11px;font-weight:700;color:{INK2};background:{LINE2};border-radius:20px;"
            f"padding:3px 9px'>{_esc(count)}</span></td></tr></table>")


def _kpi_cell(k, v, unit, col, bl):
    return (f"<td width='33%' style='{bl}padding:14px 8px;text-align:center;vertical-align:top'>"
            f"<div style='font-size:9.5px;font-weight:700;letter-spacing:1px;color:{INK3};"
            f"text-transform:uppercase'>{_esc(k)}</div>"
            f"<div style='font-family:{MONO};font-size:21px;font-weight:700;color:{col};"
            f"margin-top:7px;line-height:1;white-space:nowrap'>{_esc(v)}"
            f"<span style='font-size:11px;color:{INK3};font-weight:600'> {_esc(unit)}</span></div></td>")


def _kpi_cells(result: PerfResult):
    """A 3-column x 2-row metric grid — reads cleanly inside the 640px card
    instead of cramming six tiles onto one row."""
    err_col = RED if result.error_rate > 0 else GREEN
    row1 = [("p50", f"{result.p50_ms:.0f}", "ms", INK),
            ("p90", f"{result.p90_ms:.0f}", "ms", INK),
            ("p95", f"{result.p95_ms:.0f}", "ms", VIOLET_INK)]
    row2 = [("p99", f"{result.p99_ms:.0f}", "ms", INK),
            ("Throughput", f"{result.throughput_rps:.1f}", "req/s", GREEN),
            ("Error rate", f"{result.error_rate * 100:.1f}", "%", err_col)]
    tr1 = "".join(_kpi_cell(k, v, u, c, "" if i == 0 else f"border-left:1px solid {LINE2};")
                  for i, (k, v, u, c) in enumerate(row1))
    tr2 = "".join(_kpi_cell(k, v, u, c, "" if i == 0 else f"border-left:1px solid {LINE2};")
                  for i, (k, v, u, c) in enumerate(row2))
    return (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='border:1px solid {LINE};border-radius:12px;table-layout:fixed'>"
            f"<tr>{tr1}</tr>"
            f"<tr style='border-top:1px solid {LINE2}'>{tr2}</tr></table>")


def _request_rows(result: PerfResult):
    rows = ""
    numtd = f"padding:11px 6px;text-align:right;font-family:{MONO};font-size:12.5px"
    for rs in (result.per_request or []):
        ecol = RED if rs.errors else INK2
        rows += (
            f"<tr style='border-top:1px solid {LINE2}'>"
            # break-all lets a long query-string URL wrap instead of forcing the
            # whole table (and card) wider than the page.
            f"<td style='padding:11px 14px;font-size:12.5px;font-weight:600;color:{INK};"
            f"word-break:break-all;overflow-wrap:anywhere'>{_esc(_mask_secrets(rs.label))}</td>"
            f"<td style='{numtd};color:{INK2}'>{rs.samples}</td>"
            f"<td style='{numtd};color:{ecol}'>{rs.errors}</td>"
            f"<td style='{numtd};color:{ecol}'>{rs.error_rate * 100:.1f}%</td>"
            f"<td style='{numtd};color:{INK2}'>{rs.avg_ms:.0f}</td>"
            f"<td style='{numtd};color:{INK2}'>{rs.min_ms:.0f}</td>"
            f"<td style='{numtd};font-weight:700;color:{INK}'>{rs.p95_ms:.0f}</td>"
            f"<td style='{numtd};padding-right:14px;color:{INK2}'>{rs.max_ms:.0f}</td>"
            f"</tr>")
    if not rows:
        rows = (f"<tr style='border-top:1px solid {LINE2}'><td colspan='8' "
                f"style='padding:14px;font-size:13px;color:{INK3}'>No per-request breakdown "
                f"available.</td></tr>")
    cols = [("Request", "text-align:left"), ("Samples", "text-align:right"),
            ("Errors", "text-align:right"), ("Err %", "text-align:right"),
            ("Avg ms", "text-align:right"), ("Min ms", "text-align:right"),
            ("p95 ms", "text-align:right"), ("Max ms", "text-align:right")]
    head = "".join(
        f"<td style='padding:9px 6px;font-size:9.5px;letter-spacing:.4px;"
        f"text-transform:uppercase;color:{INK3};font-weight:700;{a}'>{h}</td>"
        for h, a in cols)
    # Fixed column widths keep the numbers aligned no matter how long the URL is.
    colgroup = ("<colgroup><col style='width:37%'>"
                + "<col style='width:9%'>" * 7 + "</colgroup>")
    return (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='border:1px solid {LINE};border-radius:12px;table-layout:fixed'>"
            f"{colgroup}<tr style='background:{TINT}'>{head}</tr>{rows}</table>")


def _verdict(result: PerfResult, profile: LoadProfile):
    """Plain-English pass/fail sentence, comparing the run to the budgets."""
    thr = profile.thresholds or {}
    if not thr:
        return (INK2, LINE2, "This run finished. No pass/fail budgets were set, so it's "
                "reported as-is — set a p95 and error-rate budget to get an automatic gate.")
    fails = []
    if "p95_ms" in thr and result.p95_ms > thr["p95_ms"]:
        fails.append(f"95% of requests took up to {result.p95_ms:.0f} ms — over your "
                     f"{thr['p95_ms']:.0f} ms target")
    if "error_rate" in thr and result.error_rate > thr["error_rate"]:
        fails.append(f"{result.error_rate * 100:.1f}% of requests failed — over your "
                     f"{thr['error_rate'] * 100:.1f}% target")
    if fails:
        return (RED, RED_SOFT, "FAILED because " + "; and ".join(fails) + ".")
    return (GREEN, GREEN_SOFT, "PASSED — response times and error rate stayed within the "
            "targets you set.")


def _pctile_bars(result: PerfResult):
    """Simple horizontal bars for p50/p90/p95/p99 — a quick visual of the spread."""
    vals = [("p50", result.p50_ms, VIOLET), ("p90", result.p90_ms, VIOLET),
            ("p95", result.p95_ms, VIOLET_INK), ("p99", result.p99_ms, VIOLET)]
    mx = max((v for _, v, _ in vals), default=1) or 1
    rows = ""
    for label, v, col in vals:
        pct = max(3, int(round((v / mx) * 100)))
        rows += (
            f"<tr><td width='54' style='font-family:{MONO};font-size:12px;font-weight:700;"
            f"color:{INK2};padding:7px 0'>{label}</td>"
            f"<td style='padding:7px 12px'>"
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='background:{LINE2};border-radius:99px'><tr>"
            f"<td height='10' style='background:{col};border-radius:99px;width:{pct}%;"
            f"font-size:0;line-height:0'>&nbsp;</td>"
            f"<td style='font-size:0;line-height:0'>&nbsp;</td></tr></table></td>"
            f"<td width='84' align='right' style='font-family:{MONO};font-size:13.5px;"
            f"font-weight:700;color:{INK};white-space:nowrap'>{v:.0f} ms</td></tr>")
    return (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0'>"
            f"{rows}</table>")


def _failures_section(result: PerfResult):
    """Grouped failure reasons (status code + message + count) so the reader can
    see WHY requests failed, not just how many. Returns '' when there are none."""
    fails = result.failures or []
    if not fails:
        return ""
    rows = ""
    for fg in fails:
        code = _esc(fg.code or "—")
        msg = _esc(_mask_secrets(fg.message or "(no message)"))
        rows += (
            f"<tr style='border-top:1px solid {LINE2}'>"
            f"<td style='padding:11px 14px;font-size:12.5px;font-weight:600;color:{INK};"
            f"word-break:break-all;overflow-wrap:anywhere'>{_esc(_mask_secrets(fg.label))}</td>"
            f"<td style='padding:11px 8px;text-align:center;font-family:{MONO};"
            f"font-size:12.5px;font-weight:700;color:{RED}'>{code}</td>"
            f"<td style='padding:11px 8px;font-size:12.5px;color:{INK2};"
            f"word-break:break-word;overflow-wrap:anywhere'>{msg}</td>"
            f"<td style='padding:11px 14px;text-align:right;font-family:{MONO};"
            f"font-size:12.5px;font-weight:700;color:{INK}'>{fg.count}</td>"
            f"</tr>")
    cols = [("Request", "text-align:left"), ("Code", "text-align:center"),
            ("Reason", "text-align:left"), ("Count", "text-align:right")]
    head = "".join(
        f"<td style='padding:9px 8px;font-size:9.5px;letter-spacing:.4px;"
        f"text-transform:uppercase;color:{INK3};font-weight:700;{a}'>{h}</td>"
        for h, a in cols)
    colgroup = ("<colgroup><col style='width:34%'><col style='width:10%'>"
                "<col style='width:44%'><col style='width:12%'></colgroup>")
    return (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='border:1px solid {RED};border-radius:12px;table-layout:fixed'>"
            f"{colgroup}<tr style='background:{RED_SOFT}'>{head}</tr>{rows}</table>")


def _glossary():
    """Plain-language explanations so a non-technical reader can read the report."""
    items = [
        ("Percentiles (p50, p90, p95, p99)",
         "How fast requests were. p50 (the median) is a typical user; p95 means 95% of "
         "requests were at least this fast, so it shows your slower users. p95 is the "
         "number teams usually promise in an SLA."),
        ("Throughput",
         "How many requests the app successfully handled every second."),
        ("Error rate",
         "The share of requests that failed (didn't return a success response)."),
        ("Virtual users",
         "Simulated people using the app at the same time — the load applied."),
        ("PASS / FAIL",
         "Whether the run stayed within the p95 and error-rate targets you set."),
    ]
    rows = "".join(
        f"<tr style='border-top:1px solid {LINE2}'>"
        f"<td width='190' style='padding:10px 12px;font-size:12.5px;font-weight:700;"
        f"color:{INK};vertical-align:top'>{_esc(k)}</td>"
        f"<td style='padding:10px 12px;font-size:12.5px;color:{INK2};line-height:1.5'>{_esc(v)}</td>"
        f"</tr>" for k, v in items)
    return (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='border:1px solid {LINE};border-radius:12px'>"
            f"<tr style='background:{TINT}'><td colspan='2' style='padding:10px 12px;"
            f"font-size:9.5px;letter-spacing:.4px;text-transform:uppercase;color:{INK3};"
            f"font-weight:700'>What these numbers mean</td></tr>{rows}</table>")


def render_html(result: PerfResult, profile: Optional[LoadProfile] = None,
                meta: Optional[dict] = None) -> str:
    """Return a complete, brand-consistent HTML document for one run."""
    meta = meta or {}
    profile = profile or LoadProfile()
    gate_txt, gate_fg, gate_bg = _gate(result)
    when = meta.get("generated_at") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    scope = _esc(meta.get("scope") or meta.get("base_url") or "Load test")
    logo = meta.get("logo_html") or ""

    thr = profile.thresholds or {}
    budget_bits = []
    if "p95_ms" in thr:
        budget_bits.append(f"p95 &le; {thr['p95_ms']:.0f} ms")
    if "error_rate" in thr:
        budget_bits.append(f"errors &le; {thr['error_rate'] * 100:.1f}%")
    budget = " &middot; ".join(budget_bits) or "none set"

    source = _esc(meta.get("source") or "")
    src_pill = (f"<span style='font-size:11px;font-weight:700;color:{VIOLET_INK};"
                f"background:{VIOLET_SOFT};padding:4px 10px;border-radius:999px'>"
                f"Source: {source}</span>") if source else ""

    masthead = (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
        + (f"<td width='34' valign='middle' style='padding-right:13px'>{logo}</td>" if logo else "")
        + f"<td valign='middle'>"
        f"<div style='font-size:15px;font-weight:700;color:{INK};letter-spacing:-.2px'>QA Studio</div>"
        f"<div style='font-size:12px;font-weight:700;color:{VIOLET_INK};margin-top:2px'>"
        f"Performance &middot; Report</div></td>"
        f"<td align='right' valign='middle'>"
        f"<span style='font-size:13px;font-weight:800;color:{gate_fg};background:{gate_bg};"
        f"border:1px solid {gate_fg};border-radius:9px;padding:7px 15px'>{gate_txt}</span>"
        f"</td></tr></table>")

    load_line = (
        f"{profile.users} virtual users &middot; ramp {profile.ramp_up_s}s &middot; "
        f"hold {profile.duration_s}s &middot; target {_esc(meta.get('target') or result.target or 'JMeter')} "
        f"&middot; budget: {budget}")

    v_fg, v_bg, v_msg = _verdict(result, profile)
    verdict = (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
               f"style='background:{v_bg};border:1px solid {v_fg};border-radius:12px'><tr>"
               f"<td width='6' style='background:{v_fg}'></td>"
               f"<td style='padding:13px 16px;font-size:13.5px;font-weight:600;color:{INK}'>"
               f"<span style='color:{v_fg};font-weight:800'>This run </span>{v_msg}</td></tr></table>")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QA Studio — Performance Report</title></head>
<body style="margin:0;background:{PAPER};font-family:{UI};color:{INK}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAPER}">
<tr><td align="center" style="padding:28px 16px 48px">
<table role="presentation" width="940" cellpadding="0" cellspacing="0"
       style="max-width:940px;width:100%;background:{CARD};border:1px solid {LINE};border-radius:18px">

  <tr><td style="padding:28px 34px 6px">{masthead}</td></tr>

  <tr><td style="padding:16px 34px 4px">
    <div style="font-size:20px;font-weight:800;color:{INK};letter-spacing:-.3px">{scope}</div>
    <div style="font-size:12px;color:{INK3};margin-top:4px">{_esc(when)}
      {('&nbsp;&nbsp;' + src_pill) if src_pill else ''}</div>
  </td></tr>

  <tr><td style="padding:16px 34px 4px">{verdict}</td></tr>

  <tr><td style="padding:16px 34px 6px">{_kpi_cells(result)}
    <div style="font-size:11.5px;color:{INK3};margin-top:12px">{load_line}</div>
  </td></tr>

  <tr><td style="padding:22px 34px 8px">{_sec_head(VIOLET, 'Response-time spread', 'ms')}</td></tr>
  <tr><td style="padding:10px 34px 4px">{_pctile_bars(result)}</td></tr>

  <tr><td style="padding:22px 34px 8px">{_sec_head(VIOLET, 'Per-request breakdown', len(result.per_request or []))}</td></tr>
  <tr><td style="padding:12px 34px 4px">{_request_rows(result)}</td></tr>

  {(f'<tr><td style="padding:22px 34px 8px">' + _sec_head(RED, 'Why requests failed', sum(f.count for f in result.failures)) + '</td></tr>' + f'<tr><td style="padding:12px 34px 4px">{_failures_section(result)}</td></tr>') if result.failures else ''}

  <tr><td style="padding:22px 34px 8px">{_sec_head(INK3, 'Reading this report', '?')}</td></tr>
  <tr><td style="padding:12px 34px 4px">{_glossary()}</td></tr>

  <tr><td style="padding:24px 34px 32px">
    <div style="border-top:1px solid {LINE};padding-top:16px;font-size:11px;color:{INK3};text-align:center">
      Generated by QA Studio &middot; {_esc(result.samples)} samples &middot;
      {_esc(result.errors)} errors. JMeter's full interactive dashboard is attached separately.
    </div>
  </td></tr>

</table></td></tr></table>
</body></html>
"""


__all__ = ["render_html"]
