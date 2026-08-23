# G1 -- generating from PostgreSQL 16 vs. SQLite, same logical schema

Both runs used the same generator, the same `[[shape]]`/`[[projection]]`
declarations, and the two DDL files from `sqlacodegen-trial/ddl/`. Reports:
`evidence/gen_pg_report.txt`, `evidence/gen_sqlite_report.txt`.

Identical in both: 7 tables, 10 edges (all derived from FKs, both directions),
31 shapes (27 auto + 4 declared), 2 projections. The join graph is a pure
function of the foreign keys, and SQLite declares the same ones.

## Where the two outputs differ

| column | PostgreSQL | SQLite | why |
|--------|-----------|--------|-----|
| `users.id`, `orders.id`, `order_items.order_id`, `orders.user_id`, `payments.order_id` | `uuid` -> `UUID` | `text` -> `str` | SQLite has no UUID type; the DDL declares TEXT. Nothing in the frontend invents one. |
| `*.created_at`, `payments.paid_at` | `timestamptz` -> `datetime.datetime` | `text` -> `str` | SQLite stores timestamps as TEXT with `datetime('now')` defaults. |
| `users.metadata` | `json` (jsonb) -> `object` | `text` -> `str` | JSON1 operates on TEXT. |
| `products.tags` | `array` of `text` -> `list[str]` | `text` -> `str` | The SQLite DDL holds a JSON array in a TEXT column. |
| `payments.method` | `text` -> `str` | **`enum` -> generated `PaymentsMethod`** | See below. |
| `users.status`, `orders.status` | native `user_status` / `order_status` ENUM -> `UserStatus` / `OrderStatus` | CHECK-derived -> `UsersStatus` / `OrdersStatus` | Different NAMES, same members. |
| `products.price`, `orders.total`, ... | `numeric(p,s)` -> `Decimal` | `NUMERIC/DECIMAL(p,s)` -> `Decimal` | identical -- SQLAlchemy keeps the declared type, not the affinity |

## The interesting one: `payments.method`

`CHECK (method IN ('card','bank','wallet'))` is written identically in both DDL
files, but:

* **SQLite** stores the constraint text verbatim, so sqlacodegen's
  `_re_enum_check_constraint` matches `method IN ('card','bank','wallet')` and
  `fix_column_types` rewrites the column to a synthetic `Enum` and registers a
  Python enum class. The facade gets a real `PaymentsMethod`.
* **PostgreSQL** normalises the constraint to
  `method = ANY (ARRAY['card'::text, 'bank'::text, 'wallet'::text])`. The regex
  does not match, so the column stays `text`.

This is sqlacodegen frontend behaviour, reused as-is and NOT fought: the whole
point of depending on the package is to inherit its normalisation decisions
(and its bugs) rather than to re-litigate them. The consequence is documented in
`patterns/patterns_pg.py` pattern 8, whose discriminated union is hand-written
on PostgreSQL for exactly this reason -- the same reason it was hand-written in
`sqlacodegen-trial/src/repo_pg.py`.

Enum CLASS NAMING also differs: a native PG enum takes its name from the SQL
type (`user_status` -> `UserStatus`), while a CHECK-derived one is named from
table+column (`users.status` -> `UsersStatus`). Both come from sqlacodegen
(`_enum_name_to_class_name` vs `_create_enum_class`). A schema migrated from
SQLite to PostgreSQL would therefore see its enum classes renamed -- worth
knowing, not worth patching in the generator.

## Both outputs are pyright-strict clean

`proofs/pyrightconfig.core.json` includes `../generated`, which covers both
`generated/pg/facade.py` and `generated/sqlite/facade.py`:
`evidence/pyright_core.txt` -> 0 errors.

The SQLite facade also EXECUTES: `evidence/run_sqlite_smoke.log`.
