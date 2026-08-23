WITH cte AS MATERIALIZED (SELECT * FROM tweet) SELECT id, text, owner_id FROM cte
