INSERT INTO tweet (id, text, owner_id) VALUES
    (1, 'a row', 10),
    (2, 'another row', NULL);
-- ON false: the result is 2 rows with id2 NULL plus 2 rows with id1 NULL.
