"""The M5 order service -- one application, both layers.

Layer A (M2): the composable, typed READ side. `generated_a.facade` executes
through `altsa_runtime`, which executes through SQLAlchemy Core.

Layer B (M3): the fixed-shape WRITE side. `generated_b.*` are typed functions
over hand-written SQL, executed directly on a psycopg connection.

Importing this package puts M2 and M3 on `sys.path` (see `m5link`) BEFORE
anything imports `generated_a.facade`, which needs `altsa_runtime`.
"""

from __future__ import annotations

import m5link

#: Re-exported so the import above is unmistakably load-bearing: importing
#: `m5link` is what puts M2 and M3 on `sys.path`, and it has to happen before
#: anything imports `generated_a.facade` (which needs `altsa_runtime`) or
#: `app.seam` (which needs it too).
M2_ROOT = m5link.M2_ROOT
M3_ROOT = m5link.M3_ROOT

__all__ = ["M2_ROOT", "M3_ROOT"]
