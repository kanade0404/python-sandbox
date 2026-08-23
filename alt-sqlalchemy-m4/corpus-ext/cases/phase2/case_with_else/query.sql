SELECT CASE WHEN m.amount > 0 THEN m.label ELSE 'zero' END AS v,
       CASE WHEN m.amount > 0 THEN n.note ELSE 'zero' END AS w
FROM m LEFT JOIN n ON n.m_id = m.id
