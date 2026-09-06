-- V60: Create APScheduler persistent job store table
-- Created at: 2026-05-28

CREATE TABLE IF NOT EXISTS `ai_agent_scheduler_jobs` (
  `id` varchar(191) NOT NULL,
  `next_run_time` double DEFAULT NULL,
  `job_state` blob NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_ai_agent_scheduler_jobs_next_run_time` (`next_run_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
