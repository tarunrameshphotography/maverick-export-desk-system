-- Content Engine (Phase 2): a signal now produces a *bundle* of named assets
-- (three LinkedIn variants, two Reels, a caption, a carousel, a visual
-- direction brief) instead of one draft per channel. asset_slot disambiguates
-- rows that share a channel; existing rows backfill asset_slot = channel so
-- the single-draft workflow (cmd_draft) keeps working unchanged.
ALTER TABLE content_drafts ADD COLUMN asset_slot TEXT;
UPDATE content_drafts SET asset_slot = channel WHERE asset_slot IS NULL;
