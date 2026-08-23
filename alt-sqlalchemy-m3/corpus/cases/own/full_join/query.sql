SELECT x.id AS x_id, x.label AS x_label, y.id AS y_id
FROM a x FULL JOIN a y ON y.id = x.id + 100
