-- SQLite version of the same logical OLTP schema, written in SQLite idioms:
--   * uuid  -> TEXT
--   * enum  -> TEXT + CHECK (...) IN (...)
--   * jsonb -> TEXT (JSON1 functions operate on TEXT)
--   * text[]-> TEXT holding a JSON array
--   * timestamptz -> TEXT with datetime('now') default
--   * numeric(10,2) declared as NUMERIC / DECIMAL(10,2) to observe how
--     sqlacodegen maps SQLite type *affinity* vs. the declared type.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS staff_accounts;
DROP TABLE IF EXISTS accounts;
DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    id         TEXT NOT NULL PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,
    status     TEXT NOT NULL DEFAULT 'active'
               CHECK (status IN ('active','suspended','deleted')),
    metadata   TEXT,                                    -- JSON payload
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE products (
    id    INTEGER       NOT NULL PRIMARY KEY AUTOINCREMENT,
    sku   TEXT          NOT NULL UNIQUE,
    name  VARCHAR(200)  NOT NULL,
    price DECIMAL(10,2) NOT NULL CHECK (price >= 0),
    tags  TEXT,                                         -- JSON array of strings
    stock INTEGER       NOT NULL DEFAULT 0
);

CREATE TABLE orders (
    id         TEXT    NOT NULL PRIMARY KEY,
    user_id    TEXT    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status     TEXT    NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','paid','shipped','cancelled')),
    version    INTEGER NOT NULL DEFAULT 1,
    total      NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE order_items (
    order_id   TEXT          NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER       NOT NULL REFERENCES products(id),
    quantity   INTEGER       NOT NULL CHECK (quantity > 0),
    unit_price DECIMAL(10,2) NOT NULL,
    PRIMARY KEY (order_id, product_id)
);

CREATE TABLE payments (
    id              INTEGER       NOT NULL PRIMARY KEY AUTOINCREMENT,
    order_id        TEXT          NOT NULL UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
    method          TEXT          NOT NULL CHECK (method IN ('card','bank','wallet')),
    amount          NUMERIC(12,2) NOT NULL,
    card_last4      CHAR(4),
    bank_account    TEXT,
    wallet_provider TEXT,
    paid_at         TEXT          NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE accounts (
    id         INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    kind       TEXT    NOT NULL DEFAULT 'basic',
    label      TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE staff_accounts (
    id          INTEGER NOT NULL PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    employee_no TEXT    NOT NULL UNIQUE,
    department  TEXT    NOT NULL
);

CREATE INDEX orders_user_created_idx ON orders (user_id, created_at DESC);
CREATE INDEX orders_status_idx       ON orders (status);
CREATE INDEX order_items_product_idx ON order_items (product_id);
