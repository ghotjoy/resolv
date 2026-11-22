INSERT OR IGNORE INTO config(k,data)
SELECT 'priority_strategy','"legacy"' WHERE EXISTS(SELECT 1 FROM users);
