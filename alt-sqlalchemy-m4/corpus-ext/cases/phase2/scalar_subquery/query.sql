SELECT m.id,
       (SELECT n.note FROM n WHERE n.m_id = m.id ORDER BY n.id LIMIT 1) AS note,
       EXISTS (SELECT 1 FROM n WHERE n.m_id = m.id) AS has_n
FROM m
