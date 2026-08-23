# The emitted code for one query

`queries/orders.sql`, the LEFT JOIN case. Nothing about nullability is declared
in the input; every `| None` below was decided by the two-pass inference against
a live PostgreSQL 16.

## Input

```sql
-- QUERY list_orders_left_join_users :many
SELECT u.id      AS user_id,
       u.email,
       u.status  AS user_status,
       o.id      AS order_id,
       o.total,
       o.status  AS order_status,
       o.created_at
FROM users u
LEFT JOIN orders o ON o.user_id = u.id AND o.total >= ${min_total}
ORDER BY u.email, o.created_at
```

Every column named here is `NOT NULL` in the EC schema -- `orders.id` is the
primary key, `orders.total` and `orders.status` have `NOT NULL DEFAULT`.

## Output (`generated/orders.py`, verbatim)

```python
_LIST_ORDERS_LEFT_JOIN_USERS_SQL = """\
SELECT u.id      AS user_id,
       u.email,
       u.status  AS user_status,
       o.id      AS order_id,
       o.total,
       o.status  AS order_status,
       o.created_at
FROM users u
LEFT JOIN orders o ON o.user_id = u.id AND o.total >= %(min_total)s
ORDER BY u.email, o.created_at
"""


@dataclass(frozen=True, slots=True)
class ListOrdersLeftJoinUsersRow:
    """One row of `list_orders_left_join_users`."""

    user_id: UUID
    email: str
    user_status: str
    order_id: UUID | None
    total: Decimal | None
    order_status: str | None
    created_at: datetime | None


def list_orders_left_join_users(
    conn: _Conn,
    *,
    min_total: Decimal,
) -> list[ListOrdersLeftJoinUsersRow]:
    """Fetch every row. Generated from `-- QUERY list_orders_left_join_users :many`."""
    with conn.cursor() as cur:
        cur.execute(_LIST_ORDERS_LEFT_JOIN_USERS_SQL, {"min_total": min_total})
        return [
            ListOrdersLeftJoinUsersRow(
                user_id=cast(UUID, row[0]),
                email=cast(str, row[1]),
                user_status=cast(str, row[2]),
                order_id=cast(UUID | None, row[3]),
                total=cast(Decimal | None, row[4]),
                order_status=cast(str | None, row[5]),
                created_at=cast(datetime | None, row[6]),
            )
            for row in cur.fetchall()
        ]
```

## What decided each type

| field | catalog pass | EXPLAIN pass | result |
|---|---|---|---|
| `user_id` | `attnotnull` -> NOT NULL | no opinion | `UUID` |
| `email` | `attnotnull` -> NOT NULL | no opinion | `str` |
| `user_status` | `attnotnull` -> NOT NULL | no opinion | `str` |
| `order_id` | `attnotnull` -> NOT NULL | **nullable** | `UUID \| None` |
| `total` | `attnotnull` -> NOT NULL | **nullable** | `Decimal \| None` |
| `order_status` | `attnotnull` -> NOT NULL | **nullable** | `str \| None` |
| `created_at` | `attnotnull` -> NOT NULL | **nullable** | `datetime \| None` |

The four upgrades all come from one fact in the plan JSON: PostgreSQL emits this
as `Hash Join / "Join Type": "Right"` whose OUTER child is the `orders` scan,
and the outer input of a Right join is the null-extended one.

`min_total: Decimal` was not declared either -- the server inferred `$1` as
`numeric` from the comparison against `orders.total`, and OID 1700 maps to
`Decimal` rather than `float`.

## The bits that are load-bearing

* **`%(min_total)s`, not `$1`.** The SQL is embedded verbatim except for the
  parameter markers, so the text in the generated module is the text a human
  can paste into `psql`.
* **`cast(...)` at every field.** `_Conn` is
  `psycopg.Connection[tuple[object, ...]]`, so `row[i]` is `object` and each
  field needs an explicit narrowing. There are exactly as many casts as there
  are columns, and each one is a claim the server's own RowDescription backs.
  There is no other place where trust enters the generated code.
* **Keyword-only parameters.** `list_orders_left_join_users(conn, min_total=...)`
  -- passing positionally is a type error, which is what stops the classic
  swapped-same-typed-arguments bug.
* **`frozen=True, slots=True`.** Rows are values, not mutable records.
