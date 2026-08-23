-- from sqlx tests/postgres/setup.sql (only the parts this case needs)
CREATE TABLE tweet (
    id         BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    text       TEXT        NOT NULL,
    owner_id   BIGINT
);

-- `accounts` is not in sqlx's setup.sql; defined here to match the shape the
-- sqlx macro tests assume (a NOT NULL id/name pair plus a nullable flag).
CREATE TABLE accounts (
    id        INTEGER NOT NULL PRIMARY KEY,
    name      TEXT    NOT NULL,
    is_active BOOLEAN
);
