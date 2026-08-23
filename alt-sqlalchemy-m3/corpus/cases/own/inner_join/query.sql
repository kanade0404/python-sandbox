SELECT a.id, a.label, b.id AS b_id, b.amount
FROM a JOIN b ON b.a_id = a.id
