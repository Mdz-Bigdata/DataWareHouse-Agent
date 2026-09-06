import json
from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import ragflow


@pytest.mark.asyncio
async def test_require_dataset_access_rejects_invisible_dataset(monkeypatch):
    class FakePermissionService:
        def __init__(self, db):
            pass

        async def get_user_permissions(self, user_id):
            return SimpleNamespace(
                permissions=SimpleNamespace(datasets=["allowed-ds"])
            )

        async def get_knowledge_base_access(self, user_id, user_name=None):
            return {
                "is_admin": False,
                "accessible_ids": {"allowed-ds"},
                "writable_ids": {"allowed-ds"},
            }

    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.require_dataset_access(
            {"role": "user", "user_id": "1"},
            db=None,
            dataset_ids=["denied-ds"],
        )

    assert exc.value.status_code == 403
    assert "denied-ds" in exc.value.detail


@pytest.mark.asyncio
async def test_require_dataset_access_allows_admin(monkeypatch):
    class FakePermissionService:
        def __init__(self, db):
            pass

        async def get_knowledge_base_access(self, user_id, user_name=None):
            return {
                "is_admin": True,
                "accessible_ids": None,
                "writable_ids": None,
            }

    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)

    await ragflow.require_dataset_access(
        {"role": "admin", "user_id": "1"},
        db=None,
        dataset_ids=["any-ds"],
    )


@pytest.mark.asyncio
async def test_require_element_permission_allows_non_admin_with_permission(monkeypatch):
    class FakePermissionService:
        def __init__(self, db):
            pass

        async def check_permission(self, user_id, resource_type, resource_id):
            return (
                user_id == 2
                and resource_type == "element"
                and resource_id == "element:knowledge:create"
            )

    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)

    await ragflow.require_element_permission(
        {"role": "user", "user_id": "2"},
        db=None,
        permission_id="element:knowledge:create",
    )


@pytest.mark.asyncio
async def test_require_element_permission_rejects_non_admin_without_permission(monkeypatch):
    class FakePermissionService:
        def __init__(self, db):
            pass

        async def check_permission(self, user_id, resource_type, resource_id):
            return False

    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.require_element_permission(
            {"role": "user", "user_id": "2"},
            db=None,
            permission_id="element:knowledge:create",
        )

    assert exc.value.status_code == 403
    assert "element:knowledge:create" in exc.value.detail


@pytest.mark.asyncio
async def test_get_ragflow_config_summary_does_not_expose_api_key(monkeypatch):
    async def fake_get(key, *args, **kwargs):
        return {
            "knowledge_ragflow_api_url": "http://ragflow.example/",
            "knowledge_ragflow_api_key": "secret-api-key",
            "metadata_provider": "ragflow",
        }.get(key, kwargs.get("default"))

    monkeypatch.setattr(ragflow.ConfigService, "get", fake_get)

    result = await ragflow.get_ragflow_config_summary()

    assert result["code"] == 0
    assert result["data"] == {
        "api_url": "http://ragflow.example/",
        "api_key_configured": True,
        "configured": True,
        "knowledge_base_enabled": True,
        "metadata_provider": "ragflow",
    }
    assert "secret-api-key" not in str(result)


@pytest.mark.asyncio
async def test_list_datasets_rejects_override_for_non_admin(monkeypatch):
    class FakePermissionService:
        def __init__(self, db):
            pass

        async def get_knowledge_base_access(self, user_id, user_name=None):
            return {
                "is_admin": False,
                "accessible_ids": {"ds-1"},
                "writable_ids": set(),
            }

    class FakeRagFlowClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("RagFlowClient should not be created for rejected override")

    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.list_ragflow_datasets(
            override_url="http://127.0.0.1:8080",
            override_key="secret",
            user={"role": "user", "user_id": "2", "user_name": "tester"},
            db=None,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_dataset_permissions_requires_write_access(monkeypatch):
    calls = {}

    async def fake_require_dataset_write_access(user, db, dataset_ids):
        calls["dataset_ids"] = dataset_ids
        raise ragflow.HTTPException(status_code=403, detail="blocked")

    monkeypatch.setattr(ragflow, "require_dataset_write_access", fake_require_dataset_write_access)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.get_dataset_permissions(
            dataset_id="denied-ds",
            user={"role": "user", "user_id": "2"},
            db=None,
        )

    assert exc.value.status_code == 403
    assert calls["dataset_ids"] == ["denied-ds"]


@pytest.mark.asyncio
async def test_dataset_portal_recommendations_requires_dataset_access(monkeypatch):
    calls = {}

    async def fake_require_dataset_access(user, db, dataset_ids):
        calls["dataset_ids"] = dataset_ids
        raise ragflow.HTTPException(status_code=403, detail="blocked")

    class FakeRagFlowClient:
        async def list_documents(self, *args, **kwargs):
            calls["client_called"] = True
            return []

    monkeypatch.setattr(ragflow, "require_dataset_access", fake_require_dataset_access)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.get_dataset_portal_recommendations(
            dataset_id="denied-ds",
            user={"role": "user", "user_id": "2"},
            db=None,
        )

    assert exc.value.status_code == 403
    assert calls["dataset_ids"] == ["denied-ds"]
    assert "client_called" not in calls


@pytest.mark.asyncio
async def test_delete_dataset_invalidates_permission_caches_for_grantees(monkeypatch):
    calls = {}

    class FakeDB:
        async def execute(self, stmt):
            calls["delete_executed"] = True

    class FakePermissionService:
        def __init__(self, db):
            pass

        async def invalidate_cached_permissions_for_users(self, user_ids):
            calls["invalidated_user_ids"] = sorted(user_ids)

    class FakeRagFlowClient:
        def __init__(self, *args, **kwargs):
            pass

        async def list_datasets(self, page=1, page_size=500):
            return [{"id": "ds-1"}]

        async def delete_datasets(self, ids):
            calls["deleted_ids"] = ids

    class FakeMetadataService:
        def __init__(self, db):
            pass

        async def mark_deleted(self, ids, user_name=None):
            calls["marked_deleted"] = ids

    async def fake_require_element_permission(user, db, permission_id):
        calls["permission_id"] = permission_id

    async def fake_require_dataset_write_access(user, db, dataset_ids):
        calls["write_checked"] = dataset_ids

    async def fake_collect_affected_users(db, dataset_ids):
        calls["collected_dataset_ids"] = dataset_ids
        return {2, 3}

    async def fake_record_knowledge_audit(*args, **kwargs):
        calls["audit_status"] = kwargs.get("status_code")

    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)
    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetadataService)
    monkeypatch.setattr(ragflow, "require_element_permission", fake_require_element_permission)
    monkeypatch.setattr(ragflow, "require_dataset_write_access", fake_require_dataset_write_access)
    monkeypatch.setattr(ragflow, "_collect_affected_dataset_permission_user_ids", fake_collect_affected_users, raising=False)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_record_knowledge_audit)

    request = SimpleNamespace(
        state=SimpleNamespace(trace_id="trace-1"),
        method="DELETE",
        client=SimpleNamespace(host="127.0.0.1"),
    )

    result = await ragflow.delete_ragflow_datasets(
        payload=ragflow.DeleteDatasetsRequest(ids=["ds-1"]),
        request=request,
        user={"user_name": "admin", "role": "admin", "user_id": "1"},
        db=FakeDB(),
    )

    assert result["code"] == 0
    assert calls["collected_dataset_ids"] == ["ds-1"]
    assert calls["invalidated_user_ids"] == [2, 3]
    assert calls["deleted_ids"] == ["ds-1"]


@pytest.mark.asyncio
async def test_document_file_requires_dataset_access(monkeypatch):
    calls = {}

    async def fake_require_dataset_access(user, db, dataset_ids):
        calls["dataset_ids"] = dataset_ids
        raise ragflow.HTTPException(status_code=403, detail="blocked")

    class FakeRagFlowClient:
        async def download_document(self, *args, **kwargs):
            calls["client_called"] = True
            return b"pdf", "test.pdf", "application/pdf"

    monkeypatch.setattr(ragflow, "require_dataset_access", fake_require_dataset_access)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.get_ragflow_dataset_document_file(
            dataset_id="denied-ds",
            document_id="doc-1",
            user={"role": "user", "user_id": "2"},
            db=None,
        )

    assert exc.value.status_code == 403
    assert calls["dataset_ids"] == ["denied-ds"]
    assert "client_called" not in calls


@pytest.mark.asyncio
async def test_document_file_passes_dataset_id_to_ragflow_client(monkeypatch):
    calls = {}

    async def fake_require_dataset_access(user, db, dataset_ids):
        calls["dataset_ids"] = dataset_ids

    class FakeRagFlowClient:
        def __init__(self, *args, **kwargs):
            pass

        async def download_document(self, document_id, *, dataset_id=None):
            calls["document_id"] = document_id
            calls["dataset_id"] = dataset_id
            return b"pdf", "test.pdf", "application/pdf"

    monkeypatch.setattr(ragflow, "require_dataset_access", fake_require_dataset_access)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)

    response = await ragflow.get_ragflow_dataset_document_file(
        dataset_id="ds-allowed",
        document_id="doc-1",
        user={"role": "user", "user_id": "2"},
        db=None,
    )

    assert calls["dataset_ids"] == ["ds-allowed"]
    assert calls["document_id"] == "doc-1"
    assert calls["dataset_id"] == "ds-allowed"
    assert response.body == b"pdf"

    calls = {}

    class FakeRagFlowClient:
        def __init__(self, *args, **kwargs):
            pass

        async def list_datasets(self, page=1, page_size=500):
            calls["page_size"] = page_size
            return [{"id": "ds-1", "name": "知识库", "description": "描述"}]

    class FakeMetadataService:
        def __init__(self, db):
            pass

        async def sync_from_ragflow_datasets(self, datasets, user_name=None):
            calls["datasets"] = datasets
            calls["user_name"] = user_name
            return {"created": 1, "updated": 0, "skipped": 0}

    async def fake_require_element_permission(user, db, permission_id):
        calls["permission_id"] = permission_id

    async def fake_record_knowledge_audit(*args, **kwargs):
        calls["audit_payload"] = kwargs.get("payload")
        calls["audit_status"] = kwargs.get("status_code")

    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)
    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetadataService)
    monkeypatch.setattr(ragflow, "require_element_permission", fake_require_element_permission)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_record_knowledge_audit)

    request = SimpleNamespace(
        state=SimpleNamespace(trace_id="trace-1"),
        method="POST",
        client=SimpleNamespace(host="127.0.0.1"),
    )

    result = await ragflow.sync_ragflow_datasets(
        request=request,
        user={"user_name": "tester", "role": "user", "user_id": "2"},
        db=None,
    )

    assert result["code"] == 0
    assert result["data"] == {"created": 1, "updated": 0, "skipped": 0}
    assert calls["page_size"] == 500
    assert calls["datasets"][0]["id"] == "ds-1"
    assert calls["user_name"] == "tester"
    assert calls["permission_id"] == "element:knowledge:edit"
    assert calls["audit_status"] == 200
    assert calls["audit_payload"]["sync_result"]["created"] == 1


@pytest.mark.asyncio
async def test_record_knowledge_audit_masks_api_key(monkeypatch):
    captured = {}

    async def fake_log_request_data(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(ragflow.AuditService, "log_request_data", fake_log_request_data)

    request = SimpleNamespace(
        state=SimpleNamespace(trace_id="trace-1"),
        method="POST",
        client=SimpleNamespace(host="127.0.0.1"),
    )
    await ragflow.record_knowledge_audit(
        request=request,
        user={"user_name": "tester"},
        action="retrieval-test",
        status_code=200,
        payload={"dataset_ids": ["ds-1"], "api_key": "secret"},
    )

    assert captured["trace_id"] == "trace-1"
    assert captured["user_name"] == "tester"
    assert captured["endpoint"] == "/api/portal/ragflow/retrieval-test"
    request_params = json.loads(captured["request_params"])
    assert request_params == {"dataset_ids": ["ds-1"]}


@pytest.mark.asyncio
async def test_add_dataset_permissions_success(monkeypatch):
    calls = {}
    
    async def fake_require_write(user, db, ids):
        calls["require_write_ids"] = ids
        
    class FakePermissionService:
        def __init__(self, db):
            pass
        async def invalidate_cached_permissions_for_users(self, user_ids):
            calls["invalidated_user_ids"] = user_ids

    class FakeSession:
        def __init__(self):
            self.added = []
            
        def add(self, model):
            self.added.append(model)
            
        async def execute(self, stmt):
            class FakeResult:
                def scalar_one_or_none(self):
                    return None
            return FakeResult()
            
        async def flush(self):
            calls["flushed"] = True

    monkeypatch.setattr(ragflow, "require_dataset_write_access", fake_require_write)
    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)

    payload = ragflow.AddDatasetPermissionsRequest(target_type="user", target_ids=[123])
    db_session = FakeSession()

    res = await ragflow.add_dataset_permissions(
        dataset_id="ds-1",
        payload=payload,
        db=db_session,
        user={"user_id": 1, "role": "admin"}
    )
    
    assert res["code"] == 0
    assert calls["require_write_ids"] == ["ds-1"]
    assert calls["flushed"] is True
    assert calls["invalidated_user_ids"] == {123}
    assert len(db_session.added) == 1
    assert db_session.added[0].user_id == 123
    assert db_session.added[0].resource_type == "dataset"
    assert db_session.added[0].resource_id == "ds-1"


@pytest.mark.asyncio
async def test_delete_dataset_permission_success(monkeypatch):
    calls = {}
    
    async def fake_require_write(user, db, ids):
        calls["require_write_ids"] = ids
        
    class FakePermissionService:
        def __init__(self, db):
            pass
        async def invalidate_cached_permissions_for_users(self, user_ids):
            calls["invalidated_user_ids"] = user_ids

    class FakeSession:
        def __init__(self):
            self.executed = []
            
        async def execute(self, stmt):
            self.executed.append(stmt)
            class FakeResult:
                def scalars(self):
                    class FakeScalars:
                        def all(self):
                            return []
                    return FakeScalars()
            return FakeResult()
            
        async def flush(self):
            calls["flushed"] = True

    monkeypatch.setattr(ragflow, "require_dataset_write_access", fake_require_write)
    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)

    payload = ragflow.DeleteDatasetPermissionRequest(target_type="user", target_id=123)
    db_session = FakeSession()

    res = await ragflow.delete_dataset_permission(
        dataset_id="ds-1",
        payload=payload,
        db=db_session,
        user={"user_id": 1, "role": "admin"}
    )
    
    assert res["code"] == 0
    assert calls["require_write_ids"] == ["ds-1"]
    assert calls["flushed"] is True
    assert calls["invalidated_user_ids"] == {123}
    assert len(db_session.executed) == 1


@pytest.mark.asyncio
async def test_get_dataset_portal_recommendations_with_custom_questions(monkeypatch):
    class FakeSession:
        async def execute(self, stmt):
            class FakeResult:
                def scalar_one_or_none(self):
                    return SimpleNamespace(
                        extra_config={
                            "custom_questions": [
                                {"label": "问题1", "query": "完整提问1"},
                                {"label": "问题2", "query": "完整提问2"},
                                {"label": "问题3", "query": "完整提问3"}
                            ]
                        }
                    )
            return FakeResult()

    async def fake_require_access(user, db, ids):
        pass

    class FakeRagFlowClient:
        def __init__(self, *args, **kwargs):
            pass
        async def list_documents(self, dataset_id, page_size):
            return [{"id": "doc-1", "name": "测试文档.pdf"}]

    class FakeLLMClient:
        async def generate_text(self, messages):
            return '["生成提问1", "生成提问2", "生成提问3"]'

    async def fake_get_llm(streaming, temperature):
        class FakeLLM:
            pass
        return FakeLLM()

    def fake_chat_client(llm):
        return FakeLLMClient()

    monkeypatch.setattr(ragflow, "require_dataset_access", fake_require_access)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)
    monkeypatch.setattr("app.core.llm.client.get_llm_async", fake_get_llm)
    monkeypatch.setattr("app.services.ai.runtime.agentscope.chat.chat_client_from_handle", fake_chat_client)

    res = await ragflow.get_dataset_portal_recommendations(
        dataset_id="ds-1",
        user={"user_id": 1, "role": "user"},
        db=FakeSession()
    )

    assert res["code"] == 0
    data = res["data"]
    custom_qs = data["custom_questions"]
    qs = data["questions"]
    assert len(custom_qs) == 3
    assert custom_qs[0]["label"] == "问题1"
    assert custom_qs[0]["query"] == "完整提问1"
    assert len(qs) == 3
    assert qs[0]["label"] == "生成提问1"


@pytest.mark.asyncio
async def test_get_dataset_portal_recommendations_hybrid(monkeypatch):
    class FakeSession:
        async def execute(self, stmt):
            class FakeResult:
                def scalar_one_or_none(self):
                    return SimpleNamespace(
                        extra_config={
                            "custom_questions": [
                                {"label": "固定问题", "query": "固定提问内容"}
                            ]
                        }
                    )
            return FakeResult()

    async def fake_require_access(user, db, ids):
        pass

    class FakeRagFlowClient:
        def __init__(self, *args, **kwargs):
            pass
        async def list_documents(self, dataset_id, page_size):
            return [{"id": "doc-1", "name": "测试文档.pdf"}]

    class FakeLLMClient:
        async def generate_text(self, messages):
            return '["生成提问1", "生成提问2", "生成提问3"]'

    async def fake_get_llm(streaming, temperature):
        class FakeLLM:
            pass
        return FakeLLM()

    def fake_chat_client(llm):
        return FakeLLMClient()

    monkeypatch.setattr(ragflow, "require_dataset_access", fake_require_access)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)
    monkeypatch.setattr("app.core.llm.client.get_llm_async", fake_get_llm)
    monkeypatch.setattr("app.services.ai.runtime.agentscope.chat.chat_client_from_handle", fake_chat_client)

    res = await ragflow.get_dataset_portal_recommendations(
        dataset_id="ds-1",
        user={"user_id": 1, "role": "user"},
        db=FakeSession()
    )

    assert res["code"] == 0
    data = res["data"]
    custom_qs = data["custom_questions"]
    qs = data["questions"]
    assert len(custom_qs) == 1
    assert custom_qs[0]["label"] == "固定问题"
    assert custom_qs[0]["query"] == "固定提问内容"
    assert len(qs) == 3
    assert qs[0]["label"] == "生成提问1"
    assert qs[0]["query"] == "生成提问1"
