"""perf/registry.py - the pluggable set of load-tool targets.

Adding a tool (k6, Locust, Gatling, a cloud service) means adding a PerfTarget
and one line here - no core change (same shape as the tracker backend registry).

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from __future__ import annotations

from typing import Dict, List

from .ports import PerfTarget
from .targets.jmeter import JMeterTarget

#: name -> factory. JMeter ships first; others plug in additively.
_TARGETS = {
    "jmeter": JMeterTarget,
    # "k6": K6Target,        # future
    # "locust": LocustTarget,
}


def target_names() -> List[str]:
    return list(_TARGETS.keys())


def get_target(name: str) -> PerfTarget:
    try:
        return _TARGETS[name]()
    except KeyError:
        raise ValueError(f"Unknown perf target {name!r}. Available: {target_names()}")


__all__ = ["target_names", "get_target"]
