SELECT m.id::text AS a,
       n.note::text AS b,
       (1 + 1)::text AS c
FROM m LEFT JOIN n ON n.m_id = m.id
