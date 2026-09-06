# 测试常见问题与解决方案 (FAQ)

本文档汇总了在进行异步代码（FastAPI + SQLAlchemy + pytest-asyncio）测试时遇到的常见问题及其标准解决方案。

## 1. `RuntimeError: Event loop is closed`

### **现象**
运行测试时（尤其是涉及 DB 操作或后台任务的测试），报错提示 Event loop 已关闭。

### **原因**
`pytest-asyncio`（特别是高版本）默认对 Event Loop 的管理较为严格，可能会在测试函数结束时立即关闭 Loop。
而我们的应用中存在以下情况：
1.  **全局资源**：数据库连接池 (`engine`) 或 Redis 连接是在 Session 级别复用的，如果 Loop 被关闭，这些资源清理时会报错。
2.  **后台任务**：中间件（如 `AccessLogMiddleware`）可能会使用 `StreamingResponse` 或后台任务在 Response 返回后继续写入日志。如果测试结束瞬间 Loop 关闭，这些悬挂的任务就会跑在已关闭的 Loop 上。

### **错误尝试 (Anti-Patterns)**
- ❌ **侵入式修改业务代码**：在 `AccessLogMiddleware` 中判断 `if settings.MODE == "TESTING": await log_sync()`。这虽然能跑通，但掩盖了真实环境的并发行为，且污染了生产代码。
- ❌ **手动管理 Loop 但未指定 Scope**：简单地 `new_event_loop()` 而不匹配 Fixture 的作用域。

### **正确解决方案**
在 `tests/conftest.py` 中显式定义一个 **Session 级别** 的 Event Loop，确保它覆盖所有 Fixture 和后台任务的生命周期。

```python
# tests/conftest.py

import pytest
import asyncio

@pytest.fixture(scope="session")
def event_loop():
    """
    创建一个 Session 级别的 Event Loop 供整个测试会话使用。
    这能避免 pytest-asyncio 频繁关闭 Loop 导致的 'Event loop is closed' 问题，
    特别是对于涉及 Database Connection Pool 或 Background Tasks 的场景。
    """
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
```

## 2. `sqlalchemy.exc.MissingGreenlet`

### **现象**
```
sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called; can't call await_only() here. 
Was IO attempted in an unexpected place?
```

### **原因**
在使用 `AsyncSession` 时，如果不小心混用了同步的属性访问（Lazy Loading）或者在错误的上下文（如非 Async 上下文）中使用了需要 `await` 的 DB 操作。
在本项目的特定场景下，更常见的原因是：**错误地混用了 `aiomysql` 原生 Cursor 和 SQLAlchemy 的连接对象**。

例如：
```python
# ❌ 错误做法：在 SQLAlchemy 的 session/connection 上拿生 cursor
async with database.get_db_connection() as conn:
    async with conn.cursor() as cursor:  # 这种混合写法容易破坏 SQLAlchemy 的 Greenlet 上下文
        ...
```

### **解决方案**
统一使用 SQLAlchemy 的 `AsyncSession` 进行交互，并使用 `text()` 构建 SQL。

```python
# ✅ 正确做法
from app.core.orm import AsyncSessionLocal
from sqlalchemy import text

async with AsyncSessionLocal() as session:
    result = await session.execute(text("SELECT * FROM users WHERE id = :id"), {"id": 1})
    rows = result.fetchall()
    await session.commit()
```

## 3. `IntegrityError` (Duplicate Entry) 在测试中频发

### **现象**
测试 A 运行通过，测试 B 报错 `Duplicate entry 'xxx' for key 'PRIMARY'`。

### **原因**
测试数据的初始化（Seeding）逻辑过于简单（直接 Insert），没有考虑到测试之间的状态污染或重运行场景。虽然 `init_infrastructure` 可能会清理 DB，但在并发或特定 Scope 下，数据可能残留。

### **解决方案**
在 `conftest.py` 的 `seed_data` fixture 中，使用 **Check-then-Insert** 或 **Upsert** 逻辑，而不是暴力 Delete + Insert。

```python
# tests/conftest.py
@pytest.fixture
async def seed_data():
    async with AsyncSessionLocal() as session:
        # 先查询是否存在
        res = await session.execute(select(User).where(User.user_name == "test_user"))
        user = res.scalar_one_or_none()
        
        if not user:
            # 不存在则插入
            session.add(User(user_name="test_user", ...))
        else:
            # 存在则更新关键字段（确保状态复位）
            user.api_key_hash = ...
            
        await session.commit()
```

## 4. 测试成功率总结

| 类别 | 关键点 | 状态 |
| :--- | :--- | :--- |
| **异步机制** | 统一使用 Session 级 Event Loop | ✅ 已解决 |
| **DB 访问** | 全面迁移至 `AsyncSession` | ✅ 已解决 |
| **数据隔离** | Fixture 作用域调整为 `function` + 稳健 Seeding | ✅ 已解决 |

遵循以上规范，可确保测试套件 (Test Suite) 的稳定性和可维护性。
