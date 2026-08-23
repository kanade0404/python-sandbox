SELECT a.id,
       coalesce(b.amount, 0)  AS amount,
       coalesce(a.note, '')   AS note
FROM a LEFT JOIN b ON b.a_id = a.id
