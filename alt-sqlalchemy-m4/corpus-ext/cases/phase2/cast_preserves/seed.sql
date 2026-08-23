-- m=1 has a child with a note; m=2 has a child with a NULL note; m=3 has no
-- child at all. grp 10 holds rows with a bonus, grp 20 holds only NULL bonuses.
INSERT INTO m (id, label, amount, bonus, grp) VALUES
    (1, 'first',  5.00, 1.50, 10),
    (2, '',       0.00, NULL, 10),
    (3, 'third',  7.00, NULL, 20);
INSERT INTO n (id, m_id, note) VALUES
    (100, 1, 'a note'),
    (101, 2, NULL);
