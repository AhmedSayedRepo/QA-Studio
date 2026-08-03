"""perf.targets - concrete load-tool adapters (one module per tool).

Copyright (c) 2026 Ahmed Sayed. All rights reserved. Proprietary - see LICENSE.
"""
from .jmeter import JMeterTarget

__all__ = ["JMeterTarget"]
