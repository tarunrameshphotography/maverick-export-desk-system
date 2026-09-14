-- Aggregator links (news.google.com/...) hide the real publisher; independence
-- checks (Part 12 two-source rule) need the outlet, not the aggregator.
ALTER TABLE raw_items ADD COLUMN publisher TEXT;
