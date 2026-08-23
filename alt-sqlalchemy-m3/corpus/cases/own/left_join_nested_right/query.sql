SELECT a.id, b.id AS b_id, c.id AS c_id
FROM a LEFT JOIN (b JOIN c ON c.b_id = b.id) ON b.a_id = a.id
