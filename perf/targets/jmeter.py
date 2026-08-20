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
import queue
import re
import signal
import shutil
import subprocess
import threading
import time
from typing import List, Optional, Tuple
from urllib.parse import urlparse
from xml.sax.saxutils import escape

from ..models import (AssertionKind, DataSource, FailureGroup, LoadProfile,
                      PerfCapability, PerfRequest, PerfResult, PerfScenario, RequestStat)
from ..ports import (CancelCheck, OnEvent, PerfRunCancelled, PerfTarget,
                     ProjectPaths, never_cancel, noop_event)

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


def _pacing_action(profile: LoadProfile) -> str:
    """Pause once after each complete thread-group iteration."""
    delay = max(0, int(profile.pacing_ms or 0))
    if not delay:
        return ""
    return (
        '<TestAction guiclass="TestActionGui" testclass="TestAction" '
        'testname="Iteration pacing" enabled="true">'
        '<intProp name="ActionProcessor.action">1</intProp>'
        '<intProp name="ActionProcessor.target">0</intProp>'
        f'<stringProp name="ActionProcessor.duration">{delay}</stringProp>'
        '</TestAction><hashTree/>'
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


def _headermanager(req: PerfRequest) -> str:
    """Replay captured request headers (auth token, content-type, custom X-*).
    Emitted as a sampler child so it applies to just this request. Empty when the
    request carries no headers (e.g. heuristic/AI scenarios)."""
    if not req.headers:
        return ""
    rows = ""
    for k, v in req.headers.items():
        if not k:
            continue
        rows += (
            '<elementProp name="" elementType="Header">'
            f'<stringProp name="Header.name">{escape(k)}</stringProp>'
            f'<stringProp name="Header.value">{_x(v)}</stringProp>'
            '</elementProp>'
        )
    return (
        '<HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" '
        'testname="HTTP Header Manager" enabled="true">'
        f'<collectionProp name="HeaderManager.headers">{rows}</collectionProp>'
        '</HeaderManager><hashTree/>'
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
    failmap: dict = {}          # (label, code, message) -> count
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
                # Capture WHY it failed: an assertion's failureMessage if present,
                # else the HTTP response code + message (e.g. "403 / Forbidden").
                code = (row.get("responseCode", "") or "").strip()
                msg = ((row.get("failureMessage", "") or "").strip()
                       or (row.get("responseMessage", "") or "").strip())
                fkey = (label, code, msg)
                failmap[fkey] = failmap.get(fkey, 0) + 1
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
    failures = [FailureGroup(label=lbl, code=code, message=msg, count=cnt)
                for (lbl, code, msg), cnt in
                sorted(failmap.items(), key=lambda kv: kv[1], reverse=True)][:30]
    return PerfResult(
        scenario_id=scenario_id, target="jmeter", samples=n, errors=errors,
        duration_s=duration_s,
        p50_ms=_pct(sv, 50), p90_ms=_pct(sv, 90), p95_ms=_pct(sv, 95), p99_ms=_pct(sv, 99),
        avg_ms=(sum(sv) / n) if n else 0.0,
        throughput_rps=(n / duration_s) if duration_s else 0.0,
        per_request=per_request, failures=failures, raw_report_dir=report_dir)


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
                children = (_headermanager(req) + _assertion_children(req)
                            + _extractor_children(req) + _timer_children(req))
                samplers += _sampler(req) + "<hashTree>" + children + "</hashTree>"
            tg_children += _transaction(sc.title) + "<hashTree>" + samplers + "</hashTree>"

        csv_block = (_csv_dataset(data) + "<hashTree/>") if data else ""
        tg_children += _pacing_action(profile)
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

    def run(self, project: ProjectPaths, on_event: OnEvent = noop_event,
            remote_hosts: str = "", cancel_check: CancelCheck = never_cancel
            ) -> PerfResult:
        ok, msg = self.preflight()
        if not ok:
            raise RuntimeError(msg)
        # Uniquely-named output per run. JMeter's -e -o REFUSES to write into a
        # non-empty report folder or over an existing .jtl; when the output dir is
        # a OneDrive-synced folder (e.g. Desktop), OneDrive keeps handles open on
        # the previous run's files so a delete can't fully clear them, and JMeter
        # then fails with "folder is not empty". A fresh timestamped name sidesteps
        # that entirely and keeps each run's dashboard instead of overwriting it.
        _prune_old_runs(project.root)          # keep the last few, don't fill the disk
        stamp = time.strftime("%Y%m%d-%H%M%S")
        jtl = os.path.join(project.root, f"results-{stamp}.jtl")
        report = os.path.join(project.root, f"report-{stamp}")
        on_event({"type": "log", "msg": "Running JMeter (non-GUI)..."})
        # On Windows `jmeter` is jmeter.bat - subprocess won't resolve the .bat
        # from the bare name (that's the WinError 2), so use the full resolved path.
        jm = shutil.which("jmeter") or shutil.which("jmeter.bat") or "jmeter"
        cmd = [jm, "-n", "-t", project.entry, "-l", jtl, "-e", "-o", report]
        # Distributed load: hand the plan to remote jmeter-server engines with -R.
        _hosts = ",".join(h.strip() for h in re.split(r"[,\s]+", remote_hosts or "") if h.strip())
        if _hosts:
            cmd += ["-R", _hosts]
            on_event({"type": "log", "msg": f"Distributed run across engines: {_hosts}"})
        # Capture JMeter's live output and STREAM it to on_event so its periodic
        # "summary +/=" lines show up in the app's activity log instead of only a
        # console (run() inheriting the parent stdout is why it was terminal-only).
        flags = 0
        if os.name == "nt":
            flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        proc = subprocess.Popen(cmd, cwd=project.root, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1,
                                creationflags=flags,
                                start_new_session=(os.name != "nt"))
        if cancel_check():
            _terminate_process_tree(proc)
            raise PerfRunCancelled("Performance run was cancelled.")

        # Reading stdout directly can block forever when JMeter is quiet. A
        # reader thread plus a timed queue keeps cancellation responsive.
        output = queue.Queue()
        reader_done = proc.stdout is None

        def _read_output():
            try:
                for raw in proc.stdout:
                    output.put(raw)
            except Exception:
                pass
            finally:
                output.put(None)

        if proc.stdout is not None:
            threading.Thread(target=_read_output, daemon=True).start()
        while proc.poll() is None or not reader_done or not output.empty():
            if cancel_check():
                on_event({"type": "log", "msg": "Stopping JMeter..."})
                _terminate_process_tree(proc)
                raise PerfRunCancelled("Performance run was cancelled.")
            try:
                raw = output.get(timeout=0.1)
            except queue.Empty:
                continue
            if raw is None:
                reader_done = True
                continue
            line = (raw or "").rstrip()
            if line:
                on_event({"type": "log", "msg": line})
        proc.wait()
        if not os.path.exists(jtl):
            raise RuntimeError("JMeter produced no results.jtl - see console output.")
        return parse_jtl(jtl, report_dir=report)


def _terminate_process_tree(proc) -> None:
    """Terminate only the process tree created for this JMeter run."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=3)
    except Exception:
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass


def _prune_old_runs(root: str, keep: int = 5) -> None:
    """Keep only the newest `keep` report-*/results-* artifacts in the output
    folder so repeated runs don't fill the disk. Best-effort; never raises."""
    try:
        for prefix, is_dir in (("report-", True), ("results-", False)):
            items = [os.path.join(root, n) for n in os.listdir(root)
                     if n.startswith(prefix)
                     and (os.path.isdir(os.path.join(root, n)) == is_dir)]
            items.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for old in items[keep:]:
                try:
                    shutil.rmtree(old, ignore_errors=True) if is_dir else os.remove(old)
                except Exception:
                    pass
    except Exception:
        pass


def apply_thresholds(result: PerfResult, profile: LoadProfile) -> PerfResult:
    """Return a copy of `result` with threshold_pass evaluated (caller-side, so
    emit/run stay profile-free and the CI gate is explicit)."""
    from dataclasses import replace
    return replace(result, threshold_pass=profile.passed(result))


__all__ = ["JMeterTarget", "parse_jtl", "apply_thresholds"]
