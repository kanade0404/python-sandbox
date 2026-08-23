INSERT INTO tweet (id, text, owner_id) VALUES
    (1, 'left row', 10),
    (2, 'another left row', NULL);
-- ON false means every left row is null-extended, so id2 is observably NULL
-- while id1 is observably never NULL.
