"""altsa_sqlgen -- Layer B Phase 1.

Annotated .sql files in, typed Python out. The nullability of every result
column is inferred from the live database using the two-pass algorithm sqlx
uses (catalog `attnotnull`, patched by `EXPLAIN (VERBOSE, FORMAT JSON)`), with
one soundness fix -- see `nullability.py`.
"""

from __future__ import annotations

from .annotations import Param, ParamRef, QueryBlock, QueryKind, parse_column_name, parse_file
from .describe import Described, Describer, DescribedParam, ResultColumn
from .errors import GenerationError
from .generate import GenerationResult, generate

__all__ = [
    "Described",
    "DescribedParam",
    "Describer",
    "GenerationError",
    "GenerationResult",
    "Param",
    "ParamRef",
    "QueryBlock",
    "QueryKind",
    "ResultColumn",
    "generate",
    "parse_column_name",
    "parse_file",
]
