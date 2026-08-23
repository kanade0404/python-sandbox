"""E2 -- the benchmark harness. Small-N, laptop-grade, relative numbers only.

Imports `m5link` first, for the same reason `app` does: the bench modules
import `altsa_runtime` and both generated packages.
"""

from __future__ import annotations

import m5link

M2_ROOT = m5link.M2_ROOT
M3_ROOT = m5link.M3_ROOT

__all__ = ["M2_ROOT", "M3_ROOT"]
