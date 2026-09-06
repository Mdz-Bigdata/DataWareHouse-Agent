from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_users_list_api_requires_menu_permission():
    management = _read("app/api/portal/endpoints/management.py")
    assert 'require_permission("menu", "menu:system:users")' in management
    assert "async def list_users" in management


def test_users_page_gates_actions_by_element_permissions():
    users_vue = _read("frontend/src/views/Users.vue")
    for token in (
        'hasPermission("element:user:edit")',
        'hasPermission("element:user:view_key")',
        'hasPermission("element:user:reset_key")',
        'hasPermission("element:user:delete")',
        "v-if=\"canEditUser\"",
        "v-if=\"canViewUserKey\"",
        "v-if=\"hasAnyRowAction\"",
    ):
        assert token in users_vue


def test_use_user_resolves_menu_permissions():
    use_user = _read("frontend/src/composables/useUser.ts")
    assert "perm.startsWith('menu:')" in use_user
    assert "menus.includes(perm)" in use_user


def test_edit_user_hides_save_on_quota_tab_with_hint():
    users_vue = _read("frontend/src/views/Users.vue")
    assert "activeTab !== 'quota'" in users_vue
    assert "额度请在上方点击「保存额度」" in users_vue
