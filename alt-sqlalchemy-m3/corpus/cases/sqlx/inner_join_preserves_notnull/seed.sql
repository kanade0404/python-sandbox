INSERT INTO tweet (id, text, owner_id) VALUES
    (1, 'widget', 10),
    (2, 'unmatched', NULL);
-- 'widget' matches tweet 1; the NULL-named row matches nothing and the
-- inner join drops it, which is exactly why `id` stays NOT NULL.
INSERT INTO products (product_no, name, price) VALUES
    (1, 'widget', 9.99),
    (2, NULL, NULL);
