-- Migration V6: Add RAGFlow configuration keys
-- Created: 2026-01-03
-- Purpose: Initialize configuration keys for RAGFlow integration (Knowledge Base QA)

-- 1. Add the NEW key 'ragflow_dataset_ids' with category='metadata'
INSERT IGNORE INTO system_configs (`key`, `value`, `description`, `category`, `is_secret`, `created_at`, `updated_at`)
VALUES (
    'ragflow_dataset_ids', 
    '', 
    'Comma-separated list of RAGFlow Dataset IDs to query against (e.g., "ds_123,ds_456").', 
    'metadata',
    FALSE, 
    NOW(), 
    NOW()
);

-- 2. Ensure ALL RAG keys are correctly categorized as 'metadata'
-- (This repairs any keys that might have been added to the wrong category)
UPDATE system_configs 
SET category = 'metadata' 
WHERE `key` IN ('metadata_provider', 'ragflow_api_url', 'ragflow_api_key', 'ragflow_dataset_ids');

