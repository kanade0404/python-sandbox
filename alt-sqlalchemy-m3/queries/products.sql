-- EC-schema write-side queries for the M3 end-to-end gate (H2, H7).

-- QUERY upsert_product :one
-- INSERT ... ON CONFLICT ... RETURNING. Generation only DESCRIBEs this and
-- then EXPLAINs the prepared statement; neither plans-and-runs, so no row is
-- ever written at generation time. (Checked: both tables still had 0 rows
-- after a full generation run.)
INSERT INTO products (sku, name, price, tags, stock)
VALUES (${sku}, ${name}, ${price}, ${tags}, ${stock})
ON CONFLICT (sku) DO UPDATE
    SET name  = EXCLUDED.name,
        price = EXCLUDED.price,
        tags  = EXCLUDED.tags,
        stock = products.stock + EXCLUDED.stock
RETURNING id, sku, name, price, tags, stock

-- QUERY lock_products_for_update :many
-- SELECT ... FOR UPDATE. Row locks are a runtime concern; for describe and
-- EXPLAIN this is an ordinary SELECT, and the LockRows plan node is
-- transparent to the outer-join walk.
SELECT id, sku, name, price, tags, stock
FROM products
WHERE sku = ANY(${skus})
ORDER BY sku
FOR UPDATE

-- QUERY insert_payment :exec
-- `:exec` -- no RETURNING, so the generated function's return type is the
-- affected row count. Note `${card_last4?}`: the parameter is declared
-- nullable, so it is typed `str | None` rather than `str`.
INSERT INTO payments (order_id, method, amount, card_last4)
VALUES (${order_id}, ${method}, ${amount}, ${card_last4?})
