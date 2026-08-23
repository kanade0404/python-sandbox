SELECT a.id, l.id AS b_id, l.amount
FROM a
LEFT JOIN LATERAL (SELECT b.id, b.amount FROM b
                   WHERE b.a_id = a.id
                   ORDER BY b.amount DESC LIMIT 1) l ON true
