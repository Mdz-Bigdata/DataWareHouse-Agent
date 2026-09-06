ALTER TABLE meta_columns ADD COLUMN synonyms JSON COMMENT '同义词列表 (JSON Array)' AFTER enums;
