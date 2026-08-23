-- One tweet with an owner and one without, so `owner_id` really is NULL
-- somewhere in the result.
INSERT INTO tweet (id, text, owner_id) VALUES
    (1, 'hello world', 10),
    (2, 'an ownerless tweet', NULL);
