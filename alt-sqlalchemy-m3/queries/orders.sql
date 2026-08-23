-- EC-schema queries for the M3 end-to-end gate (H2, H7).
-- Schema: ../sqlacodegen-trial/ddl/postgres.sql

-- QUERY get_order_with_user :one
-- An INNER JOIN: every selected column is NOT NULL in the catalog and the
-- EXPLAIN pass finds no outer join, so nothing gets upgraded. `email?`
-- demonstrates that an explicit override BEATS a correct inference: the
-- column is provably NOT NULL here, and the marker still forces `str | None`.
SELECT o.id,
       o.status,
       o.total,
       o.version,
       o.created_at,
       u.id      AS user_id,
       u.email   AS "email?",
       u.metadata
FROM orders o
JOIN users u ON u.id = o.user_id
WHERE o.id = ${order_id}

-- QUERY list_orders_left_join_users :many
-- THE inference case. `users` is on the null-extended side of a LEFT JOIN, so
-- every u.* column must come back nullable even though all of them are
-- NOT NULL in the catalog. There is no override here -- if the EXPLAIN pass
-- did nothing, these would be typed `str` and the H7 runtime check would blow
-- up on the seeded user that has no orders.
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

-- QUERY revenue_by_user :many
-- An aggregate. `SUM` over an empty group is NULL, and PostgreSQL gives the
-- column no base-table attribution at all (ftable = 0), so the catalog pass
-- returns UNKNOWN and it degrades to `Decimal | None` -- correct, and for the
-- right reason. `COUNT(*)` gets the same treatment and comes out
-- `int | None`, which is a SAFE FALSE POSITIVE: count is never NULL, but
-- Phase 1 has no expression-level reasoning to prove it. See the README's
-- Phase1 -> Phase2 delta.
SELECT u.email,
       sum(o.total)  AS revenue,
       count(*)      AS order_count
FROM users u
LEFT JOIN orders o ON o.user_id = u.id
GROUP BY u.email
ORDER BY u.email

-- QUERY transition_order_status :one
-- UPDATE with an optimistic-lock check, RETURNING. `:one` returns None when
-- the version did not match, which is exactly the "someone else got there
-- first" signal.
UPDATE orders
SET status  = ${new_status},
    version = version + 1
WHERE id = ${order_id}
  AND version = ${expected_version}
RETURNING id, status, version, total, created_at
