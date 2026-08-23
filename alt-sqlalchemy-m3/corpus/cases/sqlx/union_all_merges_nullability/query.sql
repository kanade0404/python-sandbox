SELECT text AS a, owner_id AS b FROM tweet
UNION ALL
SELECT name, NULL FROM accounts
