"""The M4 Rust engine, wrapped in M3's `Engine` protocol.

`analyze(conn, case)` takes a connection because M3's protocol does -- and
ignores it completely. That is the entire Phase 1 -> Phase 2 delta in one
signature: the argument the static analyser does not need.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from m3link import M4_ROOT, m3_engines

InferredColumn = m3_engines.InferredColumn

DEFAULT_BINARY = M4_ROOT / "target" / "release" / "altsa-analyze"


class AnalyzeError(RuntimeError):
    pass


@dataclass
class RustEngine:
    """Shells out to `altsa-analyze`. No database, ever."""

    binary: Path = DEFAULT_BINARY
    name: str = "altsa-analyze-m4-static"
    collect_warnings: bool = True
    warnings: dict[str, list[str]] | None = None

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = {}
        if not Path(self.binary).exists():
            raise AnalyzeError(
                f"{self.binary} does not exist -- run `cargo build --release` first"
            )

    def analyze(self, conn: object, case: object) -> list[InferredColumn]:
        del conn  # the point of the exercise
        path = Path(case.path)  # type: ignore[attr-defined]
        payload = self.run(path / "schema.sql", path / "query.sql")
        if "error" in payload:
            raise AnalyzeError(payload["error"])
        if self.warnings is not None and payload.get("warnings"):
            self.warnings[case.id] = list(payload["warnings"])  # type: ignore[attr-defined]
        return [
            InferredColumn(name=c["name"], nullable=bool(c["nullable"]), how="static")
            for c in payload["columns"]
        ]

    def run(self, schema: Path, query: Path) -> dict:
        proc = subprocess.run(
            [str(self.binary), "--schema", str(schema), "--query", str(query)],
            capture_output=True,
            text=True,
        )
        if proc.returncode not in (0, 1):
            raise AnalyzeError(
                f"altsa-analyze exited {proc.returncode}: {proc.stderr.strip()}"
            )
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise AnalyzeError(
                f"altsa-analyze printed non-JSON: {proc.stdout[:400]!r}"
            ) from exc
