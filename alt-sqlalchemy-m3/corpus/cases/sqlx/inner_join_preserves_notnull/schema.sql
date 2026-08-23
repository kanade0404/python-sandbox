-- from sqlx tests/postgres/setup.sql (only the parts this case needs)
CREATE TABLE tweet (
    id         BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    text       TEXT        NOT NULL,
    owner_id   BIGINT
);

CREATE TABLE products (
    product_no INTEGER,
    name       TEXT,
    price      NUMERIC CHECK (price > 0)
);
