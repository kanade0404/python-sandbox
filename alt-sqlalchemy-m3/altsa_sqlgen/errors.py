"""Generation-time errors.

Every failure the generator can hit is reported as a :class:`GenerationError`
carrying enough context (file, query name, column) for a human to fix the .sql
source.  Nothing here is raised at *runtime* by generated code.
"""

from __future__ import annotations


class GenerationError(Exception):
    """A user-fixable problem in the annotated .sql input."""

    def __init__(self, message: str, *, source: str | None = None, query: str | None = None) -> None:
        parts: list[str] = []
        if source is not None:
            parts.append(source)
        if query is not None:
            parts.append(f"query {query!r}")
        prefix = " / ".join(parts)
        super().__init__(f"{prefix}: {message}" if prefix else message)
        self.raw_message = message
        self.source = source
        self.query = query
