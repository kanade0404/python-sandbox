INSERT INTO tweet (id, text, owner_id) VALUES
    (1, 'hello world', 10),
    (2, 'an ownerless tweet', NULL);
-- The accounts branch contributes rows whose `b` is the NULL literal, so
-- column b is NULL from two independent sources while column a never is.
INSERT INTO accounts (id, name, is_active) VALUES
    (1, 'alice', TRUE),
    (2, 'bob', NULL);
