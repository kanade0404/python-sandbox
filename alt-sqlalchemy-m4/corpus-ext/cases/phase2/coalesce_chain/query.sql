SELECT coalesce(n.note, m.label) AS a,
       coalesce(n.note, m.label, 'fallback') AS b,
       coalesce(n.note, n.note) AS c
FROM m LEFT JOIN n ON n.m_id = m.id
