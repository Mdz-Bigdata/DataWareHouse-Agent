import json
import argparse
import asyncio

from app.services.ai.openclaw_client import build_openclaw_auth_system_content


def extract_auth_context(system_content: str) -> dict:
    start = system_content.index("<AUTH_CONTEXT>\n") + len("<AUTH_CONTEXT>\n")
    end = system_content.index("\n</AUTH_CONTEXT>")
    raw = system_content[start:end].strip()
    return json.loads(raw)


async def _try_real_permission_service(user_id: int, user_name: str) -> None:
    """
    Optional real-db check:
    - Resolve datasets via PermissionService.get_accessible_ragflow_meta_datasets(user_id)
    - Build AUTH_CONTEXT with these datasets and print.
    """
    try:
        from app.core.orm import AsyncSessionLocal
        from app.services.permission_service import PermissionService
    except Exception as e:
        print(f"\n⚠️ 无法导入 DB 相关模块，跳过真实权限验证：{e}")
        return

    try:
        async with AsyncSessionLocal() as db:
            datasets = await PermissionService(db).get_accessible_ragflow_meta_datasets(user_id)
    except Exception as e:
        print(f"\n⚠️ 连接 DB 或查询失败，跳过真实权限验证：{e}")
        return

    print("\n✅ Real PermissionService datasets:")
    print(json.dumps(datasets, ensure_ascii=False, indent=2))

    msg = build_openclaw_auth_system_content(
        user_info={"user_name": user_name},
        user=None,
        datasets=datasets,
    )
    ctx = extract_auth_context(msg)
    print("\n✅ Real AUTH_CONTEXT JSON:")
    print(json.dumps(ctx, ensure_ascii=False, indent=2))

    # Dispose engine to avoid aiomysql connection __del__ after loop closed
    try:
        from app.core.orm import engine
        await engine.dispose()
    except Exception:
        pass


async def _main_async() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, default=None, help="可选：用于真实 DB 验证 PermissionService")
    parser.add_argument("--user-name", type=str, default="chenxiaolong", help="用于构建 AUTH_CONTEXT 的用户名")
    args = parser.parse_args()

    # Case 1: only username provided, datasets empty -> []
    msg1 = build_openclaw_auth_system_content(
        user_info=None,
        user=args.user_name,
        datasets=None,
    )
    ctx1 = extract_auth_context(msg1)
    assert ctx1 == {
        "userName": args.user_name,
        "realName": args.user_name,
        "channel": "openai-user",
        "role": "普通用户",
        "datasets": [],
    }
    print("✅ Case1 AUTH_CONTEXT JSON:")
    print(json.dumps(ctx1, ensure_ascii=False, indent=2))

    # Case 2: datasets non-empty
    datasets = [
        {
            "ragflow_meta_id": "kb_1",
            "ragflow_meta_name": "示例库",
            "ragflow_meta_desc": "描述A",
        },
        {
            "ragflow_meta_id": "kb_2",
            "ragflow_meta_name": "库2",
            "ragflow_meta_desc": "",
        },
    ]
    msg2 = build_openclaw_auth_system_content(
        user_info={"user_name": "bob", "real_name": "鲍勃"},
        user=None,
        datasets=datasets,
    )
    ctx2 = extract_auth_context(msg2)
    assert ctx2["userName"] == "bob"
    assert ctx2["realName"] == "鲍勃"
    assert ctx2["channel"] == "openai-user"
    assert ctx2["role"] == "普通用户"
    assert ctx2["datasets"] == datasets
    print("\n✅ Case2 AUTH_CONTEXT JSON:")
    print(json.dumps(ctx2, ensure_ascii=False, indent=2))

    if args.user_id is not None:
        await _try_real_permission_service(args.user_id, args.user_name)


if __name__ == "__main__":
    asyncio.run(_main_async())

