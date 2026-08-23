"""Drive the whole pipeline: .sql directory -> Python package."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import psycopg

from .annotations import parse_file
from .describe import Described, Describer
from .emit import module_name, render_module, render_package_init


@dataclass(frozen=True, slots=True)
class GenerationResult:
    modules: tuple[str, ...]
    described: tuple[Described, ...]
    files: tuple[Path, ...]


def generate(*, url: str, queries: Path, out: Path) -> GenerationResult:
    """Describe every query under `queries/` and write a package into `out/`.

    Deterministic: files are visited in sorted order, and nothing that is
    written depends on the DSN, the clock or the environment.
    """
    sources = sorted(queries.glob("*.sql"))
    if not sources:
        raise FileNotFoundError(f"no .sql files under {queries}")

    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    modules: list[str] = []
    everything: list[Described] = []

    with psycopg.connect(url, autocommit=True) as conn:
        describer = Describer(conn)
        for path in sources:
            blocks = parse_file(path.read_text(), source=path.name)
            described = [describer.describe(b) for b in blocks]
            everything.extend(described)
            mod = module_name(path.stem)
            modules.append(mod)
            target = out / f"{mod}.py"
            target.write_text(render_module(described, source=path.name))
            written.append(target)

    init = out / "__init__.py"
    init.write_text(render_package_init(modules))
    written.append(init)

    return GenerationResult(
        modules=tuple(modules), described=tuple(everything), files=tuple(written)
    )
