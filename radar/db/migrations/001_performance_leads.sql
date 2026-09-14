-- North-star metric is qualified conversations (performance_learning_loop.md);
-- the baseline performance table only had engagement counts.
ALTER TABLE performance ADD COLUMN qualified_leads INTEGER DEFAULT 0;
