"""perf/targets/jmeter.py - the JMeter PerfTarget adapter.

Translates the normalized IR (perf.models) into a runnable Apache JMeter 5 test
plan (.jmx) + optional CSV Data Set + a run script, runs it non-GUI, and parses
the .jtl results back into a normalized PerfResult. JMeter's XML never leaks past
this file - the same "adapters translate, core stays clean" rule the trackers use.

`emit` and `parse_jtl` are pure/offline and unit-tested. `run` shells out to a
real `jmeter` (validated via `preflight`) and is exercised against a live install.

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
from typing import List, Optional, Tuple
from urllib.parse import urlparse
from xml.sax.saxutils import escape

from ..models import (AssertionKind, DataSource, LoadProfile, PerfCapability,
                      PerfRequest, PerfResult, PerfScenario, RequestStat)
from ..ports import OnEvent, PerfTarget, ProjectPaths, noop_event

_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _to_jm(s: str) -> str:
    """IR placeholder {{var}} -> JMeter ${var}."""
    return _VAR_RE.sub(r"${\1}", s or "")


def _x(s: str) -> str:
    return escape(_to_jm(s or ""))


def _split_url(url: str):
    """(protocol, domain, port, path). Relative paths default to https + ${host}."""
    u = _to_jm(url or "")
    if u.startswith(("http://", "https://")):
        p = urlparse(u)
        return (p.scheme or "https", p.hostname or "${host}",
                str(p.port or ""), (p.path or "/") + (("?" + p.query) if p.query else ""))
    return ("https", "${host}", "", u if u.startswith("/") else "/" + u)


# ---- JMeter element builders (return XML fragments) --------------------------

def _testplan(title: str) -> str:
    return (
        f'<TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="{escape(title)}" enabled="true">'
        '<boolProp name="TestPlan.functional_mode">false</boolProp>'
        '<boolProp name="TestPlan.serialize_threadgroups">false</boolProp>'
        '<elementProp name="TestPlan.user_defined_variables" elementType="Arguments" '
        'guiclass="ArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">'
        '<collectionProp name="Arguments.arguments"/></elementProp>'
        '<stringProp name="TestPlan.user_define_classpath"></stringProp>'
        '</TestPlan>'
    )


def _threadgroup(profile: LoadProfile) -> str:
    return (
        '<ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Load" enabled="true">'
        '<stringProp name="ThreadGroup.on_sample_error">continue</stringProp>'
        '<elementProp name="ThreadGroup.main_controller" elementType="LoopController" '
        'guiclass="LoopControlPanel" testclass="LoopController" testname="Loop Controller" enabled="true">'
        '<boolProp name="LoopController.continue_forever">false</boolProp>'
        '<stringProp name="LoopController.loops">-1</stringProp></elementProp>'
        f'<stringProp name="ThreadGroup.num_threads">{int(profile.users)}</stringProp>'
        f'<stringProp name="ThreadGroup.ramp_time">{int(profile.ramp_up_s)}</stringProp>'
        '<boolProp name="ThreadGroup.scheduler">true</boolProp>'
        f'<stringProp name="ThreadGroup.duration">{int(profile.duration_s)}</stringProp>'
        '<stringProp name="ThreadGroup.delay"></stringProp>'
        '</ThreadGroup>'
    )


def _csv_dataset(data: DataSource) -> str:
    cols = ",".join(c for c in data.columns if c not in set(data.sensitive_columns))
    return (
        '<CSVDataSet guiclass="TestBeanGUI" testclass="CSVDataSet" testname="CSV Data" enabled="true">'
        '<stringProp name="filename">data.csv</stringProp>'
        '<stringProp name="fileEncoding">UTF-8</stringProp>'
        f'<stringProp name="variableNames">{escape(cols)}</stringProp>'
        '<boolProp name="ignoreFirstLine">true</boolProp>'
        '<stringProp name="delimiter">,</stringProp>'
        '<boolProp name="quotedData">true</boolProp>'
        f'<boolProp name="recycle">{"true" if data.recycle else "false"}</boolProp>'
        '<boolProp name="stopThread">false</boolProp>'
        f'<stringProp name="shareMode">shareMode.{"all" if data.share_mode == "all" else "thread"}</stringProp>'
        '</CSVDataSet>'
    )


def _transaction(name: str) -> str:
    return (
        f'<TransactionController guiclass="TransactionControllerGui" testclass="TransactionController" '
        f'testname="{escape(name)}" enabled="true">'
        '<boolProp name="TransactionController.parent">false</boolProp>'
        '<boolProp name="TransactionController.includeTimers">false</boolProp>'
        '</TransactionController>'
    )


def _sampler(req: PerfRequest) -> str:
    proto, domain, port, path = _split_url(req.url)
    name = f"{req.method} {path}"
    body_arg = ""
    raw = "false"
    if req.body:
        raw = "true"
        body_arg = (
            '<elementProp name="" elementType="HTTPArgument">'
            '<boolProp name="HTTPArgument.always_encode">false</boolProp>'
            f'<stringProp name="Argument.value">{_x(req.body)}</stringProp>'
            '<stringProp name="Argument.metadata">=</stringProp></elementProp>'
        )
    return (
        f'<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" '
        f'testname="{escape(name)}" enabled="true">'
        '<elementProp name="HTTPsampler.Arguments" elementType="Arguments" '
        'guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">'
        f'<collectionProp name="Arguments.arguments">{body_arg}</collectionProp></elementProp>'
        f'<stringProp name="HTTPSampler.domain">{escape(domain)}</stringProp>'
        f'<stringProp name="HTTPSampler.port">{escape(port)}</stringProp>'
        f'<stringProp name="HTTPSampler.protocol">{escape(proto)}</stringProp>'
        f'<stringProp name="HTTPSampler.path">{escape(path)}</stringProp>'
        f'<stringProp name="HTTPSampler.method">{escape(req.method)}</stringProp>'
        f'<boolProp name="HTTPSampler.postBodyRaw">{raw}</boolProp>'
        '<boolProp name="HTTPSampler.follow_redirects">true</boolProp>'
        '<boolProp name="HTTPSampler.use_keepalive">true</boolProp>'
        '</HTTPSamplerProxy>'
    )


def _assertion_children(req: PerfRequest) -> str:
    out = ""
    for a in req.assertions:
        if a.kind == AssertionKind.STATUS:
            out += (
                '<ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" '
                'testname="Status" enabled="true"><collectionProp name="Asserion.test_strings">'
                f'<stringProp name="0">{escape(a.value)}</stringProp></collectionProp>'
                '<stringProp name="Assertion.test_field">Assertion.response_code</stringProp>'
                '<boolProp name="Assertion.assume_success">false</boolProp>'
                '<intProp name="Assertion.test_type">8</intProp></ResponseAssertion><hashTree/>'
            )
        elif a.kind == AssertionKind.BODY_CONTAINS:
            out += (
                '<ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" '
                'testname="Body contains" enabled="true"><collectionProp name="Asserion.test_strings">'
                f'<stringProp name="0">{escape(a.value)}</stringProp></collectionProp>'
                '<stringProp name="Assertion.test_field">Assertion.response_data</stringProp>'
                '<boolProp name="Assertion.assume_success">false</boolProp>'
                '<intProp name="Assertion.test_type">16</intProp></ResponseAssertion><hashTree/>'
            )
        elif a.kind == AssertionKind.MAX_LATENCY:
            out += (
                '<DurationAssertion guiclass="DurationAssertionGui" testclass="DurationAssertion" '
                'testname="Max latency" enabled="true">'
                f'<stringProp name="DurationAssertion.duration">{escape(a.value)}</stringProp>'
                '</DurationAssertion><hashTree/>'
            )
    return out


def _extractor_children(req: PerfRequest) -> str:
    out = ""
    for ex in req.extractions:
        if ex.json_path:
            out += (
                '<JSONPostProcessor guiclass="JSONPostProcessorGui" testclass="JSONPostProcessor" '
                f'testname="Extract {escape(ex.var)}" enabled="true">'
                f'<stringProp name="JSONPostProcessor.referenceNames">{escape(ex.var)}</stringProp>'
                f'<stringProp name="JSONPostProcessor.jsonPathExprs">{escape(ex.json_path)}</stringProp>'
                '<stringProp name="JSONPostProcessor.match_numbers">1</stringProp>'
                '</JSONPostProcessor><hashTree/>'
            )
        elif ex.regex:
            out += (
                '<RegexExtractor guiclass="RegexExtractorGui" testclass="RegexExtractor" '
                f'testname="Extract {escape(ex.var)}" enabled="true">'
                '<stringProp name="RegexExtractor.useHeaders">false</stringProp>'
                f'<stringProp name="RegexExtractor.refname">{escape(ex.var)}</stringProp>'
                f'<stringProp name="RegexExtractor.regex">{escape(ex.regex)}</stringProp>'
                '<stringProp name="RegexExtractor.template">$1$</stringProp>'
                '<stringProp name="RegexExtractor.match_number">1</stringProp>'
                '</RegexExtractor><hashTree/>'
            )
    return out


def _timer_children(req: PerfRequest) -> str:
    if req.think_ms and req.think_ms > 0:
        return (
            '<ConstantTimer guiclass="ConstantTimerGui" testclass="ConstantTimer" '
            'testname="Think" enabled="true">'
            f'<stringProp name="ConstantTimer.delay">{int(req.think_ms)}</stringProp>'
            '</ConstantTimer><hashTree/>'
        )
    return ""


# ---- percentile / jtl parsing ------------------------------------------------

def _pct(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = max(0, math.ceil(p / 100.0 * len(sorted_vals)) - 1)
    return float(sorted_vals[min(k, len(sorted_vals) - 1)])


def parse_jtl(jtl_path: str, scenario_id: str = "", report_dir: str = "") -> PerfResult:
    """Parse JMeter's default CSV .jtl into a normalized PerfResult. Pure + unit-tested.

    Assumes CSV output (JMeter's default for `-l`); an XML .jtl raises a clear error."""
    import csv
    with open(jtl_path, "r", encoding="utf-8", newline="") as _peek:
        if _peek.readline().lstrip().startswith("<"):
            raise RuntimeError(
                "Results look like XML; QA Studio expects JMeter's default CSV .jtl "
                "(jmeter.save.saveservice.output_format=csv).")
    elapsed: List[float] = []
    errors = 0
    ts_min = None
    ts_max = None
    per: dict = {}
    with open(jtl_path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                e = float(row.get("elapsed", 0) or 0)
            except ValueError:
                continue
            ok = str(row.get("success", "true")).strip().lower() == "true"
            label = row.get("label", "") or ""
            try:
                ts = float(row.get("timeStamp", 0) or 0)
                ts_min = ts if ts_min is None else min(ts_min, ts)
                ts_max = ts if ts_max is None else max(ts_max, ts)
            except ValueError:
                pass
            elapsed.append(e)
            if not ok:
                errors += 1
            b = per.setdefault(label, {"e": [], "err": 0})
            b["e"].append(e)
            if not ok:
                b["err"] += 1
    n = len(elapsed)
    sv = sorted(elapsed)
    duration_s = ((ts_max - ts_min) / 1000.0) if (ts_min is not None and ts_max is not None and ts_max > ts_min) else 0.0
    per_request = []
    for label, b in sorted(per.items()):
        bs = sorted(b["e"])
        per_request.append(RequestStat(
            label=label, samples=len(bs), errors=b["err"],
            avg_ms=(sum(bs) / len(bs)) if bs else 0.0, p95_ms=_pct(bs, 95),
            min_ms=(bs[0] if bs else 0.0), max_ms=(bs[-1] if bs else 0.0)))
    return PerfResult(
        scenario_id=scenario_id, target="jmeter", samples=n, errors=errors,
        duration_s=duration_s,
        p50_ms=_pct(sv, 50), p90_ms=_pct(sv, 90), p95_ms=_pct(sv, 95), p99_ms=_pct(sv, 99),
        avg_ms=(sum(sv) / n) if n else 0.0,
        throughput_rps=(n / duration_s) if duration_s else 0.0,
        per_request=per_request, raw_report_dir=report_dir)


# ---- the target -------------------------------------------------------------

def _write_filtered_csv(src: str, dst: str, sensitive_columns: List[str]) -> None:
    """Copy `src` CSV to `dst` with any `sensitive_columns` (by header name)
    stripped out - so passwords/tokens are NEVER written into the emitted
    project. Those values are supplied at run time from store.py / the worker
    credential path instead."""
    import csv
    sens = set(sensitive_columns or [])
    with open(src, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        open(dst, "w", encoding="utf-8").close()
        return
    header = rows[0]
    keep = [i for i, h in enumerate(header) if h not in sens]
    with open(dst, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        for row in rows:
            w.writerow([row[i] for i in keep if i < len(row)])


class JMeterTarget(PerfTarget):
    name = "jmeter"
    capabilities = frozenset({
        PerfCapability.THRESHOLDS, PerfCapability.CSV_DATA, PerfCapability.DISTRIBUTED,
    })

    def emit(self, scenarios: List[PerfScenario], profile: LoadProfile,
             out_dir: str, data: Optional[DataSource] = None) -> ProjectPaths:
        os.makedirs(out_dir, exist_ok=True)
        tg_children = ""
        for sc in scenarios:
            samplers = ""
            for req in sc.requests:
                if req.is_wait:
                    continue
                children = _assertion_children(req) + _extractor_children(req) + _timer_children(req)
                samplers += _sampler(req) + "<hashTree>" + children + "</hashTree>"
            tg_children += _transaction(sc.title) + "<hashTree>" + samplers + "</hashTree>"

        csv_block = (_csv_dataset(data) + "<hashTree/>") if data else ""
        tg_block = _threadgroup(profile) + "<hashTree>" + tg_children + "</hashTree>"
        plan_block = _testplan("QA Studio Performance") + "<hashTree>" + csv_block + tg_block + "</hashTree>"
        doc = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">\n<hashTree>'
            + plan_block + '</hashTree>\n</jmeterTestPlan>\n'
        )
        entry = os.path.join(out_dir, "plan.jmx")
        with open(entry, "w", encoding="utf-8") as f:
            f.write(doc)

        data_csv = ""
        if data and data.csv_path and os.path.exists(data.csv_path):
            data_csv = os.path.join(out_dir, "data.csv")
            _write_filtered_csv(data.csv_path, data_csv, data.sensitive_columns)

        # run scripts (Windows + POSIX)
        with open(os.path.join(out_dir, "run.bat"), "w", encoding="ascii", newline="\r\n") as f:
            f.write("@echo off\r\njmeter -n -t plan.jmx -l results.jtl -e -o report\r\n")
        with open(os.path.join(out_dir, "run.sh"), "w", encoding="utf-8", newline="\n") as f:
            f.write("#!/bin/sh\njmeter -n -t plan.jmx -l results.jtl -e -o report\n")
        return ProjectPaths(root=out_dir, entry=entry, data_csv=data_csv,
                            report_dir=os.path.join(out_dir, "report"))

    def preflight(self) -> Tuple[bool, str]:
        jm = shutil.which("jmeter") or shutil.which("jmeter.bat")
        if not jm:
            return (False, "Apache JMeter was not found on PATH. Install it "
                           "(https://jmeter.apache.org) and ensure `jmeter` is runnable.")
        if not shutil.which("java"):
            return (False, "A Java runtime (JRE) was not found; JMeter needs Java. "
                           "Install a JRE 8+ and re-check.")
        return (True, "JMeter and Java found.")

    def run(self, project: ProjectPaths, on_event: OnEvent = noop_event) -> PerfResult:
        ok, msg = self.preflight()
        if not ok:
            raise RuntimeError(msg)
        jtl = os.path.join(project.root, "results.jtl")
        report = project.report_dir or os.path.join(project.root, "report")
        if os.path.exists(jtl):
            os.remove(jtl)          # JMeter refuses to overwrite an existing .jtl
        if os.path.isdir(report):
            shutil.rmtree(report, ignore_errors=True)
        on_event({"type": "log", "msg": "Running JMeter (non-GUI)..."})
        cmd = ["jmeter", "-n", "-t", project.entry, "-l", jtl, "-e", "-o", report]
        subprocess.run(cmd, cwd=project.root, check=False)
        if not os.path.exists(jtl):
            raise RuntimeError("JMeter produced no results.jtl - see console output.")
        return parse_jtl(jtl, report_dir=report)


def apply_thresholds(result: PerfResult, profile: LoadProfile) -> PerfResult:
    """Return a copy of `result` with threshold_pass evaluated (caller-side, so
    emit/run stay profile-free and the CI gate is explicit)."""
    from dataclasses import replace
    return replace(result, threshold_pass=profile.passed(result))


__all__ = ["JMeterTarget", "parse_jtl", "apply_thresholds"]
