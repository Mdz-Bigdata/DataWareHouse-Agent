-- 默认管理员账号初始化脚本
-- 用户名: admin
-- API Key: 5BYfsKWhU_Cfx83cuo8E0kd4AtEhlUHDVlKwwR2kN-c
-- 注意：加密值依赖于当前系统的 ENCRYPTION_KEY

INSERT INTO ai_agent_users (user_name, role, api_key_encrypted, api_key_hash, status, remark) 
VALUES ('admin', 'admin', 'Z0FBQUFBQnBYeHZIVDBTUG5JSW9uUVlPc1ROZ3BkSjZBVXlWV3A1U3ZZek8wYnlKa1hOdm54V3FjSGNYbTZTbzVPN1pWYmdXS0JSclZVdk1BQlBRbTQ1YkZ4aFJqRGNHVE5CLWx0d293bTg2LXg3akdwQ2ZUdXRLRXZVaXBVZk5Fd1BXRDNzaVpwT1M=', '3fbf4dfdf854c61da2cbcfbd967b0d0ce50d0b8ec2b420089dfd62cd5ae95740', 1, 'Initial Admin Account');
