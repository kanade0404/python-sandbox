-- H5 negative: an OVERRIDE that names a column this query does not return.
-- Expected: a clean GenerationError naming the file, the query and the columns
-- that DO exist -- not a traceback, and not a silently ignored directive.

-- QUERY bad_override :many
-- OVERRIDE emial :nullable
SELECT u.id, u.email FROM users u
