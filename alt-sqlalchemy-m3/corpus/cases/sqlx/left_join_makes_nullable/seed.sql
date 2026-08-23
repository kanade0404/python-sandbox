-- A tweet exists, so the nullability is not an artefact of an empty table:
-- the join is ON false, so the single left row is still null-extended.
INSERT INTO tweet (id, text, owner_id) VALUES (1, 'never joined', 10);
