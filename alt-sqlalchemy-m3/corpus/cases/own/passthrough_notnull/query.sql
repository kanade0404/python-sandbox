SELECT a.id, a.label, a.note
FROM a
WHERE ${min_id}::integer IS NULL OR a.id >= ${min_id}
