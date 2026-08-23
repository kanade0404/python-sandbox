-- M5 Layer B -- the command side, product half.
-- `upsert_product` and `lock_products_for_update` are M3's queries, carried
-- over unchanged so the M5 tree regenerates the same functions from the same
-- SQL against a different database.

-- QUERY upsert_product :one
-- INSERT ... ON CONFLICT ... RETURNING. Generation only DESCRIBEs this and
-- then EXPLAINs the prepared statement; neither plans-and-runs, so no row is
-- ever written at generation time.
INSERT INTO products (sku, name, price, tags, stock)
VALUES (${sku}, ${name}, ${price}, ${tags}, ${stock})
ON CONFLICT (sku) DO UPDATE
    SET name  = EXCLUDED.name,
        price = EXCLUDED.price,
        tags  = EXCLUDED.tags,
        stock = products.stock + EXCLUDED.stock
RETURNING id, sku, name, price, tags, stock

-- QUERY lock_products_for_update :many
-- SELECT ... FOR UPDATE -- the first half of create_order. Locking the rows
-- before reading their stock is what makes the decrement below safe against a
-- concurrent order for the same product.
SELECT id, sku, name, price, tags, stock
FROM products
WHERE sku = ANY(${skus})
ORDER BY sku
FOR UPDATE

-- QUERY decrement_stock :one
-- The conditional decrement. `stock >= ${quantity}` in the WHERE clause means a
-- short row simply matches nothing, so `:one` returning None IS the
-- out-of-stock signal -- the same "no row means someone else won" shape the
-- optimistic status transition uses.
UPDATE products
SET stock = stock - ${quantity}
WHERE id = ${product_id}
  AND stock >= ${quantity}
RETURNING id, sku, price, stock

-- QUERY get_product_by_sku :one
-- The Layer B side of benchmark 2 (against a hand-written psycopg execute of
-- exactly this SQL).
SELECT id, sku, name, price, stock
FROM products
WHERE sku = ${sku}
