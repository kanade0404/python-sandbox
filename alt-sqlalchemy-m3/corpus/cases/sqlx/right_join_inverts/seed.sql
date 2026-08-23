INSERT INTO tweet (id, text, owner_id) VALUES
    (1, 'right row', 10),
    (2, 'another right row', NULL);
-- ON false null-extends the left side, so id1 is observably NULL.
