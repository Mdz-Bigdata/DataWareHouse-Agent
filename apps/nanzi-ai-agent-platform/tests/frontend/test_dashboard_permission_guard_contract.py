from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure


def test_router_falls_back_to_first_allowed_menu_instead_of_no_permission():
    source = Path("frontend/src/router/index.ts").read_text()

    assert "resolveFirstAllowedRoute" in source
    assert "MENU_HOME_CANDIDATES" in source
    assert "{ perm: 'menu:ai_chat', name: 'PersonalWorkbench' }" in source
    assert "缺目标页权限时落到第一个有权限的页面" in source
    assert "next({ name: 'Overview' }) // Fallback to overview" not in source
    assert "如果访问首页没权限，尝试重定向到第一个有权限的菜单" not in source


def test_no_permission_page_refetches_me_before_giving_up():
    source = Path("frontend/src/views/NoPermission.vue").read_text()

    assert "/api/portal/auth/me" in source
    assert "localStorage.setItem('user_info'" in source
    assert "PersonalWorkbench" in source
    assert "window.location.reload()" not in source
