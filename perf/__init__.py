"""perf - QA Studio performance-testing core (clean-architecture package).

Layers (dependencies point INWARD only):
  models   - domain (frozen dataclasses); depends on nothing.
  ports    - interfaces (PerfTarget, ScenarioExtractor, AiComplete); depends on models.
  extract  - application: test case -> PerfScenario (heuristic + AI-hook).
  targets/ - adapters: normalized IR <-> a load tool (JMeter first).
  registry - the pluggable target set.

The app (main.py / engine.py / a future performance.py screen) wires these:
  - inject engine's AI into AIExtractor,
  - pick a target via registry.get_target(...),
  - emit -> run locally or hand the project to the remote worker,
  - map PerfResult into report.py for export.

Nothing in this package imports Flet or tracker/ - it is unit-testable in
isolation and reusable by the desktop app AND the remote-run worker.

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from __future__ import annotations

from .models import (Assertion, AssertionKind, DataSource, Extraction, LoadProfile,
                     PerfCapability, PerfRequest, PerfResult, PerfScenario, RequestStat)
from .ports import PerfTarget, ProjectPaths, ScenarioExtractor
from .extract import AIExtractor, HeuristicExtractor
from .registry import get_target, target_names

__all__ = [
    "Assertion", "AssertionKind", "DataSource", "Extraction", "LoadProfile",
    "PerfCapability", "PerfRequest", "PerfResult", "PerfScenario", "RequestStat",
    "PerfTarget", "ProjectPaths", "ScenarioExtractor",
    "AIExtractor", "HeuristicExtractor", "get_target", "target_names",
]
