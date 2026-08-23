"""G6: does the GENERATED catalog match what PostgreSQL actually says?

Compares, for every column of every table in the EC schema:
  * NOT NULL          vs. information_schema.columns.is_nullable
  * has-server-default vs. information_schema.columns.column_default / identity
  * the generated Python type against the reflected `data_type`

The comparison deliberately reads `information_schema` with raw SQL rather than
SQLAlchemy reflection, so it is an INDEPENDENT oracle -- a bug in the
reflection path cannot make both sides agree.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import psycopg

from altsa_runtime.catalog import REGISTRY
from generated.pg import facade  # noqa: F401  (import registers the catalog)

DSN = "postgresql://postgres:postgres@localhost:55432/ec"

# generated kind -> the set of PG data_type strings that may legitimately map
# to it, plus the Python type the facade promises
EXPECTED: dict[str, tuple[set[str], str]] = {
    "uuid": ({"uuid"}, "uuid.UUID"),
    "text": ({"text", "character varying", "character"}, "str"),
    "enum": ({"USER-DEFINED"}, "<generated enum>"),
    "timestamptz": ({"timestamp with time zone"}, "datetime.datetime"),
    "timestamp": ({"timestamp without time zone"}, "datetime.datetime"),
    "numeric": ({"numeric"}, "decimal.Decimal"),
    "int": ({"integer", "bigint", "smallint"}, "int"),
    "float": ({"double precision", "real"}, "float"),
    "bool": ({"boolean"}, "bool"),
    "json": ({"json", "jsonb"}, "object"),
    "bytes": ({"bytea"}, "bytes"),
    "array": ({"ARRAY"}, "list[...]"),
}


@dataclass(frozen=True, slots=True)
class Row:
    table: str
    column: str
    data_type: str
    is_nullable: bool
    has_default: bool


def catalog_rows() -> list[Row]:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.table_name, c.column_name, c.data_type,
                   c.is_nullable = 'YES',
                   (c.column_default IS NOT NULL OR c.is_identity = 'YES')
            FROM information_schema.columns c
            JOIN information_schema.tables t
              ON t.table_name = c.table_name AND t.table_schema = c.table_schema
            WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
            ORDER BY c.table_name, c.ordinal_position
            """
        )
        return [Row(r[0], r[1], r[2], bool(r[3]), bool(r[4])) for r in cur.fetchall()]


def main() -> int:
    problems: list[str] = []
    checked = 0
    print(
        f"{'table.column':<30} {'pg data_type':<28} {'kind':<12} "
        f"{'notnull':<8} {'default':<8} verdict"
    )
    for row in catalog_rows():
        spec = REGISTRY.tables[row.table].column(row.column)
        checked += 1
        issues: list[str] = []
        if spec.not_null == row.is_nullable:
            issues.append(
                f"nullability: generated not_null={spec.not_null}, "
                f"pg is_nullable={row.is_nullable}"
            )
        if spec.has_default != row.has_default:
            issues.append(
                f"default: generated has_default={spec.has_default}, "
                f"pg has_default={row.has_default}"
            )
        allowed, _py = EXPECTED.get(spec.kind, (set(), "?"))
        if row.data_type not in allowed:
            issues.append(f"kind {spec.kind!r} does not cover pg {row.data_type!r}")
        verdict = "OK" if not issues else "MISMATCH"
        print(
            f"{row.table + '.' + row.column:<30} {row.data_type:<28} "
            f"{spec.kind:<12} {str(spec.not_null):<8} "
            f"{str(spec.has_default):<8} {verdict}"
        )
        for i in issues:
            print(f"    !! {i}")
            problems.append(f"{row.table}.{row.column}: {i}")

    # ---- the three type-mapping claims, stated explicitly -----------------
    print()
    claims = [
        (
            "PG ENUM -> real Python enum",
            facade.OrderStatus.PAID.value == "paid"
            and [m.value for m in facade.OrderStatus]
            == ["pending", "paid", "shipped", "cancelled"]
            and REGISTRY.enums[("orders", "status")] is facade.OrderStatus,
        ),
        (
            "numeric -> Decimal",
            REGISTRY.tables["orders"].column("total").kind == "numeric"
            and REGISTRY.tables["orders"].column("total").precision == 12
            and REGISTRY.tables["orders"].column("total").scale == 2,
        ),
        (
            "timestamptz -> datetime (tz-aware)",
            REGISTRY.tables["orders"].column("created_at").kind == "timestamptz",
        ),
        (
            "text[] -> array of text",
            REGISTRY.tables["products"].column("tags").kind == "array"
            and REGISTRY.tables["products"].column("tags").item_kind == "text",
        ),
    ]
    for name, ok in claims:
        print(f"[{'OK' if ok else 'FAIL'}] {name}")
        if not ok:
            problems.append(name)

    print()
    print(f"columns checked: {checked}; problems: {len(problems)}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
