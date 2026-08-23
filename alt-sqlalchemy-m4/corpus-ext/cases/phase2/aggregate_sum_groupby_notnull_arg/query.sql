SELECT m.grp, sum(m.amount) AS total, count(*) AS n FROM m GROUP BY m.grp
