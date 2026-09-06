## 1. 数据库与配置变更 (Database & Configuration)

- [x] 1.1 在 `db-prod/` 目录下创建 SQL 迁移脚本，在系统配置表中增加 `memory_base_half_life` (记忆基础半衰期) 与 `memory_consolidation_threshold` (降噪相似度常规阈值) 参数配置。
- [x] 1.2 后端 `ConfigService` 增加对此两项新系统参数的加载、缓存与默认值读取接口。
- [x] 1.3 后端 `app/core/config.py` 中增加这两个配置的全局硬编码常量作为默认值（半衰期设为 7.0，阈值设为 0.82）。

## 2. 艾宾浩斯动态记忆重排 (Ebbinghaus Memory Ranking)

- [x] 2.1 修改 `app/services/ai/memory_index_service.py` 中的 `_search_summaries_knn` 方法，从 FT.SEARCH 响应中解析并提取 `last_active` 和 `reference_count` 字段。
- [x] 2.2 实现艾宾浩斯保留度计算逻辑：$R = e^{-t / S}$，其中 $t$ 为流逝天数，记忆强度 $S = S_{base} \times (1.0 + \ln(1.0 + reference\_count))$，系统参数 $S_{base}$ 默认取 7.0。
- [x] 2.3 实现后置重排逻辑：将 KNN 粗筛条目的向量余弦相似度分值 `score` 乘以保留度 $R$ 得到最终分值，并对结果列表降序重新排列。

## 3. 定时记忆降噪管道与手动接口 (Memory Consolidation Scheduler & API)

- [x] 3.1 在 `app/services/ai/memory_index_service.py` 中增加轻量级聚类算法，计算同用户记忆碎片之间的 Cosine 距离，以 DFS 强连通分量进行分组。
- [x] 3.2 编写大模型合并管道逻辑：提取聚类组碎片，构造高保真合并 Prompt 并调用全局默认大模型进行合并与压缩，写入新合并的记忆 HASH，继承并累加 `reference_count`。
- [x] 3.3 编写清理机制：在新记忆写入成功后，原子性物理删除已被归并的所有旧记忆碎片 Redis 键。
- [x] 3.4 在 `app/services/ai/scheduler_service.py` 中注册每日凌晨 3:00 运行的 Cron 任务，结合 Redis 互斥键（`SETNX`）实现分布式抢占排他锁。
- [x] 3.5 后端 `app/api/portal/endpoints/memory.py` 增加 `/consolidate` 与 `/my/consolidate` 手动触发记忆降噪 API 接口，并提供完备的防水平越权鉴权隔离。

## 4. 前端配置管理与手动触发按钮 (Frontend Configuration & Manual Trigger Button)

- [x] 4.1 在 `frontend/src/views/MemoryManagement.vue` 中新增这两个参数的字段类型映射、中文标签和 Hover 气泡解释（CONFIG_TIPS）。
- [x] 4.2 将这两个参数分配到 `retrieval` 检索与会话存储配置分组，使配置表单支持读取、展示和修改保存。
- [x] 4.3 前端在“会话摘要”标签页增加高颜值的 Emerald 风格“一键整理合并”按钮与加载状态动画，点击后调用后端手动触发 API 接口并自动刷新数据列表。

## 5. 自动化测试与验证 (Automated Testing & Verification)

- [x] 5.1 编写针对艾宾浩斯重排打分及记忆归并管道的单元测试 `tests/test_memory_ebbinghaus_consolidation.py`，验证不同时间差/引用数下的重排顺序、聚类准确性及旧碎片物理清除行为。
- [x] 5.2 按照平台开发规范，更新 `tests/CHECKLIST.md` 自动化测试清单文件。
