import pytest


class TestSSOUserList:
    """测试 SSO 用户列表查询功能"""

    def test_get_sso_user_list(self):
        """测试从 SSO 端查询用户列表并打印结果"""
        # 导入并调用 get_all_users 方法
        from app.services.sso_user import LaplacePortalApiClient
        users = LaplacePortalApiClient.get_all_users()

        # 打印用户列表
        print("\nSSO 用户列表:")
        print("=" * 80)
        for user in users:
            print(f"用户名: {user['code']}")
            print(f"姓名: {user['name']}")
            print(f"部门: {user['department']}")
            print(f"职位: {user['position']}")
            print(f"邮箱: {user['email']}")
            print(f"手机号: {user['mobile']}")
            print(f"状态: {'部门内' if user['status'] else '外部门'}")
            print("-" * 80)
            print(f"userinfo: {user['userinfo']}") 

        print(f"\n总用户数: {len(users)}")

        # 验证至少返回一个用户
        assert len(users) >= 0