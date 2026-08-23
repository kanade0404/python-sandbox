SELECT m.label AS a, m.label AS b FROM m
UNION ALL
SELECT n.note, 'literal' FROM n
