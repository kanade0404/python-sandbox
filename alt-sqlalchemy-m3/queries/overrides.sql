-- H5: overrides beat inference in BOTH directions.
--
-- Two spellings are supported. The sqlx-style alias marker is preferred
-- because it sits on the column it affects:
--
--     SELECT count(*) AS "n!"      -- force NOT NULL
--     SELECT u.email AS "email?"   -- force nullable
--
-- and a block directive is available for columns that already have the name
-- you want:
--
--     -- OVERRIDE <column> :notnull|:nullable
--
-- An OVERRIDE naming a column the query does not return is a clean generation
-- error listing the columns that do exist -- see evidence/h5_bad_override.txt.

-- QUERY order_stats_for_user :one
-- `!` beats inference UPWARDS. Both aggregates are expressions, so pass 1 has
-- no base-table attribution for them and pass 2 finds no outer join; they
-- would degrade to the safe default of nullable. `count(*)` over any group is
-- provably never NULL, so the marker asserts what Phase 1 cannot prove.
--
-- `total_spent` is deliberately left un-marked: SUM really is NULL when the
-- user has no orders, and the safe default is the correct answer there.
SELECT count(*)                  AS "order_count!",
       sum(o.total)              AS total_spent,
       max(o.created_at)         AS last_order_at
FROM orders o
WHERE o.user_id = ${user_id}

-- QUERY user_email_maybe :many
-- `?` beats inference DOWNWARDS. `users.email` is NOT NULL in the catalog and
-- there is no outer join, so inference correctly says NOT NULL -- and the
-- marker overrides it anyway. This is the escape hatch for a caller who knows
-- something the schema does not (a view about to change, a column mid-
-- migration), and it is the only direction that can make types LESS precise.
SELECT u.id,
       u.email AS "email?"
FROM users u
ORDER BY u.id

-- QUERY user_email_via_directive :many
-- The same downgrade, spelled as a block directive instead of an alias.
-- OVERRIDE email :nullable
SELECT u.id,
       u.email
FROM users u
ORDER BY u.id
