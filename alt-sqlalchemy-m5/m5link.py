"""Import M2's and M3's packages without modifying a byte of either.

M2 and M3 are audited, read-only milestones. M5 has to run *their* generators
and import *their* runtime, and copying either tree would let three copies
drift. So this module puts both roots on `sys.path` and imports them in place,
exactly the way M4's `harness/m3link.py` did with M3's corpus.

What is borrowed, and from where:

    alt-sqlalchemy-m2/altsa_runtime/   Layer A runtime (Conn, Expr/Pred, the
                                       shape memo, the SQLAlchemy backend)
    alt-sqlalchemy-m2/altsa_gen/       Layer A generator (facade.py emitter)
    alt-sqlalchemy-m3/altsa_sqlgen/    Layer B generator (.sql -> typed fns)

Nothing is copied. `generated_a/facade.py` and `generated_b/*.py` in this
directory are *output* of those generators, produced fresh against the M5
database; they are the only generated code M5 owns.

`sys.dont_write_bytecode` is set before either import so that importing M2/M3
does not drop `__pycache__` entries into their trees.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True

M5_ROOT = Path(__file__).resolve().parent
REPO_ROOT = M5_ROOT.parent
M2_ROOT = REPO_ROOT / "alt-sqlalchemy-m2"
M3_ROOT = REPO_ROOT / "alt-sqlalchemy-m3"

for _root, _probe in ((M2_ROOT, "altsa_runtime/conn.py"), (M3_ROOT, "altsa_sqlgen/generate.py")):
    if not (_root / _probe).exists():  # pragma: no cover - setup guard
        raise SystemExit(f"cannot find {_probe} under {_root}")

for _root in (M5_ROOT, M2_ROOT, M3_ROOT):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

__all__ = ["M2_ROOT", "M3_ROOT", "M5_ROOT", "REPO_ROOT"]
