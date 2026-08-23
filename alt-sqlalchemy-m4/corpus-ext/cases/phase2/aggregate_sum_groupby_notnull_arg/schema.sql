-- The shared shape for corpus-ext: one table with a NOT NULL numeric, a
-- nullable numeric, and a NOT NULL grouping key; plus a child table whose only
-- payload column is nullable. Every NULL in a result is therefore attributable
-- either to `bonus`/`note` or to the query shape, never to an accident.
CREATE TABLE m (
    id     integer PRIMARY KEY,
    label  text    NOT NULL,
    amount numeric NOT NULL,
    bonus  numeric,
    grp    integer NOT NULL
);

CREATE TABLE n (
    id   integer PRIMARY KEY,
    m_id integer NOT NULL REFERENCES m(id),
    note text
);
