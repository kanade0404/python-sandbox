-- M5 Layer B -- the command side of the order service, user half.
-- Schema: ../sqlacodegen-trial/ddl/postgres.sql
--
-- Everything here is a WRITE. That is the split M5 is testing: statements whose
-- shape is fixed and whose semantics need exact SQL (RETURNING, ON CONFLICT,
-- FOR UPDATE, optimistic version checks) belong to Layer B; the composable
-- read side belongs to Layer A.

-- QUERY register_user :one
-- Registration. `status` is a native PostgreSQL ENUM; Layer B types the
-- parameter `str`, not a Python enum -- see the friction log, F10.
INSERT INTO users (email, status)
VALUES (${email}, ${status})
RETURNING id, email, status, created_at

-- QUERY get_user_by_email :one
-- The Layer B side of the benchmark's "same simple SELECT".
SELECT id, email, status, created_at
FROM users
WHERE email = ${email}
