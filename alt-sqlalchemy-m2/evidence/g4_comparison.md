# G4 -- the 8 OLTP patterns: generated facade vs. `sqlacodegen-trial/src/repo_pg.py`

Sources
: `patterns/patterns_pg.py` (rewrite), `patterns/run_patterns.py` (runner),
  `evidence/run_patterns_pg.log` (live PG 16 run + every SQL statement issued),
  `evidence/pyright_patterns.txt` (pyright strict, 0 errors).

Layer legend
: **A** = expressible in the typed query surface end to end.
  **A (ext)** = expressible, but only because M2 added a schema-INDEPENDENT
  runtime combinator M1 did not have. **ESCAPE HATCH** = `Raw[R]`: verbatim SQL
  with a type-checked ROW type. **B** = verbatim SQL with no typed result
  (`Conn.exec_raw`).

| # | pattern | verdict | what it needed | holes closed vs. repo_pg.py |
|---|---------|---------|----------------|------------------------------|
| 1 | `get_user_by_email` | **A** | nothing | none open in either; the SQLAlchemy version was already honest |
| 2 | `create_order` (tx, `FOR UPDATE`) | **A (ext)** | `QueryBase.for_update()`, `order_by()`, generated `insert_orders`/`insert_order_items`, generated `update_products` | **INSERT completeness** is now static: `orders.user_id` is NOT NULL with no server default, so omitting it is a pyright error (N27). The ORM version accepts `Orders()` with no `user_id` and fails at flush. Stock decrement is `PRODUCTS.stock.set(PRODUCTS.stock.sub(qty))` -- an expression-valued assignment that stays `int`. |
| 3 | `upsert_product` (`ON CONFLICT ... RETURNING`) | **ESCAPE HATCH** | `Raw[ProductRow]` | repo_pg.py's `reveal_type(excluded.price)` was an untyped hole *inside* what looked like typed code. Here the untyped part is the SQL string and the typed part is the row -- the boundary is visible in the source. |
| 4 | `list_orders_for_user` (keyset) | **A (ext)** | `order_by()`, `limit()`, `any_of`/`all_of` | repo_pg.py needed `tuple_(literal(cursor[0]), literal(cursor[1]))`: passing the raw `datetime`/`UUID` runs fine but is a `reportArgumentType` error. Here `ORDERS.created_at.lt(cursor[0])` takes the plain value and the OR-of-ANDs is what the planner sees anyway. |
| 5 | `list_users_with_orders` (LEFT JOIN) | **A** | nothing | **The flagship.** repo_pg.py has two versions: `..._unsafe` type-checks and lies (`row[1]` is `Orders`, `None` at runtime) and `..._safe` needs `sqlalchemy.Nullable(Orders)` to be honest. Here there is no unsafe version to write: `left_join_orders()` returns a shape whose `orders` namespace *is* the `| None` variant, so `row[1]` is `UUID | None`. N5 proves the `| None` bites. |
| 6 | `revenue_by_user` (SUM + GROUP BY) | **A (ext)** | `group_by()`, `sum_()`, `coalesce()` | **Two** holes. (a) Operand order: SQLAlchemy types `quantity * unit_price` as `ColumnElement[int]` and `unit_price * quantity` as `ColumnElement[Decimal]` -- same SQL, different static type. There is no reflected operator here, so the "backwards" form cannot be written (N10), and `NumCol[Decimal].mul(Expr[int])` still yields `Expr[Decimal]`. (b) `sum_()` returns `Expr[Decimal \| None]` because SUM over an empty group is NULL; `func.sum()` claims the bare type. N30 proves the nullability bites. |
| 7 | `transition_order_status` (optimistic lock) | **A (ext)** | generated `update_orders`, `Col.set()`, `NumCol.add()` | repo_pg.py needs `cast("CursorResult[Any]", session.execute(stmt))` purely to reach `.rowcount`, because `Session.execute()` is typed `Result[Any]`. `update_orders(...) -> int` has no cast and no `Any`. Assignments are **table-tagged**: `update_orders(conn, USERS.email.set(...))` is a type error (N26). |
| 8 | `record_payment` / `load_payment_view` | **A + hand-written narrowing** | nothing | *Not* closed, and for the same reason as before: on PostgreSQL `CHECK (method IN ('card','bank','wallet'))` compiles to `method = ANY (ARRAY[...])`, which sqlacodegen's `_re_enum_check_constraint` does not match, so `payments.method` stays `str` and the discriminated union is hand-written. The *write* side did improve: `insert_payments` makes `order_id`/`method`/`amount` required keywords. **On SQLite the very same logical schema DOES yield a generated `PaymentsMethod` enum** (see `evidence/gen_sqlite_report.txt`) -- the difference is purely the frontend's regex, and it is documented rather than fought. |

## Summary

* Layer A: 1, 5 (2 patterns)
* Layer A after a schema-independent runtime extension: 2, 4, 6, 7 (4 patterns)
* Escape hatch (`Raw`): 3 (1 pattern)
* Layer A read + hand-written discriminator narrowing: 8 (1 pattern)
* Layer B (`exec_raw`, no typed result): only the test fixture's `TRUNCATE`

7 of 8 patterns are fully inside the typed surface. The one that is not
(upsert) is not a *type-system* failure -- it is a deliberate refusal to invent
a portable spelling for a vendor DML clause, and the escape hatch keeps the row
type checked.

## Holes that remain open

* `Raw`'s SQL text is unchecked, and its row dataclass is matched to the select
  list POSITIONALLY at runtime. A wrong column order is a runtime error.
* `Pred` is not table-tagged, so `update_orders(conn, ..., where=USERS.email.eq(x))`
  type-checks and produces wrong (or failing) SQL. Only the SET side is tagged.
  Tagging `Pred` would need a variance-safe union of tags; left for M3.
* No `HAVING`, no window functions, no subqueries, no `DISTINCT`, no `UNION`.
* The statement memo keys on shape, so `limit(10)` and `limit(20)` are two
  entries (the limit is not parameterised). Cheap to fix, not done.
