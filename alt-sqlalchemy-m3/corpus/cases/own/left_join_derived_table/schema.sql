-- The shared shape for the hand-written cases: a 1-N-N chain where every
-- column is NOT NULL except `a.note`. That makes any NULL in a result
-- unambiguously the fault of the query shape, not of the data.
CREATE TABLE a (
    id    integer PRIMARY KEY,
    label text    NOT NULL,
    note  text
);

CREATE TABLE b (
    id     integer PRIMARY KEY,
    a_id   integer NOT NULL REFERENCES a(id),
    amount numeric NOT NULL
);

CREATE TABLE c (
    id   integer PRIMARY KEY,
    b_id integer NOT NULL REFERENCES b(id),
    tag  text    NOT NULL
);
