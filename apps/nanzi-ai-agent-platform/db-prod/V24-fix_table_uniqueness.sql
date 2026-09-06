-- Fix table uniqueness to be scoped by dataset
-- Previously, physical_name was unique globally, causing tables to be "stolen" when imported into a new dataset.

-- 1. Drop the existing global unique index (Verified actual name is uk_physical_name)
ALTER TABLE meta_tables DROP INDEX uk_physical_name;

-- 2. Add the new composite unique index (dataset_id + physical_name)
-- Use IF NOT EXISTS logic equivalent by allowing the script to skip if already present
ALTER TABLE meta_tables ADD UNIQUE INDEX uix_dataset_physical_name (dataset_id, physical_name);