"""PostgreSQL type OID -> Python type, plus the imports each one needs.

Deliberately small and TOTAL: an OID that is not handled raises a
:class:`GenerationError` naming the SQL type. There is no `Any` fallback --
falling back to `Any` is precisely the hole this whole experiment exists to
close, so an unmapped type has to be a generation failure.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import GenerationError


@dataclass(frozen=True, slots=True)
class PyType:
    """A rendered Python type plus the `from X import Y` lines it requires."""

    text: str
    imports: frozenset[tuple[str, str]] = frozenset()

    def optional(self) -> "PyType":
        return PyType(f"{self.text} | None", self.imports)

    def as_list(self) -> "PyType":
        # PostgreSQL arrays may always contain NULL elements -- there is no way
        # to declare otherwise -- so the element type is honestly optional.
        return PyType(f"list[{self.text} | None]", self.imports)


_UUID = PyType("UUID", frozenset({("uuid", "UUID")}))
_DECIMAL = PyType("Decimal", frozenset({("decimal", "Decimal")}))
_DATETIME = PyType("datetime", frozenset({("datetime", "datetime")}))
_DATE = PyType("date", frozenset({("datetime", "date")}))
_TIME = PyType("time", frozenset({("datetime", "time")}))
_TIMEDELTA = PyType("timedelta", frozenset({("datetime", "timedelta")}))
_STR = PyType("str")
_INT = PyType("int")
_BOOL = PyType("bool")
_FLOAT = PyType("float")
_BYTES = PyType("bytes")

# json/jsonb decode to arbitrary JSON. `JsonValue` is a recursive alias emitted
# into every generated module -- Any-free, and pyright handles it under strict.
_JSON = PyType("JsonValue")

#: element OID -> scalar type
SCALARS: dict[int, PyType] = {
    16: _BOOL,  # bool
    17: _BYTES,  # bytea
    18: _STR,  # char
    19: _STR,  # name
    20: _INT,  # int8
    21: _INT,  # int2
    23: _INT,  # int4
    25: _STR,  # text
    26: _INT,  # oid
    114: _JSON,  # json
    700: _FLOAT,  # float4
    701: _FLOAT,  # float8
    1042: _STR,  # bpchar
    1043: _STR,  # varchar
    1082: _DATE,  # date
    1083: _TIME,  # time
    1114: _DATETIME,  # timestamp
    1184: _DATETIME,  # timestamptz
    1186: _TIMEDELTA,  # interval
    1266: _TIME,  # timetz
    1700: _DECIMAL,  # numeric
    2950: _UUID,  # uuid
    3802: _JSON,  # jsonb
}

#: array OID -> element OID
ARRAYS: dict[int, int] = {
    1000: 16,
    1001: 17,
    1002: 18,
    1003: 19,
    1005: 21,
    1007: 23,
    1009: 25,
    1014: 1042,
    1015: 1043,
    1016: 20,
    1021: 700,
    1022: 701,
    1028: 26,
    1115: 1114,
    1182: 1082,
    1183: 1083,
    1185: 1184,
    1187: 1186,
    1231: 1700,
    2951: 2950,
    199: 114,
    3807: 3802,
}


@dataclass(frozen=True, slots=True)
class TypeInfo:
    """What the generator needs to know about one OID, resolved once per run."""

    oid: int
    name: str
    """`pg_type.typname`, or `format_type` output -- for error messages."""
    typtype: str
    """`b`ase, `e`num, `d`omain, `c`omposite, `r`ange, `p`seudo."""
    element_oid: int
    """`pg_type.typelem` when this OID is an array type, else 0."""
    base_oid: int
    """For domains, the OID the domain is built on; else 0."""


def python_type(info: TypeInfo, registry: dict[int, TypeInfo]) -> PyType:
    """Resolve one OID to a Python type, following domains and arrays."""
    oid = info.oid

    if oid in SCALARS:
        return SCALARS[oid]

    if oid in ARRAYS:
        return python_type(registry[ARRAYS[oid]], registry).as_list() if ARRAYS[oid] in registry else SCALARS[ARRAYS[oid]].as_list()

    # Enums: str for M3. See README "Known limitations" -- generating a real
    # Python enum needs the label set and a per-query decision about whether an
    # unknown label should fail, which is M4 work.
    if info.typtype == "e":
        return _STR

    # Domains are transparent for typing purposes.
    if info.typtype == "d" and info.base_oid:
        base = registry.get(info.base_oid)
        if base is not None:
            return python_type(base, registry)

    # Arrays the static table missed (e.g. an array OF an enum or a domain).
    if info.element_oid:
        elem = registry.get(info.element_oid)
        if elem is not None:
            return python_type(elem, registry).as_list()

    raise GenerationError(
        f"no Python type mapping for SQL type {info.name!r} (OID {oid}, "
        f"typtype {info.typtype!r}) -- add it to altsa_sqlgen/typemap.py"
    )


JSON_ALIAS = (
    "type JsonValue = (\n"
    "    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]\n"
    ")"
)
