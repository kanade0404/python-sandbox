"""E1's other type claim: no `Any` in the APPLICATION's own signatures.

    uv run python proofs/no_any.py

pyright --strict proves the app is internally consistent. It does not prove the
app has not quietly widened something to `Any` -- `Any` is perfectly strict.
So this walks every public callable and dataclass field in `app/` at runtime,
resolves its annotations, and fails if `Any` appears anywhere in them.

It also reports the FOREIGN types that do appear. Four are expected:

    altsa_runtime.conn.Conn                     Layer A's connection  } the
    psycopg.Connection[tuple[object, ...]]      Layer B's connection  } seam
    altsa_runtime.expr.Pred                     Layer A's predicate  } the query
    altsa_runtime.expr.Expr                     Layer A's expression } language

The last two are not leaks: M2's generated facade re-exports `Pred`/`Expr` in
its own `__all__` precisely so an application has one import, and a composable
filter API cannot have a return type that is not `Pred`. They are the query
language, the same way `str` is.

Anything OUTSIDE those four is a leak -- a SQLAlchemy object, a psycopg cursor,
a generated internal -- and the run says so rather than silently passing. The
one that matters is SQLAlchemy: nothing in `app/` may name a SQLAlchemy type,
which is the property M2 exists to provide and M5 is the first code to consume
it as an application rather than a proof.
"""

from __future__ import annotations

import dataclasses
import inspect
import sys
import types
import typing
from pathlib import Path
from typing import Any, get_args, get_origin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import commands, compat, domain, queries, seam  # noqa: E402

MODULES = [
    ("app.domain", domain),
    ("app.seam", seam),
    ("app.compat", compat),
    ("app.commands", commands),
    ("app.queries", queries),
]

#: The connection types the seam is ALLOWED to expose, plus the two expression
#: types M2's facade re-exports as part of its own public surface.
ALLOWED_FOREIGN = {
    "altsa_runtime.conn.Conn",
    "psycopg.Connection",
    "altsa_runtime.expr.Pred",
    "altsa_runtime.expr.Expr",
}

_problems: list[str] = []
_foreign: dict[str, list[str]] = {}
_signatures = 0


def qualname(t: object) -> str:
    mod = getattr(t, "__module__", None)
    name = getattr(t, "__qualname__", None) or getattr(t, "__name__", None)
    if mod is None or name is None:
        return repr(t)
    return f"{mod}.{name}"


def walk(t: object, where: str) -> None:
    """Recurse through a resolved annotation, flagging `Any` and foreign types."""
    if t is Any:
        _problems.append(f"{where}: Any")
        return
    if t is None or t is type(None):
        return
    origin = get_origin(t)
    if origin is not None:
        args = get_args(t)
        if origin is not types.UnionType and origin is not typing.Union:
            walk(origin, where)
        for a in args:
            walk(a, where)
        return
    if isinstance(t, type):
        name = qualname(t)
        root = name.split("[")[0]
        top = root.split(".")[0]
        if top not in {"builtins", "app", "generated_a", "generated_b",
                       "decimal", "datetime", "uuid", "enum", "typing",
                       "collections", "types"}:
            _foreign.setdefault(root, []).append(where)
            if root not in ALLOWED_FOREIGN:
                _problems.append(f"{where}: foreign type {root}")
        return
    if isinstance(t, typing.TypeVar):
        return
    # Literal members, ParamSpec, etc.
    return


def check_callable(modname: str, name: str, fn: object) -> None:
    global _signatures
    try:
        hints = typing.get_type_hints(fn)
    except Exception as exc:  # pragma: no cover - a resolution failure is a bug
        _problems.append(f"{modname}.{name}: cannot resolve annotations ({exc})")
        return
    sig = inspect.signature(fn)  # pyright: ignore[reportArgumentType]
    _signatures += 1
    if "return" not in hints and sig.return_annotation is inspect.Signature.empty:
        _problems.append(f"{modname}.{name}: no return annotation")
    for pname, hint in hints.items():
        walk(hint, f"{modname}.{name}.{pname}")


def main() -> int:
    for modname, mod in MODULES:
        for name, obj in vars(mod).items():
            if name.startswith("_"):
                continue
            if getattr(obj, "__module__", None) != mod.__name__:
                continue  # imported, not defined here
            if inspect.isfunction(obj):
                check_callable(modname, name, obj)
            elif inspect.isclass(obj):
                if dataclasses.is_dataclass(obj):
                    try:
                        hints = typing.get_type_hints(obj)
                    except Exception as exc:  # pragma: no cover
                        _problems.append(f"{modname}.{name}: {exc}")
                        continue
                    for fname, hint in hints.items():
                        walk(hint, f"{modname}.{name}.{fname}")
                for mname, member in vars(obj).items():
                    if mname.startswith("_") or not inspect.isfunction(member):
                        continue
                    check_callable(modname, f"{name}.{mname}", member)
                for mname, member in vars(obj).items():
                    if mname.startswith("_") or not isinstance(member, property):
                        continue
                    fget = member.fget
                    if fget is not None:
                        check_callable(modname, f"{name}.{mname}", fget)

    print(f"checked {_signatures} app-level signature(s) across {len(MODULES)} modules")
    print()
    print("foreign types reachable from an app signature:")
    for root in sorted(_foreign):
        verdict = "ALLOWED (the seam)" if root in ALLOWED_FOREIGN else "LEAK"
        sites = sorted(set(_foreign[root]))
        print(f"  {root:<40} {verdict}")
        for s in sites[:4]:
            print(f"      {s}")
        if len(sites) > 4:
            print(f"      ... and {len(sites) - 4} more")
    print()
    if _problems:
        print(f"{len(_problems)} problem(s):")
        for p in _problems:
            print(f"  {p}")
        print("FAIL")
        return 1
    print("0 `Any`, 0 unexpected foreign types")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
