"""The "argument not supplied" sentinel used by generated typed writers.

SCHEMA-INDEPENDENT. A generated `insert_orders()` must distinguish three cases
for a column: a value was given, SQL NULL was given, or the column was omitted
so the server default applies. `None` can only express two of them, so a
dedicated sentinel type is needed -- and it must be a *type*, not `Any`, for
`isinstance` narrowing to work under pyright strict.
"""

from __future__ import annotations

from typing import Final, final


@final
class Unset:
    """Type of the "not supplied" marker. Do not instantiate; use `UNSET`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final[Unset] = Unset()
