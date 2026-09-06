import pytest
import os
import json
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.utils.fs_access import get_user_private_workspace_root, get_user_uploads_dir
from app.utils.fs_paths import get_data_base_dir
from app.api.v1.endpoints.fs import (
    WORKSPACE_BROWSER_PREFS_REDIS_PREFIX,
    WORKSPACE_RECENT_REDIS_PREFIX,
    WorkspaceRecentFileItem,
    _sanitize_workspace_browser_prefs,
    _sanitize_workspace_recent_files,
)


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, nx=False):
        self.store[key] = value
        return True

@pytest.mark.asyncio
async def test_list_root_directory(db_session, valid_api_key):
    """
    测试获取文件系统默认根目录列表
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/chat/fs/list",
            headers={"X-API-Key": valid_api_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert data["message"] == "success"
        assert "current_path" in data["data"]
        assert "is_root" in data["data"]
        assert data["data"]["is_root"] is True
        assert data["data"]["scope"] == "user_scoped"
        assert isinstance(data["data"]["items"], list)
        item_paths = {item["path"] for item in data["data"]["items"]}
        base = get_data_base_dir()
        # 普通用户虚拟根不再暴露公共 uploads，应至少可见 skills 或本人工作区
        assert any("skills" in p or "agent_workspaces" in p for p in item_paths) or len(item_paths) >= 1

@pytest.mark.asyncio
async def test_regular_user_cannot_list_other_workspace(db_session, valid_api_key):
    base = get_data_base_dir()
    other_dir = os.path.join(base, "agent_workspaces", "other_user__999", "conv-a")
    os.makedirs(other_dir, exist_ok=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/chat/fs/list",
            params={"path": other_dir},
            headers={"X-API-Key": valid_api_key},
        )
        assert resp.status_code == 403
        assert "越权" in resp.json()["message"]

@pytest.mark.asyncio
async def test_regular_user_cannot_preview_other_workspace_file(db_session, valid_api_key):
    base = get_data_base_dir()
    other_file = os.path.join(base, "agent_workspaces", "other_user__999", "secret.txt")
    os.makedirs(os.path.dirname(other_file), exist_ok=True)
    with open(other_file, "w", encoding="utf-8") as handle:
        handle.write("secret")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/chat/fs/preview",
            params={"path": other_file},
            headers={"X-API-Key": valid_api_key},
        )
        assert resp.status_code == 403

@pytest.mark.asyncio
async def test_list_traversal_interception(db_session, valid_api_key):
    """
    测试防路径穿越的安全防御拦截逻辑，应当返回 403
    """
    forbidden_paths = [
        "/app/data/../../../etc/passwd",
        "data/../../../etc/shadow",
        "../../",
        "data/.."
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for fpath in forbidden_paths:
            resp = await client.get(
                "/api/v1/chat/fs/list",
                params={"path": fpath},
                headers={"X-API-Key": valid_api_key}
            )
            assert resp.status_code == 403
            assert "安全越权拦截" in resp.json()["message"]

@pytest.mark.asyncio
async def test_list_nonexistent_directory(db_session, valid_api_key):
    """
    测试获取不存在的子目录，应当返回 404
    """
    base = get_data_base_dir()
    uploads = os.path.join(base, "uploads")
    os.makedirs(uploads, exist_ok=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/chat/fs/list",
            params={"path": os.path.join(uploads, "nonexistent_folder_abc")},
            headers={"X-API-Key": valid_api_key}
        )
        assert resp.status_code == 404
        assert "不存在" in resp.json()["message"]

@pytest.mark.asyncio
async def test_write_own_workspace_text_file(db_session, valid_api_key):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me_resp = await client.get(
            "/api/portal/auth/me",
            headers={"X-API-Key": valid_api_key},
        )
        assert me_resp.status_code == 200
        workspace_root_path = get_user_private_workspace_root(me_resp.json()["data"])
        assert workspace_root_path
        os.makedirs(workspace_root_path, exist_ok=True)

        list_resp = await client.get(
            "/api/v1/chat/fs/list",
            headers={"X-API-Key": valid_api_key},
        )
        assert list_resp.status_code == 200
        items = list_resp.json()["data"]["items"]
        workspace_root = next(
            (item["path"] for item in items if item.get("is_user_workspace")),
            None,
        )
        assert workspace_root, "expected user workspace root in virtual list"

        target = os.path.join(workspace_root, "conv-write-test", "editable.txt")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("before")

        write_resp = await client.put(
            "/api/v1/chat/fs/write",
            json={"path": target, "content": "after edit"},
            headers={"X-API-Key": valid_api_key},
        )
        assert write_resp.status_code == 200
        assert write_resp.json()["data"]["path"] == os.path.normpath(target)
        with open(target, encoding="utf-8") as handle:
            assert handle.read() == "after edit"


@pytest.mark.asyncio
async def test_regular_user_cannot_write_public_uploads(db_session, valid_api_key):
    base = get_data_base_dir()
    target = os.path.join(base, "uploads", "should-not-write.txt")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("public")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/chat/fs/write",
            json={"path": target, "content": "hack"},
            headers={"X-API-Key": valid_api_key},
        )
        assert resp.status_code == 403
        with open(target, encoding="utf-8") as handle:
            assert handle.read() == "public"


@pytest.mark.asyncio
async def test_regular_user_cannot_write_other_workspace_file(db_session, valid_api_key):
    base = get_data_base_dir()
    target = os.path.join(base, "agent_workspaces", "other_user__999", "secret.txt")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("secret")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/chat/fs/write",
            json={"path": target, "content": "hack"},
            headers={"X-API-Key": valid_api_key},
        )
        assert resp.status_code == 403
        with open(target, encoding="utf-8") as handle:
            assert handle.read() == "secret"


@pytest.mark.asyncio
async def test_search_from_virtual_root_for_regular_user(db_session, valid_api_key):
    """非管理员在虚拟根（data 根路径）下搜索时不应 403。"""
    base = get_data_base_dir()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me_resp = await client.get(
            "/api/portal/auth/me",
            headers={"X-API-Key": valid_api_key},
        )
        assert me_resp.status_code == 200
        workspace_root = get_user_private_workspace_root(me_resp.json()["data"])
        assert workspace_root
        os.makedirs(workspace_root, exist_ok=True)

        sample = os.path.join(workspace_root, "conv-search", "notes.md")
        os.makedirs(os.path.dirname(sample), exist_ok=True)
        with open(sample, "w", encoding="utf-8") as handle:
            handle.write("# hello")

        resp = await client.get(
            "/api/v1/chat/fs/search",
            params={"q": "*md", "path": base},
            headers={"X-API-Key": valid_api_key},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        names = {item["name"] for item in data["items"]}
        assert "notes.md" in names


@pytest.mark.asyncio
async def test_create_file_in_workspace_root(db_session, valid_api_key):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me_resp = await client.get(
            "/api/portal/auth/me",
            headers={"X-API-Key": valid_api_key},
        )
        workspace_root = get_user_private_workspace_root(me_resp.json()["data"])
        os.makedirs(workspace_root, exist_ok=True)

        resp = await client.post(
            "/api/v1/chat/fs/create-entry",
            json={"parent_path": workspace_root, "name": "root-note.md", "kind": "file", "content": "root"},
            headers={"X-API-Key": valid_api_key},
        )
        assert resp.status_code == 200
        assert os.path.isfile(os.path.join(workspace_root, "root-note.md"))


@pytest.mark.asyncio
async def test_list_user_workspace_is_writable(db_session, valid_api_key):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me_resp = await client.get(
            "/api/portal/auth/me",
            headers={"X-API-Key": valid_api_key},
        )
        workspace_root = get_user_private_workspace_root(me_resp.json()["data"])
        os.makedirs(workspace_root, exist_ok=True)

        list_resp = await client.get(
            "/api/v1/chat/fs/list",
            params={"path": workspace_root},
            headers={"X-API-Key": valid_api_key},
        )
        assert list_resp.status_code == 200
        data = list_resp.json()["data"]
        assert data["writable"] is True
        assert data["user_workspace_root"] == os.path.normpath(workspace_root)


@pytest.mark.asyncio
async def test_list_auto_creates_user_uploads_dir(db_session, valid_api_key):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me_resp = await client.get(
            "/api/portal/auth/me",
            headers={"X-API-Key": valid_api_key},
        )
        user_info = me_resp.json()["data"]
        workspace_root = get_user_private_workspace_root(user_info)
        uploads_dir = get_user_uploads_dir(user_info)
        assert workspace_root and uploads_dir
        os.makedirs(workspace_root, exist_ok=True)
        if os.path.isdir(uploads_dir):
            os.rmdir(uploads_dir)
        assert not os.path.exists(uploads_dir)

        list_resp = await client.get(
            "/api/v1/chat/fs/list",
            params={"path": uploads_dir},
            headers={"X-API-Key": valid_api_key},
        )
        assert list_resp.status_code == 200
        data = list_resp.json()["data"]
        assert os.path.isdir(uploads_dir)
        assert data["current_path"] == os.path.normpath(uploads_dir)
        assert data["writable"] is True
        assert data["items"] == []


@pytest.mark.asyncio
async def test_create_file_and_folder_in_own_workspace(db_session, valid_api_key):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me_resp = await client.get(
            "/api/portal/auth/me",
            headers={"X-API-Key": valid_api_key},
        )
        workspace_root = get_user_private_workspace_root(me_resp.json()["data"])
        parent = os.path.join(workspace_root, "ctx-menu-test")
        os.makedirs(parent, exist_ok=True)

        dir_resp = await client.post(
            "/api/v1/chat/fs/create-entry",
            json={"parent_path": parent, "name": "nested-dir", "kind": "dir"},
            headers={"X-API-Key": valid_api_key},
        )
        assert dir_resp.status_code == 200
        assert dir_resp.json()["data"]["is_dir"] is True

        file_resp = await client.post(
            "/api/v1/chat/fs/create-entry",
            json={"parent_path": parent, "name": "draft.md", "kind": "file", "content": "# hi"},
            headers={"X-API-Key": valid_api_key},
        )
        assert file_resp.status_code == 200
        created = file_resp.json()["data"]["path"]
        with open(created, encoding="utf-8") as handle:
            assert handle.read() == "# hi"


@pytest.mark.asyncio
async def test_cannot_create_in_public_skills(db_session, valid_api_key):
    base = get_data_base_dir()
    skills = os.path.join(base, "skills")
    os.makedirs(skills, exist_ok=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat/fs/create-entry",
            json={"parent_path": skills, "name": "hack.md", "kind": "file"},
            headers={"X-API-Key": valid_api_key},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_restore_and_purge_trash_entry(db_session, valid_api_key):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me_resp = await client.get(
            "/api/portal/auth/me",
            headers={"X-API-Key": valid_api_key},
        )
        workspace_root = get_user_private_workspace_root(me_resp.json()["data"])
        os.makedirs(workspace_root, exist_ok=True)
        original = os.path.join(workspace_root, "restore-me.txt")
        with open(original, "w", encoding="utf-8") as handle:
            handle.write("bye")

        delete_resp = await client.post(
            "/api/v1/chat/fs/delete-entry",
            json={"path": original},
            headers={"X-API-Key": valid_api_key},
        )
        assert delete_resp.status_code == 200
        trashed_path = delete_resp.json()["data"]["trashed_path"]
        assert not os.path.exists(original)

        restore_resp = await client.post(
            "/api/v1/chat/fs/restore-entry",
            json={"path": trashed_path},
            headers={"X-API-Key": valid_api_key},
        )
        assert restore_resp.status_code == 200
        restored_path = restore_resp.json()["data"]["path"]
        assert os.path.isfile(restored_path)
        with open(restored_path, encoding="utf-8") as handle:
            assert handle.read() == "bye"

        delete_resp2 = await client.post(
            "/api/v1/chat/fs/delete-entry",
            json={"path": restored_path},
            headers={"X-API-Key": valid_api_key},
        )
        trashed_path2 = delete_resp2.json()["data"]["trashed_path"]

        purge_resp = await client.post(
            "/api/v1/chat/fs/purge-entry",
            json={"path": trashed_path2},
            headers={"X-API-Key": valid_api_key},
        )
        assert purge_resp.status_code == 200
        assert not os.path.exists(trashed_path2)


@pytest.mark.asyncio
async def test_empty_trash_and_protect_trash_root(db_session, valid_api_key):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me_resp = await client.get(
            "/api/portal/auth/me",
            headers={"X-API-Key": valid_api_key},
        )
        workspace_root = get_user_private_workspace_root(me_resp.json()["data"])
        os.makedirs(workspace_root, exist_ok=True)
        trash_root = os.path.join(workspace_root, ".trash")
        os.makedirs(trash_root, exist_ok=True)

        for index in range(2):
            original = os.path.join(workspace_root, f"trash-item-{index}.txt")
            with open(original, "w", encoding="utf-8") as handle:
                handle.write("x")
            delete_resp = await client.post(
                "/api/v1/chat/fs/delete-entry",
                json={"path": original},
                headers={"X-API-Key": valid_api_key},
            )
            assert delete_resp.status_code == 200

        empty_resp = await client.post(
            "/api/v1/chat/fs/empty-trash",
            headers={"X-API-Key": valid_api_key},
        )
        assert empty_resp.status_code == 200
        assert empty_resp.json()["data"]["deleted_count"] == 2
        assert os.path.isdir(trash_root)
        assert os.listdir(trash_root) == []

        rename_resp = await client.post(
            "/api/v1/chat/fs/rename-entry",
            json={"path": trash_root, "new_name": "old-trash"},
            headers={"X-API-Key": valid_api_key},
        )
        assert rename_resp.status_code == 400

        delete_root_resp = await client.post(
            "/api/v1/chat/fs/delete-entry",
            json={"path": trash_root},
            headers={"X-API-Key": valid_api_key},
        )
        assert delete_root_resp.status_code == 400


@pytest.mark.asyncio
async def test_search_glob_pattern(db_session, valid_api_key):
    base = get_data_base_dir()
    uploads = os.path.join(base, "uploads")
    os.makedirs(uploads, exist_ok=True)
    sample = os.path.join(uploads, "glob-test.md")
    with open(sample, "w", encoding="utf-8") as handle:
        handle.write("x")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/chat/fs/search",
            params={"q": "*.md", "path": uploads},
            headers={"X-API-Key": valid_api_key},
        )
        assert resp.status_code == 200
        names = {item["name"] for item in resp.json()["data"]["items"]}
        assert "glob-test.md" in names
        assert "glob-test.md".replace(".md", ".txt") not in names


@pytest.mark.no_infrastructure
def test_sanitize_workspace_recent_files_filters_invalid_paths():
    base = get_data_base_dir()
    user_info = {"user_id": 42, "username": "alice", "is_admin": False}
    workspace = get_user_private_workspace_root(user_info)
    allowed_file = os.path.join(workspace, "notes.md")
    other_file = os.path.join(base, "agent_workspaces", "bob__99", "secret.txt")
    trash_file = os.path.join(workspace, ".trash", "old.md")

    items = [
        WorkspaceRecentFileItem(path=allowed_file, name="notes.md", mtime=100),
        WorkspaceRecentFileItem(path=other_file, name="secret.txt", mtime=200),
        WorkspaceRecentFileItem(path=trash_file, name="old.md", mtime=300),
        WorkspaceRecentFileItem(path=allowed_file, name="notes.md", mtime=400),
    ]
    cleaned = _sanitize_workspace_recent_files(items, user_info)
    assert len(cleaned) == 1
    assert cleaned[0].path == os.path.normpath(allowed_file)
    assert cleaned[0].mtime == 100


@pytest.mark.asyncio
async def test_workspace_recent_files_save_and_get(db_session, valid_api_key, monkeypatch):
    fake = FakeRedis()

    async def _redis():
        return fake

    monkeypatch.setattr("app.core.redis.get_redis", _redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me_resp = await client.get(
            "/api/portal/auth/me",
            headers={"X-API-Key": valid_api_key},
        )
        user_info = me_resp.json()["data"]
        workspace_root = get_user_private_workspace_root(user_info)
        os.makedirs(workspace_root, exist_ok=True)
        sample = os.path.join(workspace_root, "recent-demo.txt")
        with open(sample, "w", encoding="utf-8") as handle:
            handle.write("demo")

        empty_resp = await client.get(
            "/api/v1/chat/fs/recent-files",
            headers={"X-API-Key": valid_api_key},
        )
        assert empty_resp.status_code == 200
        assert empty_resp.json()["data"]["items"] == []

        save_resp = await client.put(
            "/api/v1/chat/fs/recent-files",
            json={
                "items": [
                    {"path": sample, "name": "recent-demo.txt", "mtime": 123.5},
                ]
            },
            headers={"X-API-Key": valid_api_key},
        )
        assert save_resp.status_code == 200
        saved_items = save_resp.json()["data"]["items"]
        assert len(saved_items) == 1
        assert saved_items[0]["name"] == "recent-demo.txt"
        assert saved_items[0]["mtime"] == 123.5

        user_id = int(user_info["user_id"])
        redis_key = f"{WORKSPACE_RECENT_REDIS_PREFIX}{user_id}"
        assert redis_key in fake.store

        get_resp = await client.get(
            "/api/v1/chat/fs/recent-files",
            headers={"X-API-Key": valid_api_key},
        )
        assert get_resp.status_code == 200
        got_items = get_resp.json()["data"]["items"]
        assert len(got_items) == 1
        assert got_items[0]["path"] == os.path.normpath(sample)


@pytest.mark.asyncio
async def test_workspace_recent_files_user_isolation(
    db_session, valid_api_key, admin_api_key, monkeypatch
):
    fake = FakeRedis()

    async def _redis():
        return fake

    monkeypatch.setattr("app.core.redis.get_redis", _redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        user_me = await client.get(
            "/api/portal/auth/me",
            headers={"X-API-Key": valid_api_key},
        )
        admin_me = await client.get(
            "/api/portal/auth/me",
            headers={"X-API-Key": admin_api_key},
        )
        user_info = user_me.json()["data"]
        admin_info = admin_me.json()["data"]
        user_workspace = get_user_private_workspace_root(user_info)
        admin_workspace = get_user_private_workspace_root(admin_info)
        os.makedirs(user_workspace, exist_ok=True)
        os.makedirs(admin_workspace, exist_ok=True)
        user_file = os.path.join(user_workspace, "user-only.txt")
        admin_file = os.path.join(admin_workspace, "admin-only.txt")
        with open(user_file, "w", encoding="utf-8") as handle:
            handle.write("user")
        with open(admin_file, "w", encoding="utf-8") as handle:
            handle.write("admin")

        await client.put(
            "/api/v1/chat/fs/recent-files",
            json={"items": [{"path": user_file, "name": "user-only.txt", "mtime": 1}]},
            headers={"X-API-Key": valid_api_key},
        )
        await client.put(
            "/api/v1/chat/fs/recent-files",
            json={"items": [{"path": admin_file, "name": "admin-only.txt", "mtime": 2}]},
            headers={"X-API-Key": admin_api_key},
        )

        user_key = f"{WORKSPACE_RECENT_REDIS_PREFIX}{int(user_info['user_id'])}"
        admin_key = f"{WORKSPACE_RECENT_REDIS_PREFIX}{int(admin_info['user_id'])}"
        assert user_key != admin_key
        assert json.loads(fake.store[user_key])["items"][0]["name"] == "user-only.txt"
        assert json.loads(fake.store[admin_key])["items"][0]["name"] == "admin-only.txt"

        user_recent = await client.get(
            "/api/v1/chat/fs/recent-files",
            headers={"X-API-Key": valid_api_key},
        )
        admin_recent = await client.get(
            "/api/v1/chat/fs/recent-files",
            headers={"X-API-Key": admin_api_key},
        )
        user_names = {item["name"] for item in user_recent.json()["data"]["items"]}
        admin_names = {item["name"] for item in admin_recent.json()["data"]["items"]}
        assert user_names == {"user-only.txt"}
        assert admin_names == {"admin-only.txt"}


@pytest.mark.asyncio
async def test_workspace_recent_files_put_filters_other_user_paths(
    db_session, valid_api_key, monkeypatch
):
    fake = FakeRedis()

    async def _redis():
        return fake

    monkeypatch.setattr("app.core.redis.get_redis", _redis)

    base = get_data_base_dir()
    other_file = os.path.join(base, "agent_workspaces", "other_user__999", "secret.txt")
    os.makedirs(os.path.dirname(other_file), exist_ok=True)
    with open(other_file, "w", encoding="utf-8") as handle:
        handle.write("secret")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/chat/fs/recent-files",
            json={"items": [{"path": other_file, "name": "secret.txt", "mtime": 9}]},
            headers={"X-API-Key": valid_api_key},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["items"] == []


@pytest.mark.no_infrastructure
def test_sanitize_workspace_browser_prefs_normalizes_invalid_values():
    prefs = _sanitize_workspace_browser_prefs({
        "include_subdirs": "yes",
        "type_filter": "NOT_A_TYPE",
    })
    assert prefs.include_subdirs is True
    assert prefs.type_filter == "all"

    prefs = _sanitize_workspace_browser_prefs({
        "include_subdirs": False,
        "type_filter": "markdown",
    })
    assert prefs.include_subdirs is False
    assert prefs.type_filter == "markdown"


@pytest.mark.asyncio
async def test_workspace_browser_prefs_save_and_get(db_session, valid_api_key, monkeypatch):
    fake = FakeRedis()

    async def _redis():
        return fake

    monkeypatch.setattr("app.core.redis.get_redis", _redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        empty_resp = await client.get(
            "/api/v1/chat/fs/browser-prefs",
            headers={"X-API-Key": valid_api_key},
        )
        assert empty_resp.status_code == 200
        assert empty_resp.json()["data"] == {
            "include_subdirs": True,
            "type_filter": "all",
        }

        save_resp = await client.put(
            "/api/v1/chat/fs/browser-prefs",
            json={"include_subdirs": False, "type_filter": "markdown"},
            headers={"X-API-Key": valid_api_key},
        )
        assert save_resp.status_code == 200
        assert save_resp.json()["data"] == {
            "include_subdirs": False,
            "type_filter": "markdown",
        }

        me_resp = await client.get(
            "/api/portal/auth/me",
            headers={"X-API-Key": valid_api_key},
        )
        user_id = int(me_resp.json()["data"]["user_id"])
        redis_key = f"{WORKSPACE_BROWSER_PREFS_REDIS_PREFIX}{user_id}"
        assert redis_key in fake.store

        get_resp = await client.get(
            "/api/v1/chat/fs/browser-prefs",
            headers={"X-API-Key": valid_api_key},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["data"] == {
            "include_subdirs": False,
            "type_filter": "markdown",
        }


@pytest.mark.asyncio
async def test_workspace_browser_prefs_user_isolation(
    db_session, valid_api_key, admin_api_key, monkeypatch
):
    fake = FakeRedis()

    async def _redis():
        return fake

    monkeypatch.setattr("app.core.redis.get_redis", _redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.put(
            "/api/v1/chat/fs/browser-prefs",
            json={"include_subdirs": False, "type_filter": "code"},
            headers={"X-API-Key": valid_api_key},
        )
        await client.put(
            "/api/v1/chat/fs/browser-prefs",
            json={"include_subdirs": True, "type_filter": "image"},
            headers={"X-API-Key": admin_api_key},
        )

        user_resp = await client.get(
            "/api/v1/chat/fs/browser-prefs",
            headers={"X-API-Key": valid_api_key},
        )
        admin_resp = await client.get(
            "/api/v1/chat/fs/browser-prefs",
            headers={"X-API-Key": admin_api_key},
        )
        assert user_resp.json()["data"]["type_filter"] == "code"
        assert admin_resp.json()["data"]["type_filter"] == "image"
