import asyncio
from app.core.orm import engine
from sqlalchemy import text

async def check_user_perms(username):
    async with engine.connect() as conn:
        # 1. Find user
        res = await conn.execute(text("SELECT id, user_name, role, status FROM ai_agent_users WHERE user_name = :u"), {"u": username})
        user = res.fetchone()
        if not user:
            print(f"User {username} not found.")
            return
        
        user_id = user[0]
        print(f"--- User Info ---")
        print(f"ID: {user_id}, Name: {user[1]}, Global Role: {user[2]}, Status: {user[3]}")

        # 2. Find Roles via relations
        res = await conn.execute(text("""
            SELECT r.name, r.code 
            FROM ai_agent_roles r
            JOIN ai_agent_user_role_relations ur ON r.id = ur.role_id
            WHERE ur.user_id = :uid
        """), {"uid": user_id})
        roles = res.fetchall()
        print(f"\n--- Assigned Roles ---")
        for r in roles:
            print(f"- {r[0]} ({r[1]})")

        # 3. Find Resource Permissions
        res = await conn.execute(text("""
            SELECT resource_type, resource_id, enabled
            FROM ai_agent_resource_permissions
            WHERE user_id = :uid OR role_id IN (
                SELECT role_id FROM ai_agent_user_role_relations WHERE user_id = :uid
            )
        """), {"uid": user_id})
        perms = res.fetchall()
        print(f"\n--- Effective Resource Permissions (Direct + Role) ---")
        for p in perms:
            print(f"- Type: {p[0]}, ID: {p[1]}, Enabled: {p[2]}")

if __name__ == "__main__":
    asyncio.run(check_user_perms("chenxiaolong"))
