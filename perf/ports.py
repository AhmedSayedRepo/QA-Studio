"""perf/ports.py - the seams (interfaces) the core depends on.

Clean-architecture rule: the domain and application logic depend on THESE
abstractions, never on a concrete tool or the AI provider. Concrete load tools
(perf/targets/*) implement `PerfTarget`; the app's AI engine is injected as the
`AiComplete` callable the extractor needs. Nothing here imports a tool or Flet.

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, List, Optional, Tuple

from .models import DataSource, LoadProfile, PerfCapability, PerfResult, PerfScenario

#: Injected AI seam: given a prompt, return the model's raw text. The app wires
#: this to engine.py; tests pass a stub. Keeps the core provider-agnostic AND
#: metered through the app's existing AI-usage/credit path (the app's wrapper,
#: not this module, does the metering).
AiComplete = Callable[[str], str]

#: Progress/log callback for long emit/run operations (mirrors the app's on_event).
OnEvent = Callable[[dict], None]


def noop_event(_ev: dict) -> None:  # default OnEvent
    return None


class ProjectPaths:
    """Where an emitted tool project landed on disk."""

    def __init__(self, root: str, entry: str, data_csv: str = "", report_dir: str = ""):
        self.root = root          # project folder
        self.entry = entry        # the runnable artifact (e.g. plan.jmx)
        self.data_csv = data_csv  # emitted CSV data file, if any
        self.report_dir = report_dir

    def __repr__(self) -> str:
        return f"ProjectPaths(entry={self.entry!r})"


class PerfTarget(ABC):
    """One load tool == one adapter (same shape as a tracker Backend).

    `emit` translates the normalized IR into the tool's project; `run` executes
    it and maps the tool's output back onto the normalized `PerfResult`. Neither
    the IR nor the core ever sees the tool's native format.
    """

    name: str = ""
    capabilities: frozenset = frozenset()

    @abstractmethod
    def emit(self, scenarios: List[PerfScenario], profile: LoadProfile,
             out_dir: str, data: Optional[DataSource] = None) -> ProjectPaths:
        """IR -> a self-contained tool project in out_dir."""

    @abstractmethod
    def preflight(self) -> Tuple[bool, str]:
        """Is the tool (and its runtime) available? (ok, message-or-hint)."""

    def run(self, project: ProjectPaths, on_event: OnEvent = noop_event) -> PerfResult:
        """Execute the emitted project and return a normalized result.

        Optional: a target may emit-only (design/export) and leave running to
        the remote worker. Default raises so a missing impl is loud, not silent.
        """
        raise NotImplementedError(f"{self.name}: run() is not implemented")

    def supports(self, cap: PerfCapability) -> bool:
        return cap in self.capabilities


class ScenarioExtractor(ABC):
    """Turns a functional test case (primitives) into a PerfScenario. Kept on
    primitives (title + step dicts) so this package never imports tracker/."""

    @abstractmethod
    def extract(self, case_id: str, title: str, steps: List[dict],
                story_id: str = "") -> PerfScenario:
        ...


__all__ = [
    "AiComplete", "OnEvent", "noop_event", "ProjectPaths",
    "PerfTarget", "ScenarioExtractor",
]
