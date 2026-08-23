-- M5 Layer B -- the command side, order half.
-- `transition_order_status` is M3's query verbatim; the rest are new and exist
-- because `create_order` is a TRANSACTION, which is the shape Layer A's typed
-- writers cannot express in one statement.

-- QUERY insert_order :one
-- The order header. `status`, `version` and `total` all take their server
-- defaults ('pending', 1, 0) so the application never has to know them; the
-- total is corrected by `set_order_total` once the lines are in.
INSERT INTO orders (user_id)
VALUES (${user_id})
RETURNING id, user_id, status, version, total, created_at

-- QUERY insert_order_item :exec
-- One line. `:exec` -- the row count is the whole answer.
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
VALUES (${order_id}, ${product_id}, ${quantity}, ${unit_price})

-- QUERY set_order_total :exec
-- create_order computes the total from the LOCKED prices, so the header is
-- inserted first (the items need its id) and corrected at the end.
UPDATE orders
SET total = ${total}
WHERE id = ${order_id}

-- QUERY record_payment :one
-- `payments.order_id` is UNIQUE, so this is the "exactly one payment per order"
-- write. `${card_last4?}` is declared nullable: parameter nullability is not
-- inferable, so it is the one thing Layer B still asks you to state.
INSERT INTO payments (order_id, method, amount, card_last4)
VALUES (${order_id}, ${method}, ${amount}, ${card_last4?})
RETURNING id, order_id, method, amount, paid_at

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

-- QUERY get_order_with_user :one
-- An INNER JOIN read kept on Layer B on purpose: the scenario cross-checks it
-- against the Layer A search over the same two tables, which is the cheapest
-- possible test that the two layers agree about the same database.
SELECT o.id,
       o.status,
       o.total,
       o.version,
       o.created_at,
       u.id      AS user_id,
       u.email
FROM orders o
JOIN users u ON u.id = o.user_id
WHERE o.id = ${order_id}
