## ADDED Requirements

### Requirement: Backend Memory Consolidation Task
系统必须 (MUST) 在分布式调度中心注册并维护一个每日凌晨（默认为 3:00）运行的记忆降噪定时任务。该任务在执行时，必须 (MUST) 拉取当天有新对话或者活跃的用户的历史长期记忆碎片。

#### Scenario: Trigger overnight consolidation task
- **WHEN** 定时任务中心的时间到达凌晨 3:00 且触发 Cron 规则时
- **THEN** 调度器必须通过 Redis 分布式锁保障独占拉起“记忆降噪管道服务”，并开始处理有活动记录的用户记忆

### Requirement: Memory Vector Clustering
系统必须 (MUST) 对拉取的同一个用户的全部记忆碎片进行两两向量距离（Cosine Distance）计算。若相似度大于设定的系统配置参数 `memory_consolidation_threshold`（默认 0.82），系统必须 (MUST) 将这些高度相似的记忆碎片聚合到同一个合并分组中。

#### Scenario: Group similar memories for consolidation
- **WHEN** 用户有三条关于“使用 ClickHouse 检索 PUE 数据”的长期记忆，计算出两两之间的 Cosine 相似度均大于 0.82
- **THEN** 系统必须将这三条记忆碎片归入同一个待合并聚类组中

### Requirement: LLM Memory Compression and Cleanup
系统必须 (MUST) 调用大模型对每个聚类分组中的相似记忆碎片进行语义合并。大模型生成的合并偏好必须 (MUST) 是一条简短且不失真的精炼陈述（字数控制在 50 字以内）。写入新记忆时，系统必须 (MUST) 将新记忆的 `reference_count` 设为所有旧碎片引用次数之和，并物理删除原来的旧记忆碎片。

#### Scenario: Successfully compress and cleanup memory
- **WHEN** 大模型对聚合的三条旧记忆完成了语义重构，合并出“用户常用 ClickHouse 检索 PUE 数据”的新记忆后
- **THEN** 系统必须将该新记忆保存到 Redis，把 `reference_count` 设为三条旧记的累加之和，最后将三条旧记忆的 Redis HASH 键物理删除

