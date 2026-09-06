import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.services.metadata_service import MetadataService
from app.models.metadata import MetaDataset, MetaTable, MetaColumn
from app.models.user import User
from app.models.permission import ResourcePermission

@pytest.mark.asyncio
async def test_table_sync_removes_stale_columns(db_session: AsyncSession):
    """
    P1 复现: 验证 save_table_metadata 是否会删除过时字段
    """
    # 1. 创建初始数据集和表
    import uuid
    uid = uuid.uuid4().hex[:6]
    dataset = MetaDataset(name=f"test_sync_ds_{uid}", display_name="Test Sync")
    db_session.add(dataset)
    await db_session.flush()

    table_data = {
        "physical_name": "test_table",
        "term": "测试表",
        "columns": [
            {"physical_name": "col1", "term": "列1", "type": "String"},
            {"physical_name": "col2", "term": "列2", "type": "String"}
        ]
    }

    # 第一次保存
    table = await MetadataService.save_table_metadata(db_session, dataset.id, table_data)
    assert len(table.columns) == 2

    # 2. 第二次保存，只保留 col1，移除 col2
    updated_table_data = {
        "physical_name": "test_table",
        "term": "测试表",
        "columns": [
            {"physical_name": "col1", "term": "列1", "type": "String"}
        ]
    }

    await MetadataService.save_table_metadata(db_session, dataset.id, updated_table_data)

    # 3. 验证结果
    # 重新从数据库读取字段数量
    stmt = select(func.count(MetaColumn.id)).where(MetaColumn.table_id == table.id)
    count = await db_session.scalar(stmt)

    # 目前的代码逻辑下，这里会失败，因为 col2 依然存在 (count 将为 2 而非 1)
    assert count == 1, f"期望只有1个字段，但实际有 {count} 个。P1 修复失败/尚未修复。"

@pytest.mark.asyncio
async def test_get_dataset_detail_permission_vulnerability(db_session: AsyncSession):
    """
    P0 复现: 验证非管理员是否能越权获取未授权的数据集详情
    """
    # 1. 创建数据集
    import uuid
    uid = uuid.uuid4().hex[:6]
    dataset = MetaDataset(name=f"secret_ds_{uid}", display_name="机密数据集")
    db_session.add(dataset)

    # 创建一个普通用户
    user = User(user_name=f"unauth_user_{uid}", role="user", status=1, api_key_hash=f"test_hash_{uid}")
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()

    # 2. 尝试获取详情
    # 修复后，传入 user_id，由于没有权限记录，应该返回 None
    ds = await MetadataService.get_dataset_by_id(db_session, dataset.id, user_id=user.id)

    assert ds is None, "未授权用户不应获取到数据集详情"

    # 3. 模拟赋予权限
    permission = ResourcePermission(
        user_id=user.id,
        resource_type="metadata",
        resource_id=str(dataset.id),
        enabled=True
    )
    db_session.add(permission)
    await db_session.flush()

    # 再次尝试获取
    ds_granted = await MetadataService.get_dataset_by_id(db_session, dataset.id, user_id=user.id)
    assert ds_granted is not None, "授权用户应该能获取到详情"
    assert ds_granted.name.startswith("secret_ds_")
