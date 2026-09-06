import pytest

from app.models.knowledge import KnowledgeBaseMetadata
from app.services.knowledge_base_service import KnowledgeBaseMetadataService


@pytest.mark.asyncio
async def test_merge_with_ragflow_prefers_local_metadata():
    service = KnowledgeBaseMetadataService(db=None)
    local = KnowledgeBaseMetadata(
        ragflow_dataset_id="ds-1",
        name="平台名称",
        description="平台描述",
        owner="数据团队",
        visibility="team",
        tags=["faq", "policy"],
        notes="内部备注",
        status="active",
    )

    async def fake_list_metadata(include_deleted=False):
        return [local]

    service.list_metadata = fake_list_metadata

    merged = await service.merge_with_ragflow([
        {
            "id": "ds-1",
            "name": "RAGFlow 名称",
            "description": "RAGFlow 描述",
            "doc_count": 2,
            "chunk_count": 12,
        }
    ])

    assert merged[0]["platform_name"] == "平台名称"
    assert merged[0]["platform_description"] == "平台描述"
    assert merged[0]["owner"] == "数据团队"
    assert merged[0]["tags"] == ["faq", "policy"]
    assert merged[0]["doc_count"] == 2
    assert merged[0]["is_missing_in_ragflow"] is False


@pytest.mark.asyncio
async def test_merge_with_ragflow_marks_local_record_missing():
    service = KnowledgeBaseMetadataService(db=None)
    local = KnowledgeBaseMetadata(
        ragflow_dataset_id="lost-ds",
        name="失联知识库",
        description="本地存在但 RAGFlow 不存在",
        tags=[],
        status="active",
    )

    async def fake_list_metadata(include_deleted=False):
        return [local]

    service.list_metadata = fake_list_metadata

    merged = await service.merge_with_ragflow([])

    assert merged[0]["id"] == "lost-ds"
    assert merged[0]["status"] == "missing"
    assert merged[0]["is_missing_in_ragflow"] is True


@pytest.mark.asyncio
async def test_sync_from_ragflow_creates_missing_and_preserves_local_fields():
    service = KnowledgeBaseMetadataService(db=None)
    existing = KnowledgeBaseMetadata(
        ragflow_dataset_id="ds-existing",
        name="旧名称",
        description="旧描述",
        owner="业务负责人",
        visibility="team",
        tags=["local"],
        notes="本地备注",
        extra_config={"keep": True},
        status="missing",
    )
    created_payloads = []

    async def fake_get_by_dataset_id(dataset_id):
        return existing if dataset_id == "ds-existing" else None

    async def fake_upsert_metadata(**kwargs):
        created_payloads.append(kwargs)
        return KnowledgeBaseMetadata(
            ragflow_dataset_id=kwargs["ragflow_dataset_id"],
            name=kwargs["name"],
            description=kwargs["description"],
            status=kwargs["status"],
        )

    service.get_by_dataset_id = fake_get_by_dataset_id
    service.upsert_metadata = fake_upsert_metadata

    result = await service.sync_from_ragflow_datasets(
        [
            {"id": "ds-existing", "name": "新名称", "description": "新描述"},
            {"id": "ds-new", "name": "新知识库", "description": "新知识库描述"},
            {"id": "meta-hidden", "name": "meta-system", "description": "should skip"},
        ],
        user_name="tester",
    )

    assert result == {"created": 1, "updated": 1, "skipped": 1}
    assert existing.name == "新名称"
    assert existing.description == "新描述"
    assert existing.status == "active"
    assert existing.owner == "业务负责人"
    assert existing.tags == ["local"]
    assert existing.notes == "本地备注"
    assert existing.extra_config == {"keep": True}
    assert created_payloads[0]["ragflow_dataset_id"] == "ds-new"
    assert created_payloads[0]["status"] == "active"
