"""Local CSV/XLSX test-case source for Automation.

The Automation generator consumes a provider-neutral ``stories_payload``
structure.  This module turns a simple, reviewable spreadsheet into that
structure without importing it into Azure DevOps or writing back to the file.
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
from collections import OrderedDict


SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}


def _text(value):
    return str(value or "").strip()


def _header(value):
    return re.sub(r"[^a-z0-9]+", " ", _text(value).lower()).strip()


def _field(row, *names):
    for name in names:
        value = row.get(name)
        if value:
            return _text(value)
    return ""


def _rows_from_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError("The CSV needs a header row.")
        return [{_header(k): _text(v) for k, v in row.items() if k is not None}
                for row in reader]


# Sheets that hold test DATA (one row per run of a data-driven case), not steps.
_DATA_SHEETS = {"data", "test data", "testdata", "data sets", "datasets", "data set"}


def _sheet_rows(sheet):
    values = sheet.iter_rows(values_only=True)
    header = next(values, None)
    if not header or not any(_text(v) for v in header):
        return None
    keys = [_header(v) for v in header]
    return [{keys[i]: _text(value) for i, value in enumerate(row)
             if i < len(keys) and keys[i]}
            for row in values]


def _data_rows_from_xlsx(path):
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover
        return []
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in book.worksheets:
            if _header(sheet.title) in _DATA_SHEETS:
                return _sheet_rows(sheet) or []
    finally:
        book.close()
    return []


def data_source_name(path):
    """The data file/sheet a test-case file draws its data rows from, or ""."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xlsx":
        try:
            from openpyxl import load_workbook
            book = load_workbook(path, read_only=True, data_only=True)
            try:
                for sheet in book.worksheets:
                    if _header(sheet.title) in _DATA_SHEETS:
                        return "%s (sheet '%s')" % (os.path.basename(path), sheet.title)
            finally:
                book.close()
        except Exception:
            return ""
        return ""
    stem = os.path.splitext(path)[0]
    for suffix in ("-data.csv", ".data.csv", "_data.csv"):
        if os.path.isfile(stem + suffix):
            return os.path.basename(stem + suffix)
    return ""


def _data_rows_from_companion_csv(path):
    """'cases.csv' -> 'cases-data.csv' (or cases.data.csv / cases_data.csv)."""
    stem = os.path.splitext(path)[0]
    for suffix in ("-data.csv", ".data.csv", "_data.csv"):
        candidate = stem + suffix
        if os.path.isfile(candidate):
            return _rows_from_csv(candidate)
    return []


_DATA_KEY_COLUMNS = ("test case id", "testcase id", "case id", "id",
                     "test case title", "testcase title", "case title", "title")
_DATA_NAME_COLUMNS = ("data set", "dataset", "data name", "row name", "name", "iteration")


def _attach_data(cases, rows):
    """Give each case its data rows: {"name": ..., "values": {column: value}}.
    A row names its case by Test Case ID or Title; a row with neither applies
    to every case that uses {{placeholders}}."""
    for position, row in enumerate(rows, start=1):
        if not any(row.values()):
            continue
        key = _field(row, "test case id", "testcase id", "case id", "id")
        title = _field(row, "test case title", "testcase title", "case title", "title")
        name = _field(row, *_DATA_NAME_COLUMNS)
        values = {k: v for k, v in row.items()
                  if k and k not in _DATA_KEY_COLUMNS and k not in _DATA_NAME_COLUMNS and v != ""}
        if not values:
            continue
        for case in cases:
            uses_tokens = any("{{" in (st.get("action", "") + st.get("expected", ""))
                              for st in case["steps"])
            if (key and key == case.get("source_id")) or \
                    (title and title.lower() == case["title"].lower()) or \
                    (not key and not title and uses_tokens):
                rows_for = case.setdefault("data", [])
                rows_for.append({"name": name or "Row %d" % (len(rows_for) + 1),
                                 "values": dict(values)})


def _rows_from_xlsx(path):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - bundled by requirements
        raise ValueError("Excel import is unavailable because openpyxl is missing.") from exc
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in book.worksheets:
            if _header(sheet.title) in _DATA_SHEETS:
                continue
            values = sheet.iter_rows(values_only=True)
            header = next(values, None)
            if not header or not any(_text(v) for v in header):
                continue
            keys = [_header(v) for v in header]
            return [{keys[i]: _text(value) for i, value in enumerate(row)
                     if i < len(keys) and keys[i]}
                    for row in values]
    finally:
        book.close()
    raise ValueError("The workbook needs a worksheet with a header row.")


def load_test_cases(path):
    """Read ordered test cases from a CSV/XLSX file.

    Required columns: ``Test Case Title`` and ``Action``.  ``Expected Result``
    is optional.  ``Test Case ID`` and ``Step`` are optional; when absent, the
    title and spreadsheet row order keep steps grouped and ordered.
    """
    path = os.path.abspath(_text(path))
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError("Choose a CSV or XLSX test-case file.")
    if not os.path.isfile(path):
        raise ValueError("The selected test-case file is no longer available.")

    rows = _rows_from_csv(path) if ext == ".csv" else _rows_from_xlsx(path)
    grouped = OrderedDict()
    for position, row in enumerate(rows, start=1):
        title = _field(row, "test case title", "testcase title", "case title", "title")
        action = _field(row, "action", "step action", "test step", "steps")
        expected = _field(row, "expected result", "expected", "expected outcome", "result")
        case_id = _field(row, "test case id", "testcase id", "case id", "id")
        # Optional execution metadata lets a reviewed local workbook express
        # browser context operations (frame/window/dialog) without trying to
        # smuggle them through natural-language action text.  Azure DevOps
        # payloads simply omit these fields and retain their existing flow.
        metadata = {
            "element_type": _field(row, "element type", "control type"),
            "preferred_locator": _field(row, "preferred locator", "locator", "selector"),
            "frame_context": _field(row, "frame context", "frame"),
            "automation_note": _field(row, "automation note", "note"),
        }
        if not any((title, action, expected, case_id)):
            continue
        if not title:
            raise ValueError(f"Row {position}: Test Case Title is required.")
        if not action:
            raise ValueError(f"Row {position}: Action is required.")
        key = case_id or title
        case = grouped.setdefault(key, {"id": case_id or f"row-{position}",
                                        "source_id": case_id,
                                        "title": title, "steps": []})
        step = {"index": len(case["steps"]) + 1,
                "action": action, "expected": expected}
        step.update({key: value for key, value in metadata.items() if value})
        case["steps"].append(step)
    if not grouped:
        raise ValueError("No test-case steps were found in the selected file.")
    cases = list(grouped.values())
    # Data-driven cases: a "Data" sheet (Excel) or "<file>-data.csv" (CSV).
    data_rows = _data_rows_from_xlsx(path) if ext == ".xlsx" else _data_rows_from_companion_csv(path)
    if data_rows:
        _attach_data(cases, data_rows)
    for case in cases:
        case.pop("source_id", None)
    return cases


def load_stories_payload(path):
    """Create one stable local source story containing all imported cases."""
    cases = load_test_cases(path)
    path = os.path.abspath(path)
    fingerprint = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
    name = os.path.basename(path)
    for seq, case in enumerate(cases, start=1):
        # Avoid collisions with Azure work-item IDs while remaining stable across
        # repeated runs of the same file.
        case["id"] = f"file-{fingerprint}-{case['id']}-{seq}"
    return [{
        "story": {"id": f"file-{fingerprint}", "title": f"Imported file: {name}",
                  "criteria": "Test cases imported from a local file."},
        "test_cases": cases,
        # Shown in the run log: which data file/sheet the data rows came from.
        "data_source": data_source_name(path),
    }]
