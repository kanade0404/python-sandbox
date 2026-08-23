SELECT a.id, s.total, s.n
FROM a
LEFT JOIN (SELECT a_id, sum(amount) AS total, count(*) AS n
           FROM b GROUP BY a_id) s ON s.a_id = a.id
