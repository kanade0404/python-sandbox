-- a=1 has a b (which has a c); a=2 has nothing. Any LEFT JOIN therefore
-- produces at least one genuinely null-extended row.
INSERT INTO a (id, label, note) VALUES (1, 'has-b', 'note-1'), (2, 'no-b', NULL);
INSERT INTO b (id, a_id, amount) VALUES (10, 1, 5.00);
INSERT INTO c (id, b_id, tag) VALUES (100, 10, 'tag-1');
