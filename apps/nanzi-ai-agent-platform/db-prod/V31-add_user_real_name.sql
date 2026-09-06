-- V31: Add real_name field to users table
ALTER TABLE ai_agent_users ADD COLUMN real_name VARCHAR(50) AFTER user_name;
