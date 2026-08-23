SELECT b.a_id, sum(b.amount) AS total, count(*) AS n
FROM b GROUP BY b.a_id
