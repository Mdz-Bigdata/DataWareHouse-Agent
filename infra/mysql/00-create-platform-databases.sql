CREATE DATABASE IF NOT EXISTS nanzi_api_data_platform
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS nanzi_ai_agent_platform
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS listen_book
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Business/warehouse schema queried by the Listen Book audio agent
-- (AUDIO_DB_NAME). Seed data is loaded separately via
-- apps/listen-book-data-agent/tools/audio_data/sql/audio.sql.
CREATE DATABASE IF NOT EXISTS audio
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
