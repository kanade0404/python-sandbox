"""altsa_gen -- the alt-SQLAlchemy facade generator.

Pipeline:

    SQLAlchemy MetaData.reflect
        -> sqlacodegen TablesGenerator.fix_column_types / get_adapted_type
           (CHECK -> Enum, IN (0,1) -> Boolean, dialect types -> generic,
            (table, column) -> Python enum class name)
        -> altsa_gen.frontend.build_schema      (our IR: GSchema)
        -> altsa_gen.render.render              (our renderer: one .py module)

Only the middle step is sqlacodegen's; the renderer is written from scratch.
"""

from __future__ import annotations

__all__ = ["build_schema", "load_config", "render"]

from .config import load_config
from .frontend import build_schema
from .render import render
