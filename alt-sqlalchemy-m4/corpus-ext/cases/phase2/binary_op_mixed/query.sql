SELECT m.amount + 1 AS a,
       m.amount + m.bonus AS b,
       m.amount > 0 AS c,
       m.bonus > 0 AS d
FROM m
